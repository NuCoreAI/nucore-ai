"""generate_plugin_scaffold (design/developers/plugin_authoring_p4_impl.md
Stage 2) -- all against real tmp_path fixtures, no mocking needed since
generation is local-disk-only and never touches the hub (installing what
gets generated here is Stage 3's separate install_generated_plugin tool).
"""

from __future__ import annotations

import json

import pytest

from unified.plugin_authoring import developer_config
from unified.plugin_authoring.evidence_ledger import EvidenceLedger
from unified.plugin_authoring.handlers import scaffold
from unified.plugin_authoring.handlers.scaffold import generate_plugin_scaffold
from unified.plugin_authoring.secret_guard import OAUTH_PLACEHOLDER

DEVELOPER_EMAIL = "dev@example.com"
DEVELOPER_NAME = "Dev Name"

# "AA:BB:CC:DD:EE:FF" -> mac6 "ddeeff" -- every placeholder below is rewritten to
# f"ddeeff_{placeholder}" by guard 4a.
FAKE_MAC_ADDRESS = "AA:BB:CC:DD:EE:FF"
FAKE_MAC6 = "ddeeff"

WIRE_PROFILE = {
    "editors": [{"id": "ED_ONOFF", "ranges": [{"uom": "25", "subset": "0,1"}]}],
    "nodedefs": [
        {
            "id": "switch",
            "name": "Switch",
            "properties": [{"id": "ST", "name": "Status", "editor": "ED_ONOFF"}],
            "cmds": {"sends": [], "accepts": [{"id": "DON"}]},
            "links": {"ctl": [], "rsp": []},
        },
        {
            "id": "controller",
            "name": "Controller",
            "properties": [],
            "cmds": {"sends": [], "accepts": []},
            "links": {"ctl": [], "rsp": []},
        },
    ],
    "linkdefs": [],
}

PROFILE_WITH_DANGLING_EDITOR = {
    "editors": [],
    "nodedefs": [
        {"id": "ND_X", "properties": [{"id": "ST", "editor": "MISSING_EDITOR"}], "cmds": {}},
        {"id": "controller", "properties": [], "cmds": {}},
    ],
    "linkdefs": [],
}

# A minimal oauth config satisfying the name/auth_endpoint/token_endpoint required-when-authorize
# guard -- reused by every authorize test that doesn't care about the oauth config's own content.
COMPLETE_OAUTH_CONFIG = {
    "name": "Acme API",
    "auth_endpoint": "https://x/auth",
    "token_endpoint": "https://x/token",
}


class _FakeNuCoreInterface:
    """Minimal stand-in for the one method generate_plugin_scaffold's guard
    4a calls -- generation never touches the hub otherwise."""

    def __init__(self, mac_address: str | None = FAKE_MAC_ADDRESS):
        self._mac_address = mac_address

    async def get_device_mac_address(self):
        return self._mac_address

    async def refresh_eisy_ai_permissions(self):
        return {"successful": True}


def _ledger_with_evidence() -> EvidenceLedger:
    ledger = EvidenceLedger()
    ledger.record_source(tier="github", url="https://github.com/example/repo", title="example/repo", license="MIT", license_ok=True)
    return ledger


def _base_args(**overrides):
    args = {
        "location": "acme_pool",
        "profile": WIRE_PROFILE,
        "server_entry": {"name": "AcmePool", "desc": "Acme pool controller"},
        "override_bodies": {},
        "readme_body": "This plugin integrates with the Acme Pool API.",
        "tests": {},
        "iteration_note": "Initial generation.",
    }
    args.update(overrides)
    return args


async def _generate(
    tmp_path,
    args,
    *,
    ledger=None,
    secret_values=None,
    get_user_id=None,
    skip_developer_config=False,
    nucore_interface=None,
):
    if not skip_developer_config:
        developer_config.write_developer_config(str(tmp_path), email=DEVELOPER_EMAIL, name=DEVELOPER_NAME)
    return await generate_plugin_scaffold(
        nucore_interface if nucore_interface is not None else _FakeNuCoreInterface(),
        args,
        ledger=ledger if ledger is not None else _ledger_with_evidence(),
        secret_values=secret_values or [],
        plugin_output_root=str(tmp_path),
        get_user_id=get_user_id,
    )


# --- guard 1: location ---


@pytest.mark.asyncio
async def test_requires_location(tmp_path):
    result = await _generate(tmp_path, _base_args(location=""))
    assert "error" in result
    assert not (tmp_path / "acme_pool").exists()


@pytest.mark.asyncio
async def test_rejects_path_traversal_location(tmp_path):
    result = await _generate(tmp_path, _base_args(location="../escape"))
    assert "error" in result


# --- guard 2: evidence ---


@pytest.mark.asyncio
async def test_refuses_without_evidence(tmp_path):
    result = await _generate(tmp_path, _base_args(), ledger=EvidenceLedger())
    assert "error" in result
    assert not (tmp_path / "acme_pool").exists()


# --- guard 3: profile validation (wire -> catalog bridge) ---


@pytest.mark.asyncio
async def test_rejects_non_dict_profile(tmp_path):
    result = await _generate(tmp_path, _base_args(profile="not-a-dict"))
    assert "error" in result


@pytest.mark.asyncio
async def test_rejects_profile_with_dangling_editor_reference(tmp_path):
    result = await _generate(tmp_path, _base_args(profile=PROFILE_WITH_DANGLING_EDITOR))
    assert "error" in result
    assert result.get("details")


@pytest.mark.asyncio
async def test_accepts_well_formed_wire_profile(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result


@pytest.mark.asyncio
async def test_result_includes_the_absolute_path_on_disk(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert result["absolute_path"] == str((tmp_path / "acme_pool").resolve())


# --- guard 4: override_bodies key allowlist / AI-tool matching ---


@pytest.mark.asyncio
async def test_unknown_override_body_key_is_rejected(tmp_path):
    result = await _generate(tmp_path, _base_args(override_bodies={"not_a_real_override": "def x(self): pass"}))
    assert "error" in result
    assert not (tmp_path / "acme_pool").exists()


@pytest.mark.asyncio
async def test_ai_tool_without_matching_helper_is_rejected(tmp_path):
    args = _base_args(
        server_entry={"name": "AcmePool", "ai_enabled": True, "aiTools": [{"name": "get_dates", "description": "x"}]},
        override_bodies={},
    )
    result = await _generate(tmp_path, args)
    assert "error" in result
    assert not (tmp_path / "acme_pool").exists()


@pytest.mark.asyncio
async def test_ai_tools_without_ai_enabled_is_rejected(tmp_path):
    args = _base_args(server_entry={"name": "AcmePool", "aiTools": [{"name": "get_dates", "description": "x"}]})
    result = await _generate(tmp_path, args)
    assert "error" in result


# --- guard 4b: controller cmds.accepts -> commands dispatch dict ---


def _profile_with_controller_accepts(accepts):
    profile = json.loads(json.dumps(WIRE_PROFILE))
    for nodedef in profile["nodedefs"]:
        if nodedef["id"] == "controller":
            nodedef["cmds"]["accepts"] = accepts
    return profile


@pytest.mark.asyncio
async def test_controller_commands_dict_is_generated_for_non_colliding_accepts_command(tmp_path):
    profile = _profile_with_controller_accepts([{"id": "DISCOVER"}, {"id": "QUERYALL"}])
    result = await _generate(tmp_path, _base_args(profile=profile))
    assert "error" not in result
    plugin_py = (tmp_path / "acme_pool" / "plugin.py").read_text()
    assert "def queryall(self, *args, **kwargs):" in plugin_py
    assert "'QUERYALL': queryall" in plugin_py


@pytest.mark.asyncio
async def test_controller_commands_dict_reuses_discover_override_on_collision(tmp_path):
    profile = _profile_with_controller_accepts([{"id": "DISCOVER"}, {"id": "QUERYALL"}])
    result = await _generate(tmp_path, _base_args(profile=profile))
    assert "error" not in result
    plugin_py = (tmp_path / "acme_pool" / "plugin.py").read_text()
    assert "'DISCOVER': discover" in plugin_py
    assert plugin_py.count("def discover(") == 1


@pytest.mark.asyncio
async def test_override_bodies_can_supply_a_custom_body_for_a_new_command_method(tmp_path):
    profile = _profile_with_controller_accepts([{"id": "QUERYALL"}])
    args = _base_args(
        profile=profile,
        override_bodies={"queryall": 'def queryall(self):\n    LOGGER.info("query all")\n'},
    )
    result = await _generate(tmp_path, args)
    assert "error" not in result
    plugin_py = (tmp_path / "acme_pool" / "plugin.py").read_text()
    assert 'LOGGER.info("query all")' in plugin_py


@pytest.mark.asyncio
async def test_commands_dict_is_placed_after_the_methods_it_references(tmp_path):
    profile = _profile_with_controller_accepts([{"id": "DISCOVER"}, {"id": "QUERYALL"}])
    result = await _generate(tmp_path, _base_args(profile=profile))
    assert "error" not in result
    plugin_py = (tmp_path / "acme_pool" / "plugin.py").read_text()

    import ast as ast_module

    tree = ast_module.parse(plugin_py)
    controller = next(n for n in ast_module.walk(tree) if isinstance(n, ast_module.ClassDef) and n.name == "Controller")
    method_linenos = {
        n.name: n.lineno for n in controller.body if isinstance(n, (ast_module.FunctionDef, ast_module.AsyncFunctionDef))
    }
    commands_stmt = next(
        stmt
        for stmt in controller.body
        if isinstance(stmt, ast_module.Assign)
        and len(stmt.targets) == 1
        and isinstance(stmt.targets[0], ast_module.Name)
        and stmt.targets[0].id == "commands"
    )
    assert commands_stmt.lineno > method_linenos["discover"]
    assert commands_stmt.lineno > method_linenos["queryall"]


@pytest.mark.asyncio
async def test_controller_invalid_command_id_is_rejected(tmp_path):
    profile = _profile_with_controller_accepts([{"id": "2-BAD"}])
    result = await _generate(tmp_path, _base_args(profile=profile))
    assert "error" in result
    assert not (tmp_path / "acme_pool").exists()


@pytest.mark.asyncio
async def test_colliding_derived_command_names_are_rejected(tmp_path):
    profile = _profile_with_controller_accepts([{"id": "QueryAll"}, {"id": "QUERYALL"}])
    result = await _generate(tmp_path, _base_args(profile=profile))
    assert "error" in result
    assert not (tmp_path / "acme_pool").exists()


# --- guard 5: ast.parse + class-membership round trip ---


@pytest.mark.asyncio
async def test_assembled_plugin_py_is_valid_python_and_defines_the_controller_class(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "plugin.py").read_text()
    import ast as ast_module

    tree = ast_module.parse(source)
    class_names = {n.name for n in ast_module.walk(tree) if isinstance(n, ast_module.ClassDef)}
    assert "Controller" in class_names


@pytest.mark.asyncio
async def test_override_body_written_at_column_zero_still_lands_inside_the_class(tmp_path):
    """Regression test: a method written as if it were a top-level function
    (no leading indentation) must still end up as an actual method of the
    Controller class after _reindent_method normalizes it -- not spliced in
    beside the class as a dead, never-called module-level function (which
    ast.parse alone would not catch, since that's still syntactically valid
    Python)."""
    args = _base_args(override_bodies={"start": 'def start(self):\n    LOGGER.info("hi")\n'})
    result = await _generate(tmp_path, args)
    assert "error" not in result

    import ast as ast_module

    source = (tmp_path / "acme_pool" / "plugin.py").read_text()
    tree = ast_module.parse(source)
    controller = next(n for n in ast_module.walk(tree) if isinstance(n, ast_module.ClassDef) and n.name == "Controller")
    method_names = {n.name for n in controller.body if isinstance(n, (ast_module.FunctionDef, ast_module.AsyncFunctionDef))}
    assert "start" in method_names
    module_level_functions = {n.name for n in tree.body if isinstance(n, (ast_module.FunctionDef, ast_module.AsyncFunctionDef))}
    assert "start" not in module_level_functions


# --- guard 6: secrets + OAuth placeholder enforcement ---


@pytest.mark.asyncio
async def test_secret_value_in_override_body_is_refused(tmp_path):
    args = _base_args(override_bodies={"start": 'def start(self):\n    self.key = "sekret123"\n'})
    result = await _generate(tmp_path, args, secret_values=["sekret123"])
    assert "error" in result
    assert not (tmp_path / "acme_pool").exists()


@pytest.mark.asyncio
async def test_secret_value_in_server_entry_desc_is_refused(tmp_path):
    args = _base_args(server_entry={"name": "AcmePool", "desc": "uses key sekret123 internally"})
    result = await _generate(tmp_path, args, secret_values=["sekret123"])
    assert "error" in result


@pytest.mark.asyncio
async def test_oauth_client_secret_defaults_to_placeholder_when_omitted(tmp_path):
    args = _base_args(
        server_entry={
            "name": "AcmePool",
            "authorize": True,
            "oauth": dict(COMPLETE_OAUTH_CONFIG),
        }
    )
    result = await _generate(tmp_path, args)
    assert "error" not in result
    entry = json.loads((tmp_path / "acme_pool" / "server_entry.json").read_text())
    oauth = json.loads(entry["oauth"])
    assert oauth["client_id"] == OAUTH_PLACEHOLDER
    assert oauth["client_secret"] == OAUTH_PLACEHOLDER


@pytest.mark.asyncio
async def test_oauth_client_secret_with_a_real_value_is_refused(tmp_path):
    args = _base_args(
        server_entry={
            "name": "AcmePool",
            "authorize": True,
            "oauth": {**COMPLETE_OAUTH_CONFIG, "client_secret": "real-secret-value"},
        }
    )
    result = await _generate(tmp_path, args)
    assert "error" in result
    assert not (tmp_path / "acme_pool").exists()


# --- guard 8: oauth.name/auth_endpoint/token_endpoint required when authorize ---


@pytest.mark.asyncio
async def test_oauth_missing_name_is_rejected(tmp_path):
    args = _base_args(
        server_entry={
            "name": "AcmePool",
            "authorize": True,
            "oauth": {"auth_endpoint": "https://x/auth", "token_endpoint": "https://x/token"},
        }
    )
    result = await _generate(tmp_path, args)
    assert "error" in result
    assert not (tmp_path / "acme_pool").exists()


@pytest.mark.asyncio
async def test_oauth_missing_auth_endpoint_is_rejected(tmp_path):
    args = _base_args(
        server_entry={
            "name": "AcmePool",
            "authorize": True,
            "oauth": {"name": "Acme API", "token_endpoint": "https://x/token"},
        }
    )
    result = await _generate(tmp_path, args)
    assert "error" in result
    assert not (tmp_path / "acme_pool").exists()


@pytest.mark.asyncio
async def test_oauth_missing_token_endpoint_is_rejected(tmp_path):
    args = _base_args(
        server_entry={
            "name": "AcmePool",
            "authorize": True,
            "oauth": {"name": "Acme API", "auth_endpoint": "https://x/auth"},
        }
    )
    result = await _generate(tmp_path, args)
    assert "error" in result
    assert not (tmp_path / "acme_pool").exists()


@pytest.mark.asyncio
async def test_oauth_omitted_entirely_when_authorize_is_rejected(tmp_path):
    args = _base_args(server_entry={"name": "AcmePool", "authorize": True})
    result = await _generate(tmp_path, args)
    assert "error" in result
    assert not (tmp_path / "acme_pool").exists()


@pytest.mark.asyncio
async def test_oauth_name_and_parameters_round_trip_into_server_entry(tmp_path):
    args = _base_args(
        server_entry={
            "name": "AcmePool",
            "authorize": True,
            "oauth": {**COMPLETE_OAUTH_CONFIG, "parameters": {"access_type": "offline"}},
        }
    )
    result = await _generate(tmp_path, args)
    assert "error" not in result
    entry = json.loads((tmp_path / "acme_pool" / "server_entry.json").read_text())
    oauth = json.loads(entry["oauth"])
    assert oauth["name"] == "Acme API"
    assert oauth["parameters"] == {"access_type": "offline"}


# --- authorize / ai_enabled wiring ---


@pytest.mark.asyncio
async def test_authorize_true_adds_oauth_subscription_and_service(tmp_path):
    args = _base_args(server_entry={"name": "AcmePool", "authorize": True, "oauth": dict(COMPLETE_OAUTH_CONFIG)})
    result = await _generate(tmp_path, args)
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "plugin.py").read_text()
    assert "self.poly.OAUTH" in source
    assert "udi_interface.OAuth(self.poly)" in source


@pytest.mark.asyncio
async def test_authorize_false_omits_oauth_subscription(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "plugin.py").read_text()
    assert "self.poly.OAUTH" not in source
    assert "udi_interface.OAuth" not in source


# --- OAuth2 token-lifecycle fix: oauthTokens forwarding, oauthHandler delegation,
# and the late-authentication start() retry path (see design/developers/
# plugin_authoring_design.md stage 8 for the three bugs this closes) ---


@pytest.mark.asyncio
async def test_custom_ns_handler_forwards_oauth_tokens_key_to_oauth_service(tmp_path):
    args = _base_args(server_entry={"name": "AcmePool", "authorize": True, "oauth": dict(COMPLETE_OAUTH_CONFIG)})
    result = await _generate(tmp_path, args)
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "plugin.py").read_text()
    handler_section = source[source.index("def __customNSHandler(self, key, data):") :]
    handler_section = handler_section[: handler_section.index("def __customDataHandler")]
    assert 'if key in ("oauth", "oauthTokens"):' in handler_section
    assert "self.oauthService.customNsHandler(key, data)" in handler_section


@pytest.mark.asyncio
async def test_oauth_handler_delegates_token_to_oauth_service(tmp_path):
    args = _base_args(server_entry={"name": "AcmePool", "authorize": True, "oauth": dict(COMPLETE_OAUTH_CONFIG)})
    result = await _generate(tmp_path, args)
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "plugin.py").read_text()
    handler_section = source[source.index("def __oauthHandler(self, token):") :]
    handler_section = handler_section[: handler_section.index("def __customDataHandler")]
    assert "self.oauthService.oauthHandler(token)" in handler_section


@pytest.mark.asyncio
async def test_oauth_handler_unblocks_start_after_late_authentication(tmp_path):
    args = _base_args(server_entry={"name": "AcmePool", "authorize": True, "oauth": dict(COMPLETE_OAUTH_CONFIG)})
    result = await _generate(tmp_path, args)
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "plugin.py").read_text()
    handler_section = source[source.index("def __oauthHandler(self, token):") :]
    handler_section = handler_section[: handler_section.index("def __customDataHandler")]
    assert "self.oauthService.getAccessToken()" in handler_section
    assert "self.configDoneAlready = True" in handler_section
    assert "self.configDone.notifyAll()" in handler_section
    assert "self.__runStartOnce()" in handler_section


@pytest.mark.asyncio
async def test_authorize_init_sets_up_start_called_guard(tmp_path):
    args = _base_args(server_entry={"name": "AcmePool", "authorize": True, "oauth": dict(COMPLETE_OAUTH_CONFIG)})
    result = await _generate(tmp_path, args)
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "plugin.py").read_text()
    assert "self.startCalled = False" in source


@pytest.mark.asyncio
async def test_run_start_once_method_exists_and_is_idempotent(tmp_path):
    args = _base_args(server_entry={"name": "AcmePool", "authorize": True, "oauth": dict(COMPLETE_OAUTH_CONFIG)})
    result = await _generate(tmp_path, args)
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "plugin.py").read_text()
    assert "def __runStartOnce(self):" in source
    run_once_section = source[source.index("def __runStartOnce(self):") :]
    assert "if self.startCalled:" in run_once_section
    assert "self.startCalled = True" in run_once_section
    assert "self.start()" in run_once_section


@pytest.mark.asyncio
async def test_start_in_authorize_plugin_delegates_through_run_start_once(tmp_path):
    args = _base_args(server_entry={"name": "AcmePool", "authorize": True, "oauth": dict(COMPLETE_OAUTH_CONFIG)})
    result = await _generate(tmp_path, args)
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "plugin.py").read_text()
    start_section = source[source.index("def __start(self):") : source.index("def __stop(self):")]
    assert "self.__runStartOnce()" in start_section
    assert "self.start()" not in start_section


# --- webhook_enabled wiring ---


@pytest.mark.asyncio
async def test_webhook_enabled_true_adds_webhook_subscription_and_start_call(tmp_path):
    args = _base_args(
        server_entry={"name": "AcmePool", "webhook_enabled": True},
        override_bodies={"handle_webhook": "def handle_webhook(self, data):\n    pass\n"},
    )
    result = await _generate(tmp_path, args)
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "plugin.py").read_text()
    assert "self.poly.subscribe(self.poly.WEBHOOK, self.handle_webhook)" in source
    assert "self.poly.webhookStart()" in source
    assert "def handle_webhook(self, data):" in source


@pytest.mark.asyncio
async def test_webhook_enabled_false_omits_webhook_subscription(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "plugin.py").read_text()
    assert "self.poly.WEBHOOK" not in source
    assert "webhookStart" not in source
    assert "handle_webhook" not in source


@pytest.mark.asyncio
async def test_webhook_enabled_without_handle_webhook_override_is_rejected(tmp_path):
    args = _base_args(server_entry={"name": "AcmePool", "webhook_enabled": True}, override_bodies={})
    result = await _generate(tmp_path, args)
    assert "error" in result
    assert not (tmp_path / "acme_pool").exists()


@pytest.mark.asyncio
async def test_handle_webhook_override_without_webhook_enabled_is_rejected(tmp_path):
    args = _base_args(override_bodies={"handle_webhook": "def handle_webhook(self, data):\n    pass\n"})
    result = await _generate(tmp_path, args)
    assert "error" in result
    assert not (tmp_path / "acme_pool").exists()


# --- custom_typed_params wiring ---

VALID_TYPED_PARAMS = [
    {"name": "host", "title": "Host", "isRequired": True},
    {"name": "port", "title": "Port", "isRequired": True, "type": "NUMBER"},
]
TYPED_DATA_HANDLER_BODY = "def customTypedDataHandler(self, custom_params):\n    self.CustomTypedData.load(custom_params)\n"


@pytest.mark.asyncio
async def test_custom_typed_params_adds_subscription_and_custom_objects(tmp_path):
    args = _base_args(
        custom_typed_params=VALID_TYPED_PARAMS,
        override_bodies={"customTypedDataHandler": TYPED_DATA_HANDLER_BODY},
    )
    result = await _generate(tmp_path, args)
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "plugin.py").read_text()
    assert "self.CustomTypedParams = udi_interface.Custom(self.poly, 'customtypedparams')" in source
    assert "self.CustomTypedData = udi_interface.Custom(self.poly, 'customtypeddata')" in source
    assert "self.poly.subscribe(self.poly.CUSTOMTYPEDDATA, self.customTypedDataHandler)" in source
    assert "'host'" in source and "'port'" in source
    assert "self.CustomTypedParams.load(" in source and ", True)" in source
    assert "def customTypedDataHandler(self, custom_params):" in source
    assert "self.CustomTypedData.load(custom_params)" in source


@pytest.mark.asyncio
async def test_custom_typed_params_omitted_when_not_given(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "plugin.py").read_text()
    assert "CustomTypedParams" not in source
    assert "CustomTypedData" not in source
    assert "CUSTOMTYPEDDATA" not in source
    assert "customTypedDataHandler" not in source


@pytest.mark.asyncio
async def test_custom_typed_params_without_handler_override_is_rejected(tmp_path):
    args = _base_args(custom_typed_params=VALID_TYPED_PARAMS, override_bodies={})
    result = await _generate(tmp_path, args)
    assert "error" in result
    assert not (tmp_path / "acme_pool").exists()


@pytest.mark.asyncio
async def test_custom_typed_data_handler_override_without_custom_typed_params_is_rejected(tmp_path):
    args = _base_args(override_bodies={"customTypedDataHandler": TYPED_DATA_HANDLER_BODY})
    result = await _generate(tmp_path, args)
    assert "error" in result
    assert not (tmp_path / "acme_pool").exists()


@pytest.mark.asyncio
async def test_custom_typed_params_does_not_require_custom_param_docs(tmp_path):
    # Unlike customParams, custom_typed_params entries are self-documenting (their own 'desc'),
    # so no custom_param_docs is required even though none was given here.
    args = _base_args(
        custom_typed_params=VALID_TYPED_PARAMS,
        override_bodies={"customTypedDataHandler": TYPED_DATA_HANDLER_BODY},
    )
    result = await _generate(tmp_path, args)
    assert "error" not in result


@pytest.mark.asyncio
async def test_custom_typed_params_rejects_non_list(tmp_path):
    result = await _generate(tmp_path, _base_args(custom_typed_params={"name": "host"}))
    assert "error" in result


@pytest.mark.asyncio
async def test_custom_typed_params_rejects_missing_name(tmp_path):
    result = await _generate(tmp_path, _base_args(custom_typed_params=[{"title": "Host"}]))
    assert "error" in result


@pytest.mark.asyncio
async def test_custom_typed_params_rejects_missing_title(tmp_path):
    result = await _generate(tmp_path, _base_args(custom_typed_params=[{"name": "host"}]))
    assert "error" in result


@pytest.mark.asyncio
async def test_custom_typed_params_rejects_bad_type(tmp_path):
    args = _base_args(custom_typed_params=[{"name": "host", "title": "Host", "type": "INTEGER"}])
    result = await _generate(tmp_path, args)
    assert "error" in result


@pytest.mark.asyncio
async def test_custom_typed_params_rejects_doubly_nested_params(tmp_path):
    args = _base_args(
        custom_typed_params=[
            {
                "name": "entries",
                "title": "Entries",
                "isList": True,
                "params": [{"name": "inner", "title": "Inner", "params": [{"name": "x", "title": "X"}]}],
            }
        ]
    )
    result = await _generate(tmp_path, args)
    assert "error" in result


# --- __start must wait for configDone before calling the LLM-authored
# start() override -- nothing is configured yet (no custom params, no
# previously-discovered nodes, no OAuth token) when the real START event
# fires; calling start() before CONFIGDONE is the real bug a live
# generated plugin (simplisafe) hit this session ---


@pytest.mark.asyncio
async def test_generated_plugin_imports_threading(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "plugin.py").read_text()
    assert "import threading" in source


@pytest.mark.asyncio
async def test_init_sets_up_config_done_synchronization(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "plugin.py").read_text()
    assert "self.configDone = threading.Condition()" in source
    assert "self.configDoneAlready = False" in source


@pytest.mark.asyncio
async def test_start_waits_for_config_done_before_calling_the_override(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "plugin.py").read_text()
    assert "def __start(self):" in source
    assert "self.configDone.wait(timeout=10)" in source
    start_section = source[source.index("def __start(self):") : source.index("def __stop(self):")]
    assert "self.start()" in start_section
    assert "timed out waiting for configDone" in start_section


@pytest.mark.asyncio
async def test_config_done_handler_notifies_unconditionally_when_not_authorize(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "plugin.py").read_text()
    handler_section = source[source.index("def __configDoneHandler(self):") :]
    handler_section = handler_section[: handler_section.index("def __addNodeDoneHandler")]
    assert "self.configDoneAlready = True" in handler_section
    assert "self.configDone.notifyAll()" in handler_section
    assert "getAccessToken" not in handler_section


@pytest.mark.asyncio
async def test_config_done_handler_gates_on_oauth_token_when_authorize(tmp_path):
    args = _base_args(server_entry={"name": "AcmePool", "authorize": True, "oauth": dict(COMPLETE_OAUTH_CONFIG)})
    result = await _generate(tmp_path, args)
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "plugin.py").read_text()
    handler_section = source[source.index("def __configDoneHandler(self):") :]
    handler_section = handler_section[: handler_section.index("def __addNodeDoneHandler")]
    assert "self.oauthService.getAccessToken()" in handler_section
    assert 'self.poly.Notices["auth"]' in handler_section
    assert "self.configDoneAlready = True" in handler_section
    assert "self.configDone.notifyAll()" in handler_section
    assert "self.__runStartOnce()" in handler_section


@pytest.mark.asyncio
async def test_custom_data_handler_takes_only_data_not_a_key(tmp_path):
    # udi_interface's own CUSTOMDATA publish (interface.py) is
    # `pub.publish(self.CUSTOMDATA, None, value)` -- no key, unlike
    # CUSTOMNS's `pub.publish_nt(self.CUSTOMNS, None, key, value)`. A
    # `(self, key, data)` subscriber would get `data` bound into `key`.
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "plugin.py").read_text()
    assert "def __customDataHandler(self, data):" in source


# --- generation_inputs.json: a faithful snapshot of this call's own
# normalized inputs, read back by regenerate_plugin_boilerplate to
# re-render the template-driven files alone with today's template code,
# without the caller re-supplying everything ---


@pytest.mark.asyncio
async def test_generation_inputs_json_is_written_with_the_normalized_inputs(tmp_path):
    args = _base_args(
        server_entry={"name": "AcmePool", "desc": "Acme pool controller"},
        override_bodies={"start": 'def start(self):\n    LOGGER.info("hi")\n'},
        custom_param_docs=None,
        controller_class="Controller",
        controller_module="plugin",
        version="1.2.3",
    )
    result = await _generate(tmp_path, args)
    assert "error" not in result

    generation_inputs = json.loads((tmp_path / "acme_pool" / "generation_inputs.json").read_text())
    rewritten_profile = json.loads(json.dumps(WIRE_PROFILE))
    for nodedef in rewritten_profile["nodedefs"]:
        if nodedef["id"] == "controller":
            nodedef["properties"].append({"id": "ST", "name": "Status", "editor": "ED_CONTROLLER_ST"})
        nodedef["id"] = f"{FAKE_MAC6}_{nodedef['id']}"
    rewritten_profile["editors"].append(
        {
            "id": "ED_CONTROLLER_ST",
            "ranges": [{"subset": "0,1", "uom": "25", "names": {"0": "Not Connected", "1": "Connected"}}],
        }
    )
    assert generation_inputs == {
        "profile": rewritten_profile,
        "server_entry": {"name": "AcmePool", "desc": "Acme pool controller"},
        "readme_body": "This plugin integrates with the Acme Pool API.",
        "override_bodies": {"start": 'def start(self):\n    LOGGER.info("hi")\n'},
        "node_classes": {},
        "tests": {},
        "custom_param_docs": None,
        "custom_typed_params": None,
        "controller_class": "Controller",
        "controller_module": "plugin",
        "version": "1.2.3",
    }


@pytest.mark.asyncio
async def test_ai_enabled_adds_customrequest_subscription_and_helper(tmp_path):
    args = _base_args(
        server_entry={"name": "AcmePool", "ai_enabled": True, "aiTools": [{"name": "get_dates", "description": "x"}]},
        override_bodies={
            "handle_custom_request": "def handle_custom_request(self, request):\n    pass\n",
            "_get_dates": "async def _get_dates(self, payload):\n    return {}\n",
        },
    )
    result = await _generate(tmp_path, args)
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "plugin.py").read_text()
    assert "self.poly.CUSTOMREQUEST" in source
    assert "def handle_custom_request" in source
    assert "def _get_dates" in source


@pytest.mark.asyncio
async def test_ai_disabled_omits_customrequest_subscription(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "plugin.py").read_text()
    assert "CUSTOMREQUEST" not in source
    assert "handle_custom_request" not in source


# --- server_entry.json shape ---


@pytest.mark.asyncio
async def test_server_entry_name_too_long_is_rejected(tmp_path):
    args = _base_args(server_entry={"name": "WayTooLongAPluginName"})
    result = await _generate(tmp_path, args)
    assert "error" in result


@pytest.mark.asyncio
async def test_server_entry_missing_name_is_rejected(tmp_path):
    args = _base_args(server_entry={"desc": "Acme pool controller"})
    result = await _generate(tmp_path, args)
    assert "error" in result
    assert not (tmp_path / "acme_pool").exists()


# --- controller node display name must include the project's own name ---


@pytest.mark.asyncio
async def test_controller_node_name_includes_the_project_name(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    plugin_py = (tmp_path / "acme_pool" / "plugin.py").read_text()
    main_py = (tmp_path / "acme_pool" / "main.py").read_text()
    assert "name='AcmePool Controller'" in plugin_py
    assert "'AcmePool Controller'" in main_py


@pytest.mark.asyncio
async def test_controller_node_name_is_never_the_bare_word_controller(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    plugin_py = (tmp_path / "acme_pool" / "plugin.py").read_text()
    main_py = (tmp_path / "acme_pool" / "main.py").read_text()
    # "Controller" only ever appears as part of "AcmePool Controller" or the
    # Controller *class* name -- never as a standalone node-name value.
    assert "'Controller'" not in plugin_py
    assert "'Controller'" not in main_py


@pytest.mark.asyncio
async def test_server_entry_omits_optional_keys_when_not_applicable(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    entry = json.loads((tmp_path / "acme_pool" / "server_entry.json").read_text())
    assert entry["name"] == "AcmePool"
    assert entry["type"] == "python3"
    assert entry["executable"] == "main.py"
    assert entry["runAs"] == scaffold._DEFAULT_RUN_AS
    assert entry["status"] == "active"
    assert entry["purchaseOptions"] == []
    assert entry["install"] == "install.sh"
    assert entry["developer"] == DEVELOPER_EMAIL
    assert entry["author"] == DEVELOPER_NAME
    for key in ("oauth", "aiPrompt", "aiTools", "authorize"):
        assert key not in entry


@pytest.mark.asyncio
async def test_server_entry_runas_explicit_value_overrides_default(tmp_path):
    args = _base_args(server_entry={"name": "AcmePool", "runAs": "custom_user"})
    result = await _generate(tmp_path, args)
    assert "error" not in result
    entry = json.loads((tmp_path / "acme_pool" / "server_entry.json").read_text())
    assert entry["runAs"] == "custom_user"


@pytest.mark.asyncio
async def test_regenerating_preserves_a_previously_persisted_nsid(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    entry_path = tmp_path / "acme_pool" / "server_entry.json"
    entry = json.loads(entry_path.read_text())
    entry["nsid"] = "local.acme_pool-abc123"
    entry_path.write_text(json.dumps(entry))

    result = await _generate(tmp_path, _base_args(confirm_overwrite=True))
    assert "error" not in result
    regenerated = json.loads(entry_path.read_text())
    assert regenerated["nsid"] == "local.acme_pool-abc123"


@pytest.mark.asyncio
async def test_first_generation_never_invents_an_nsid(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    entry = json.loads((tmp_path / "acme_pool" / "server_entry.json").read_text())
    assert "nsid" not in entry


@pytest.mark.asyncio
async def test_regenerating_tolerates_a_malformed_existing_server_entry(tmp_path):
    plugin_dir = tmp_path / "acme_pool"
    plugin_dir.mkdir()
    (plugin_dir / "server_entry.json").write_text("{not valid json")

    result = await _generate(tmp_path, _base_args(confirm_overwrite=True))
    assert "error" not in result
    entry = json.loads((plugin_dir / "server_entry.json").read_text())
    assert "nsid" not in entry


@pytest.mark.asyncio
async def test_server_entry_renders_devd_rule_when_given(tmp_path):
    args = _base_args(
        server_entry={
            "name": "AcmePool",
            "devd": {"vendor_id": "10c4", "product_id": "ea60", "name": "acmepool"},
        }
    )
    result = await _generate(tmp_path, args)
    assert "error" not in result
    entry = json.loads((tmp_path / "acme_pool" / "server_entry.json").read_text())
    assert 'match "vendor" "0x10c4"' in entry["devd"]
    assert 'match "product" "0xea60"' in entry["devd"]
    assert "pg3.acmepool" in entry["devd"]
    assert "UDX_OWNER_PLACE_HOLDER" in entry["devd"]
    assert "UDX_PERMISSION_PLACE_HOLDER" in entry["devd"]


@pytest.mark.asyncio
async def test_server_entry_omits_devd_when_not_given(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    entry = json.loads((tmp_path / "acme_pool" / "server_entry.json").read_text())
    assert "devd" not in entry


@pytest.mark.asyncio
async def test_server_entry_devd_requires_all_three_fields(tmp_path):
    args = _base_args(server_entry={"name": "AcmePool", "devd": {"vendor_id": "10c4"}})
    result = await _generate(tmp_path, args)
    assert "error" in result


@pytest.mark.asyncio
async def test_server_entry_includes_ai_fields_when_ai_enabled(tmp_path):
    args = _base_args(
        server_entry={
            "name": "AcmePool",
            "ai_enabled": True,
            "aiPrompt": "Ask about pool status.",
            "aiTools": [{"name": "get_dates", "description": "x"}],
        },
        override_bodies={
            "handle_custom_request": "def handle_custom_request(self, request):\n    pass\n",
            "_get_dates": "async def _get_dates(self, payload):\n    return {}\n",
        },
    )
    result = await _generate(tmp_path, args)
    assert "error" not in result
    entry = json.loads((tmp_path / "acme_pool" / "server_entry.json").read_text())
    assert entry["aiPrompt"] == "Ask about pool status."
    assert json.loads(entry["aiTools"]) == [{"name": "get_dates", "description": "x"}]


# --- overwrite flow ---


@pytest.mark.asyncio
async def test_existing_files_without_confirm_overwrite_lists_conflicts_and_writes_nothing(tmp_path):
    first = await _generate(tmp_path, _base_args())
    assert "error" not in first

    second = await _generate(tmp_path, _base_args(iteration_note="Second pass."))
    assert "conflicts" in second
    assert "plugin.py" in second["conflicts"]


@pytest.mark.asyncio
async def test_confirm_overwrite_true_proceeds(tmp_path):
    first = await _generate(tmp_path, _base_args())
    assert "error" not in first

    second = await _generate(tmp_path, _base_args(iteration_note="Second pass.", confirm_overwrite=True))
    assert "error" not in second
    assert "conflicts" not in second


# --- context.md append-only log ---


@pytest.mark.asyncio
async def test_context_md_accumulates_across_calls(tmp_path):
    await _generate(tmp_path, _base_args(iteration_note="First note."))
    await _generate(tmp_path, _base_args(iteration_note="Second note.", confirm_overwrite=True))

    context = (tmp_path / "acme_pool" / "context.md").read_text()
    assert "First note." in context
    assert "Second note." in context


# --- sources.md: deduped, cross-session evidence record ---


@pytest.mark.asyncio
async def test_sources_md_accumulates_new_sources_across_calls(tmp_path):
    ledger1 = EvidenceLedger()
    ledger1.record_source(tier="github", url="https://github.com/example/repo-a", title="repo-a")
    await _generate(tmp_path, _base_args(), ledger=ledger1)

    ledger2 = EvidenceLedger()
    ledger2.record_source(tier="web_search", url="https://example.com/docs", title="docs")
    await _generate(tmp_path, _base_args(confirm_overwrite=True), ledger=ledger2)

    sources = (tmp_path / "acme_pool" / "sources.md").read_text()
    assert "repo-a" in sources
    assert "docs" in sources


@pytest.mark.asyncio
async def test_sources_md_does_not_duplicate_an_already_recorded_source(tmp_path):
    def ledger_with(url):
        ledger = EvidenceLedger()
        ledger.record_source(tier="github", url=url, title="repo-a")
        return ledger

    await _generate(tmp_path, _base_args(), ledger=ledger_with("https://github.com/example/repo-a"))
    await _generate(
        tmp_path, _base_args(confirm_overwrite=True), ledger=ledger_with("https://github.com/example/repo-a")
    )

    sources = (tmp_path / "acme_pool" / "sources.md").read_text()
    assert sources.count("https://github.com/example/repo-a") == 1


@pytest.mark.asyncio
async def test_sources_md_in_files_written(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "sources.md" in result["files_written"]


# --- README is license-only now; the full source list lives in sources.md ---


@pytest.mark.asyncio
async def test_readme_has_no_license_section_when_nothing_flagged(tmp_path):
    # _ledger_with_evidence()'s default source has license_ok=True.
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    readme = (tmp_path / "acme_pool" / "README.md").read_text()
    assert "License Notice" not in readme
    assert "## Sources" not in readme  # old unconditional bullet-list behavior is gone


@pytest.mark.asyncio
async def test_readme_license_notice_lists_only_flagged_sources(tmp_path):
    ledger = EvidenceLedger()
    ledger.record_source(tier="github", url="https://github.com/example/good", title="good", license="MIT", license_ok=True)
    ledger.record_source(tier="github", url="https://github.com/example/bad", title="bad", license="GPL-3.0", license_ok=False)
    result = await _generate(tmp_path, _base_args(), ledger=ledger)
    assert "error" not in result
    readme = (tmp_path / "acme_pool" / "README.md").read_text()
    assert "## License Notice" in readme
    assert "bad" in readme
    assert "good" not in readme


@pytest.mark.asyncio
async def test_readme_license_notice_persists_across_a_regeneration_that_does_not_reflag_it(tmp_path):
    flagging_ledger = EvidenceLedger()
    flagging_ledger.record_source(tier="github", url="https://github.com/example/bad", title="bad", license="GPL-3.0", license_ok=False)
    await _generate(tmp_path, _base_args(), ledger=flagging_ledger)

    unrelated_ledger = EvidenceLedger()
    unrelated_ledger.record_source(tier="web_search", url="https://example.com/unrelated", title="unrelated")
    result = await _generate(tmp_path, _base_args(confirm_overwrite=True), ledger=unrelated_ledger)

    assert "error" not in result
    readme = (tmp_path / "acme_pool" / "README.md").read_text()
    assert "## License Notice" in readme
    assert "bad" in readme


# --- LICENSE.md: full third-party attribution + an unconditional MIT grant
# for this plugin's own code ---


@pytest.mark.asyncio
async def test_license_md_contains_mit_grant_unconditionally(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    license_md = (tmp_path / "acme_pool" / "LICENSE.md").read_text()
    assert "## MIT License" in license_md
    assert f"Copyright (c) " in license_md
    assert DEVELOPER_NAME in license_md


@pytest.mark.asyncio
async def test_license_md_attributes_sources_regardless_of_license_ok(tmp_path):
    ledger = EvidenceLedger()
    ledger.record_source(tier="github", url="https://github.com/example/good", title="good", license="MIT", license_ok=True)
    ledger.record_source(tier="github", url="https://github.com/example/bad", title="bad", license="GPL-3.0", license_ok=False)
    result = await _generate(tmp_path, _base_args(), ledger=ledger)
    assert "error" not in result
    license_md = (tmp_path / "acme_pool" / "LICENSE.md").read_text()
    assert "## Third-Party Attributions" in license_md
    assert "good" in license_md
    assert "bad" in license_md


@pytest.mark.asyncio
async def test_license_md_omits_attributions_section_when_no_source_has_a_license(tmp_path):
    ledger = EvidenceLedger()
    ledger.record_source(tier="web_search", url="https://example.com/unrelated", title="unrelated")
    result = await _generate(tmp_path, _base_args(), ledger=ledger)
    assert "error" not in result
    license_md = (tmp_path / "acme_pool" / "LICENSE.md").read_text()
    assert "Third-Party Attributions" not in license_md
    assert "## MIT License" in license_md


@pytest.mark.asyncio
async def test_license_md_attribution_persists_across_a_regeneration_that_does_not_reflag_it(tmp_path):
    attributing_ledger = EvidenceLedger()
    attributing_ledger.record_source(tier="github", url="https://github.com/example/good", title="good", license="MIT", license_ok=True)
    await _generate(tmp_path, _base_args(), ledger=attributing_ledger)

    unrelated_ledger = EvidenceLedger()
    unrelated_ledger.record_source(tier="web_search", url="https://example.com/unrelated", title="unrelated")
    result = await _generate(tmp_path, _base_args(confirm_overwrite=True), ledger=unrelated_ledger)

    assert "error" not in result
    license_md = (tmp_path / "acme_pool" / "LICENSE.md").read_text()
    assert "good" in license_md


@pytest.mark.asyncio
async def test_license_md_participates_in_overwrite_conflict_check(tmp_path):
    await _generate(tmp_path, _base_args())
    second = await _generate(tmp_path, _base_args())
    assert "LICENSE.md" in second["conflicts"]


@pytest.mark.asyncio
async def test_license_md_in_files_written(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "LICENSE.md" in result["files_written"]


# --- persist/ directory, always created, for any override body that needs
# to persist something beyond customParams/customData; data/ is a separate,
# narrower directory created only when server_entry.fileUpload is true ---


@pytest.mark.asyncio
async def test_persist_directory_is_always_created_on_disk(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    assert (tmp_path / "acme_pool" / "persist").is_dir()
    assert "persist/" in result["files_written"]


@pytest.mark.asyncio
async def test_rendered_plugin_py_always_points_persist_dir_at_the_persist_subdirectory(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "plugin.py").read_text()
    assert 'self.persist_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "persist")' in source


@pytest.mark.asyncio
async def test_data_directory_is_not_created_when_file_upload_is_not_set(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    assert not (tmp_path / "acme_pool" / "data").exists()
    assert "data/" not in result["files_written"]
    source = (tmp_path / "acme_pool" / "plugin.py").read_text()
    assert "self.data_dir" not in source


@pytest.mark.asyncio
async def test_data_directory_is_created_on_disk_when_file_upload_is_true(tmp_path):
    args = _base_args(server_entry={"name": "AcmePool", "desc": "Acme pool controller", "fileUpload": True})
    result = await _generate(tmp_path, args)
    assert "error" not in result
    assert (tmp_path / "acme_pool" / "data").is_dir()
    assert "data/" in result["files_written"]


@pytest.mark.asyncio
async def test_rendered_plugin_py_points_data_dir_at_the_data_subdirectory_when_file_upload_is_true(tmp_path):
    args = _base_args(server_entry={"name": "AcmePool", "desc": "Acme pool controller", "fileUpload": True})
    result = await _generate(tmp_path, args)
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "plugin.py").read_text()
    assert 'self.data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")' in source


# --- main.py sends the Dynamic Profile on startup -- writing profile.json
# to disk alone does nothing to PG3/IoX (design/developers/plugin_model.md
# §1/§2/§5); the plugin's own code must call updateJsonProfile() ---


@pytest.mark.asyncio
async def test_main_py_sends_the_profile_via_updatejsonprofile(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "main.py").read_text()
    assert "import json" in source
    assert "import os" in source
    assert 'polyglot.updateJsonProfile(json.load(f), {"waitResponse": True})' in source


@pytest.mark.asyncio
async def test_main_py_sends_the_profile_before_constructing_the_controller(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "main.py").read_text()
    assert (
        source.index("updateJsonProfile")
        < source.index("Controller(polyglot")
        < source.index("polyglot.ready()")
        < source.index("polyglot.runForever()")
    )


@pytest.mark.asyncio
async def test_main_py_logs_the_profile_update_response(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "main.py").read_text()
    assert 'response = polyglot.updateJsonProfile(json.load(f), {"waitResponse": True})' in source
    assert 'LOGGER.info("Profile update response: %s", response)' in source


@pytest.mark.asyncio
async def test_main_py_passes_enable_webhook_option_when_webhook_enabled(tmp_path):
    args = _base_args(
        server_entry={"name": "AcmePool", "webhook_enabled": True},
        override_bodies={"handle_webhook": "def handle_webhook(self, data):\n    pass\n"},
    )
    result = await _generate(tmp_path, args)
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "main.py").read_text()
    assert 'udi_interface.Interface([], {"enableWebhook": True})' in source


@pytest.mark.asyncio
async def test_main_py_omits_enable_webhook_option_when_webhook_disabled(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "main.py").read_text()
    assert "udi_interface.Interface([])" in source
    assert "enableWebhook" not in source


# --- main.py/install.sh both self-detect a local-dev .venv (see
# setup_dev_venv) -- production never has one, so both checks are always a
# no-op there; the host always launches main.py with its bare system
# python3 regardless, confirmed against a real /usr/local/etc/rc.d/plugin_N
# script, so main.py re-exec'ing into .venv itself is the only place this
# can take effect once a plugin is actually registered/started ---


@pytest.mark.asyncio
async def test_main_py_re_execs_into_venv_before_importing_udi_interface(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "main.py").read_text()
    assert 'os.path.join(_PLUGIN_DIR, ".venv", "bin", "python3")' in source
    assert "os.execv(_VENV_PYTHON" in source
    # The re-exec decision itself probes "import udi_interface" (before
    # execv) to see whether a .venv is even needed -- but the real,
    # unconditional import the rest of the file uses still comes after
    # that decision; a failed probe discards the whole process via execv
    # before ever reaching it.
    assert source.index("os.execv") < source.rindex("import udi_interface")


@pytest.mark.asyncio
async def test_main_py_venv_reexec_uses_bare_python3_as_argv0(tmp_path):
    # Regression: argv[0] must be the literal "python3", not _VENV_PYTHON's
    # full path -- confirmed against a real host's /etc/rc.subr that
    # check_pidfile()/_find_processes() matches the running process's own
    # argv[0] against the service's procname (the real system python3's
    # path) or its bare basename ("python3"). Passing the venv's full path
    # as argv[0] breaks that match even though the PID is exactly right and
    # the process is genuinely alive, which makes rc.subr conclude the
    # service isn't running and spawn a duplicate on every subsequent
    # start. os.execv's first argument (_VENV_PYTHON) is still the real
    # interpreter that actually runs -- only argv[0] changes.
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "main.py").read_text()
    assert 'os.execv(_VENV_PYTHON, ["python3"] + sys.argv)' in source


@pytest.mark.asyncio
async def test_main_py_venv_reexec_skips_when_deps_already_importable(tmp_path):
    # Whether to re-exec is decided by directly probing whether dependencies
    # (udi_interface) are already importable without entering .venv at all
    # -- not by inferring it indirectly from launch parentage or a
    # "plugin_"-prefixed username, since a generated plugin's own runAs can
    # now default to the authoring tool's own account, which can itself be
    # "plugin_"-prefixed while the tool is developer-tested on a real hub.
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "main.py").read_text()
    assert "import udi_interface" in source
    # Pins the fix itself, not just token presence -- a regression back to
    # an unconditional re-exec would still pass a bare substring check.
    assert "if not _deps_already_available:" in source
    assert "_is_framework_launch" not in source
    assert "_is_framework_user" not in source
    venv_guard_start = source.index("if sys.prefix == sys.base_prefix")
    probe = source.index("import udi_interface")
    execv_call = source.index("os.execv(_VENV_PYTHON")
    # The import probe lives inside the existing .venv-exists guard, and
    # runs before the (now further-nested) os.execv call.
    assert venv_guard_start < probe < execv_call


@pytest.mark.asyncio
async def test_main_py_venv_reexec_prepends_venv_bin_to_path(tmp_path):
    # argv[0] is deliberately the bare "python3" (see the test above/the
    # argv0 regression test) for rc.subr's own service-identity matching --
    # anything downstream that re-resolves that bare name via PATH instead
    # of the exact PID must still land on this venv's interpreter, not the
    # system one.
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "main.py").read_text()
    path_prepend = 'os.environ["PATH"] = os.path.dirname(_VENV_PYTHON) + os.pathsep + os.environ.get("PATH", "")'
    assert path_prepend in source
    assert source.index(path_prepend) < source.index("os.execv(_VENV_PYTHON")


@pytest.mark.asyncio
async def test_main_py_venv_reexec_probe_is_defensively_wrapped(tmp_path):
    # Runs before udi_interface.LOGGER exists -- any failure importing
    # udi_interface here (not just ModuleNotFoundError) must degrade
    # silently to the old behavior (allow the re-exec), not crash the
    # plugin before it starts.
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "main.py").read_text()
    probe_import = source.index("import udi_interface")
    except_clause = source.index("except Exception:")
    execv_call = source.index("os.execv(_VENV_PYTHON")
    assert probe_import < except_clause < execv_call


@pytest.mark.asyncio
async def test_install_sh_uses_venv_pip_when_present_else_falls_back_to_user_install(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    install_sh = (tmp_path / "acme_pool" / "install.sh").read_text()
    assert '.venv/bin/pip3 install -r requirements.txt' in install_sh
    assert "pip3 install -r requirements.txt --user" in install_sh
    assert '[ -x ".venv/bin/pip3" ]' in install_sh


# --- node classes ---


@pytest.mark.asyncio
async def test_extra_node_classes_are_written_as_separate_files(tmp_path):
    args = _base_args(
        node_classes={
            "pump_node": (
                '"""Pump node."""\n\nclass Pump:\n    id = "switch"\n\n'
                "    def don(self, *args, **kwargs):\n        pass\n\n"
                '    commands = {"DON": don}\n'
            )
        }
    )
    result = await _generate(tmp_path, args)
    assert "error" not in result
    pump_source = (tmp_path / "acme_pool" / "pump_node.py").read_text()
    assert f"id = '{FAKE_MAC6}_switch'" in pump_source


@pytest.mark.asyncio
async def test_invalid_node_class_syntax_is_rejected(tmp_path):
    args = _base_args(node_classes={"pump_node": "def broken(:\n"})
    result = await _generate(tmp_path, args)
    assert "error" in result
    assert not (tmp_path / "acme_pool").exists()


@pytest.mark.asyncio
async def test_node_class_missing_id_is_rejected(tmp_path):
    args = _base_args(node_classes={"pump_node": "class Pump:\n    pass\n"})
    result = await _generate(tmp_path, args)
    assert "error" in result
    assert "id" in result["error"]
    assert not (tmp_path / "acme_pool").exists()


@pytest.mark.asyncio
async def test_node_class_with_unrecognized_id_is_rejected(tmp_path):
    args = _base_args(node_classes={"pump_node": 'class Pump:\n    id = "no_such_nodedef"\n'})
    result = await _generate(tmp_path, args)
    assert "error" in result
    assert not (tmp_path / "acme_pool").exists()


# --- guard 7b: a node class's commands = {...} dict must cover its own
# nodedef's cmds.accepts ids (WIRE_PROFILE's "switch" nodedef declares
# cmds.accepts = [{"id": "DON"}]) ---


@pytest.mark.asyncio
async def test_node_class_commands_dict_satisfying_required_accepts_is_accepted(tmp_path):
    args = _base_args(
        node_classes={
            "pump_node": (
                'class Pump:\n    id = "switch"\n\n'
                "    def don(self, *args, **kwargs):\n        pass\n\n"
                '    commands = {"DON": don}\n'
            )
        }
    )
    result = await _generate(tmp_path, args)
    assert "error" not in result


@pytest.mark.asyncio
async def test_node_class_commands_dict_missing_required_entry_is_rejected(tmp_path):
    args = _base_args(node_classes={"pump_node": 'class Pump:\n    id = "switch"\n    commands = {}\n'})
    result = await _generate(tmp_path, args)
    assert "error" in result
    assert "DON" in result["error"]
    assert not (tmp_path / "acme_pool").exists()


@pytest.mark.asyncio
async def test_node_class_with_no_commands_dict_at_all_is_rejected_when_accepts_is_non_empty(tmp_path):
    args = _base_args(node_classes={"pump_node": 'class Pump:\n    id = "switch"\n'})
    result = await _generate(tmp_path, args)
    assert "error" in result
    assert "commands" in result["error"]
    assert not (tmp_path / "acme_pool").exists()


@pytest.mark.asyncio
async def test_node_class_dynamic_commands_assignment_is_treated_as_missing(tmp_path):
    args = _base_args(
        node_classes={
            "pump_node": (
                'class Pump:\n    id = "switch"\n\n'
                "    def don(self, *args, **kwargs):\n        pass\n\n"
                "    def __init__(self):\n        self.commands = {\"DON\": self.don}\n"
            )
        }
    )
    result = await _generate(tmp_path, args)
    assert "error" in result
    assert not (tmp_path / "acme_pool").exists()


@pytest.mark.asyncio
async def test_node_class_with_empty_accepts_nodedef_needs_no_commands_dict(tmp_path):
    profile = json.loads(json.dumps(WIRE_PROFILE))
    profile["nodedefs"].append(
        {
            "id": "no_cmds",
            "name": "No Cmds",
            "properties": [{"id": "ST", "name": "Status", "editor": "ED_ONOFF"}],
            "cmds": {"sends": [], "accepts": []},
            "links": {"ctl": [], "rsp": []},
        }
    )
    args = _base_args(profile=profile, node_classes={"bare_node": 'class Bare:\n    id = "no_cmds"\n'})
    result = await _generate(tmp_path, args)
    assert "error" not in result


@pytest.mark.asyncio
async def test_mac_fetch_failure_is_rejected(tmp_path):
    result = await _generate(tmp_path, _base_args(), nucore_interface=_FakeNuCoreInterface(mac_address=None))
    assert "error" in result
    assert not (tmp_path / "acme_pool").exists()


@pytest.mark.asyncio
async def test_profile_missing_controller_nodedef_is_rejected(tmp_path):
    profile_without_controller = {
        "editors": WIRE_PROFILE["editors"],
        "nodedefs": [nd for nd in WIRE_PROFILE["nodedefs"] if nd["id"] != "controller"],
        "linkdefs": [],
    }
    result = await _generate(tmp_path, _base_args(profile=profile_without_controller))
    assert "error" in result
    assert "controller" in result["error"]
    assert not (tmp_path / "acme_pool").exists()


@pytest.mark.asyncio
async def test_controller_id_and_address_are_mac_derived(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    plugin_py = (tmp_path / "acme_pool" / "plugin.py").read_text()
    assert f"id = '{FAKE_MAC6}_controller'" in plugin_py
    assert f"'{FAKE_MAC6}_ctl'" in plugin_py
    profile_json = json.loads((tmp_path / "acme_pool" / "profile.json").read_text())
    nodedef_ids = {nd["id"] for nd in profile_json["nodedefs"]}
    assert nodedef_ids == {f"{FAKE_MAC6}_controller", f"{FAKE_MAC6}_switch"}


# --- controller connection status (ST driver) is entirely tool-generated ---


@pytest.mark.asyncio
async def test_controller_always_declares_the_st_driver_and_conn_status(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    plugin_py = (tmp_path / "acme_pool" / "plugin.py").read_text()
    assert "drivers = [{'driver': 'ST', 'value': 0, 'uom': 25, 'name': 'Status'}]" in plugin_py
    assert "self.poly.addNode(self, conn_status='ST')" in plugin_py


@pytest.mark.asyncio
async def test_controller_nodedef_gets_a_matching_st_property_and_editor(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    profile_json = json.loads((tmp_path / "acme_pool" / "profile.json").read_text())

    controller_nodedef = next(nd for nd in profile_json["nodedefs"] if nd["id"] == f"{FAKE_MAC6}_controller")
    assert {"id": "ST", "name": "Status", "editor": "ED_CONTROLLER_ST"} in controller_nodedef["properties"]

    st_editor = next(e for e in profile_json["editors"] if e["id"] == "ED_CONTROLLER_ST")
    assert st_editor["ranges"] == [
        {"subset": "0,1", "uom": "25", "names": {"0": "Not Connected", "1": "Connected"}}
    ]


@pytest.mark.asyncio
async def test_controller_st_property_is_overwritten_if_already_declared_differently(tmp_path):
    profile_with_custom_st = json.loads(json.dumps(WIRE_PROFILE))
    for nodedef in profile_with_custom_st["nodedefs"]:
        if nodedef["id"] == "controller":
            nodedef["properties"].append({"id": "ST", "editor": "SOME_OTHER_EDITOR"})
    profile_with_custom_st["editors"].append(
        {"id": "ED_CONTROLLER_ST", "ranges": [{"min": 0, "max": 1, "uom": "2"}]}
    )

    result = await _generate(tmp_path, _base_args(profile=profile_with_custom_st))
    assert "error" not in result
    profile_json = json.loads((tmp_path / "acme_pool" / "profile.json").read_text())

    controller_nodedef = next(nd for nd in profile_json["nodedefs"] if nd["id"] == f"{FAKE_MAC6}_controller")
    st_properties = [p for p in controller_nodedef["properties"] if p["id"] == "ST"]
    assert st_properties == [{"id": "ST", "name": "Status", "editor": "ED_CONTROLLER_ST"}]

    st_editor = next(e for e in profile_json["editors"] if e["id"] == "ED_CONTROLLER_ST")
    assert st_editor["ranges"] == [
        {"subset": "0,1", "uom": "25", "names": {"0": "Not Connected", "1": "Connected"}}
    ]


@pytest.mark.asyncio
async def test_controller_duplicate_st_properties_collapse_to_one(tmp_path):
    # A profile with two author-supplied "ST" properties on the controller
    # must not leave a stale duplicate behind after injection.
    profile_with_duplicate_st = json.loads(json.dumps(WIRE_PROFILE))
    for nodedef in profile_with_duplicate_st["nodedefs"]:
        if nodedef["id"] == "controller":
            nodedef["properties"].append({"id": "ST", "name": "First", "editor": "SOME_EDITOR"})
            nodedef["properties"].append({"id": "ST", "name": "Second", "editor": "SOME_OTHER_EDITOR"})

    result = await _generate(tmp_path, _base_args(profile=profile_with_duplicate_st))
    assert "error" not in result
    profile_json = json.loads((tmp_path / "acme_pool" / "profile.json").read_text())

    controller_nodedef = next(nd for nd in profile_json["nodedefs"] if nd["id"] == f"{FAKE_MAC6}_controller")
    st_properties = [p for p in controller_nodedef["properties"] if p["id"] == "ST"]
    assert st_properties == [{"id": "ST", "name": "Status", "editor": "ED_CONTROLLER_ST"}]


# --- every non-controller nodedef needs exactly one ST property ---


@pytest.mark.asyncio
async def test_device_nodedef_missing_st_property_is_rejected(tmp_path):
    profile = json.loads(json.dumps(WIRE_PROFILE))
    for nodedef in profile["nodedefs"]:
        if nodedef["id"] == "switch":
            nodedef["properties"] = [{"id": "ON_OFF", "name": "On Off", "editor": "ED_ONOFF"}]

    result = await _generate(tmp_path, _base_args(profile=profile))
    assert "error" in result
    assert any("no 'ST' property" in detail for detail in result.get("details") or [])
    assert not (tmp_path / "acme_pool").exists()


@pytest.mark.asyncio
async def test_device_nodedef_with_duplicate_property_id_is_rejected(tmp_path):
    profile = json.loads(json.dumps(WIRE_PROFILE))
    for nodedef in profile["nodedefs"]:
        if nodedef["id"] == "switch":
            nodedef["properties"].append({"id": "ST", "name": "Status Again", "editor": "ED_ONOFF"})

    result = await _generate(tmp_path, _base_args(profile=profile))
    assert "error" in result
    assert any("more than once" in detail for detail in result.get("details") or [])
    assert not (tmp_path / "acme_pool").exists()


# --- guard 2: developer commissioning ---


@pytest.mark.asyncio
async def test_missing_developer_config_is_refused(tmp_path):
    result = await _generate(tmp_path, _base_args(), skip_developer_config=True)
    assert "error" in result
    assert "configure_developer" in result["error"]
    assert not (tmp_path / "acme_pool").exists()


@pytest.mark.asyncio
async def test_mismatched_authenticated_user_is_refused(tmp_path):
    result = await _generate(tmp_path, _base_args(), get_user_id=lambda: "someone-else@example.com")
    assert "error" in result
    assert not (tmp_path / "acme_pool").exists()


@pytest.mark.asyncio
async def test_no_authenticated_user_id_does_not_block_generation(tmp_path):
    result = await _generate(tmp_path, _base_args(), get_user_id=lambda: None)
    assert "error" not in result


@pytest.mark.asyncio
async def test_matching_authenticated_user_is_allowed(tmp_path):
    result = await _generate(tmp_path, _base_args(), get_user_id=lambda: DEVELOPER_EMAIL)
    assert "error" not in result


# --- customParams/nsdata must be JSON-encoded strings, not raw objects/arrays
# (confirmed against a real failed registration -- the host rejects a raw
# array) ---


@pytest.mark.asyncio
async def test_custom_params_is_a_json_encoded_string(tmp_path):
    # customParams is PG3's actual flat 'customparams' shape -- {field_name:
    # value} -- not the richer [{name, type, isRequired, ...}] list
    # ('customtypedparams'), confirmed directly against a real plugin's
    # working registration. 'value' is a seeded initial value (often ''),
    # not documentation -- that's the separate custom_param_docs input below.
    custom_params = {"host": ""}
    args = _base_args(server_entry={"name": "AcmePool", "customParams": custom_params}, custom_param_docs="Host docs.")
    result = await _generate(tmp_path, args)
    assert "error" not in result
    entry = json.loads((tmp_path / "acme_pool" / "server_entry.json").read_text())
    assert isinstance(entry["customParams"], str)
    assert json.loads(entry["customParams"]) == custom_params


@pytest.mark.asyncio
async def test_custom_params_rejects_the_richer_typed_list_shape(tmp_path):
    custom_params = [{"name": "host", "desc": "IP address", "type": "string"}]
    args = _base_args(server_entry={"name": "AcmePool", "customParams": custom_params}, custom_param_docs="docs")
    result = await _generate(tmp_path, args)
    assert "error" in result


@pytest.mark.asyncio
async def test_custom_params_rejects_non_string_values(tmp_path):
    args = _base_args(server_entry={"name": "AcmePool", "customParams": {"host": 123}}, custom_param_docs="docs")
    result = await _generate(tmp_path, args)
    assert "error" in result


# --- setCustomParamsDoc comes from the separate, richly-authored
# custom_param_docs input -- never derived from customParams' own minimal
# {field_name: value} map, and never something the LLM-authored half has
# to remember to call itself ---


@pytest.mark.asyncio
async def test_custom_param_docs_is_required_when_custom_params_given(tmp_path):
    args = _base_args(server_entry={"name": "AcmePool", "customParams": {"host": ""}})
    result = await _generate(tmp_path, args)
    assert "error" in result


@pytest.mark.asyncio
async def test_custom_param_docs_renders_set_custom_param_docs_call_verbatim(tmp_path):
    docs = "## Setup\n\n- **host**: enter the IP address printed on your controller's sticker.\n"
    args = _base_args(
        server_entry={"name": "AcmePool", "customParams": {"host": ""}},
        custom_param_docs=docs,
    )
    result = await _generate(tmp_path, args)
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "plugin.py").read_text()
    assert "self.poly.setCustomParamsDoc(" in source
    assert repr(docs) in source


@pytest.mark.asyncio
async def test_no_custom_params_means_no_set_custom_param_docs_call(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "plugin.py").read_text()
    assert "setCustomParamsDoc" not in source


@pytest.mark.asyncio
async def test_nsdata_is_a_json_encoded_string(tmp_path):
    nsdata = {"seed": "value"}
    args = _base_args(server_entry={"name": "AcmePool", "nsdata": nsdata})
    result = await _generate(tmp_path, args)
    assert "error" not in result
    entry = json.loads((tmp_path / "acme_pool" / "server_entry.json").read_text())
    assert isinstance(entry["nsdata"], str)
    assert json.loads(entry["nsdata"]) == nsdata


# --- requireEisyui ---


@pytest.mark.asyncio
async def test_require_eisyui_true_is_passed_through(tmp_path):
    args = _base_args(server_entry={"name": "AcmePool", "requireEisyui": True})
    result = await _generate(tmp_path, args)
    assert "error" not in result
    entry = json.loads((tmp_path / "acme_pool" / "server_entry.json").read_text())
    assert entry["requireEisyui"] is True


@pytest.mark.asyncio
async def test_require_eisyui_omitted_is_absent(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    entry = json.loads((tmp_path / "acme_pool" / "server_entry.json").read_text())
    assert "requireEisyui" not in entry


# --- requirements.txt + install script ---


@pytest.mark.asyncio
async def test_requirements_txt_always_includes_the_base_requirement(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    requirements = (tmp_path / "acme_pool" / "requirements.txt").read_text()
    assert "udi_interface>=3.4.7" in requirements
    assert "requirements.txt" in result["files_written"]


@pytest.mark.asyncio
async def test_requirements_txt_includes_extra_requirements_and_dedupes(tmp_path):
    args = _base_args(
        server_entry={
            "name": "AcmePool",
            "extra_requirements": ["requests>=2.31.0", "udi_interface>=3.4.7", "requests>=2.31.0"],
        }
    )
    result = await _generate(tmp_path, args)
    assert "error" not in result
    lines = (tmp_path / "acme_pool" / "requirements.txt").read_text().splitlines()
    assert lines == ["udi_interface>=3.4.7", "requests>=2.31.0"]


@pytest.mark.asyncio
async def test_extra_requirements_rejects_non_string_entries(tmp_path):
    args = _base_args(server_entry={"name": "AcmePool", "extra_requirements": [123]})
    result = await _generate(tmp_path, args)
    assert "error" in result
    assert not (tmp_path / "acme_pool").exists()


@pytest.mark.asyncio
async def test_secret_in_extra_requirements_is_refused(tmp_path):
    args = _base_args(server_entry={"name": "AcmePool", "extra_requirements": ["sekrit-token-value"]})
    result = await _generate(tmp_path, args, secret_values=["sekrit-token-value"])
    assert "error" in result
    assert not (tmp_path / "acme_pool").exists()


# --- install.sh: server_entry.json's 'install' field is its filename, not
# a literal command -- confirmed against the real ioxplugin reference
# tooling (getStoreEntryContent() defaults to "install.sh" by the same
# name) ---


@pytest.mark.asyncio
async def test_install_sh_is_generated_with_expected_content(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    install_sh = (tmp_path / "acme_pool" / "install.sh").read_text()
    assert install_sh.startswith("#!/usr/bin/env bash\n")
    assert "pip3 install -r requirements.txt" in install_sh
    assert "install.sh" in result["files_written"]


@pytest.mark.asyncio
async def test_install_sh_is_executable(tmp_path):
    import os

    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    install_path = tmp_path / "acme_pool" / "install.sh"
    assert os.access(install_path, os.X_OK)


@pytest.mark.asyncio
async def test_install_sh_does_not_enumerate_extra_requirements_itself(tmp_path):
    # install.sh always just invokes requirements.txt, which already
    # carries any extras -- it never needs to list them a second time.
    args = _base_args(server_entry={"name": "AcmePool", "extra_requirements": ["requests>=2.31.0"]})
    result = await _generate(tmp_path, args)
    assert "error" not in result
    install_sh = (tmp_path / "acme_pool" / "install.sh").read_text()
    assert "requests" not in install_sh


@pytest.mark.asyncio
async def test_server_entry_install_field_is_the_script_filename(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    entry = json.loads((tmp_path / "acme_pool" / "server_entry.json").read_text())
    assert entry["install"] == "install.sh"


@pytest.mark.asyncio
async def test_install_sh_participates_in_overwrite_conflict_check(tmp_path):
    await _generate(tmp_path, _base_args())
    second = await _generate(tmp_path, _base_args())
    assert "install.sh" in second["conflicts"]
