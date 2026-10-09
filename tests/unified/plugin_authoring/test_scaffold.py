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
from unified.plugin_authoring.handlers.scaffold import generate_plugin_scaffold
from unified.plugin_authoring.secret_guard import OAUTH_PLACEHOLDER

DEVELOPER_EMAIL = "dev@example.com"
DEVELOPER_NAME = "Dev Name"

WIRE_PROFILE = {
    "editors": [{"id": "ED_ONOFF", "ranges": [{"uom": "25", "subset": "0,1"}]}],
    "nodedefs": [
        {
            "id": "ND_SWITCH",
            "name": "Switch",
            "properties": [{"id": "ST", "editor": "ED_ONOFF"}],
            "cmds": {"sends": [], "accepts": [{"id": "DON"}]},
            "links": {"ctl": [], "rsp": []},
        }
    ],
    "linkdefs": [],
}

PROFILE_WITH_DANGLING_EDITOR = {
    "editors": [],
    "nodedefs": [{"id": "ND_X", "properties": [{"id": "ST", "editor": "MISSING_EDITOR"}], "cmds": {}}],
    "linkdefs": [],
}


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
    tmp_path, args, *, ledger=None, secret_values=None, get_user_id=None, skip_developer_config=False, default_run_as=None
):
    if not skip_developer_config:
        developer_config.write_developer_config(
            str(tmp_path), email=DEVELOPER_EMAIL, name=DEVELOPER_NAME, default_run_as=default_run_as
        )
    return await generate_plugin_scaffold(
        None,
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
            "oauth": {"auth_endpoint": "https://x/auth", "token_endpoint": "https://x/token"},
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
            "oauth": {"client_secret": "real-secret-value"},
        }
    )
    result = await _generate(tmp_path, args)
    assert "error" in result
    assert not (tmp_path / "acme_pool").exists()


# --- authorize / ai_enabled wiring ---


@pytest.mark.asyncio
async def test_authorize_true_adds_oauth_subscription_and_service(tmp_path):
    args = _base_args(server_entry={"name": "AcmePool", "authorize": True})
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
    args = _base_args(server_entry={"name": "AcmePool", "authorize": True})
    result = await _generate(tmp_path, args)
    assert "error" not in result
    source = (tmp_path / "acme_pool" / "plugin.py").read_text()
    handler_section = source[source.index("def __configDoneHandler(self):") :]
    handler_section = handler_section[: handler_section.index("def __addNodeDoneHandler")]
    assert "self.oauthService.getAccessToken()" in handler_section
    assert 'self.poly.Notices["auth"]' in handler_section
    assert "self.configDoneAlready = True" in handler_section
    assert "self.configDone.notifyAll()" in handler_section


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
    assert generation_inputs == {
        "profile": WIRE_PROFILE,
        "server_entry": {"name": "AcmePool", "desc": "Acme pool controller"},
        "readme_body": "This plugin integrates with the Acme Pool API.",
        "override_bodies": {"start": 'def start(self):\n    LOGGER.info("hi")\n'},
        "node_classes": {},
        "tests": {},
        "custom_param_docs": None,
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
async def test_server_entry_omits_optional_keys_when_not_applicable(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    entry = json.loads((tmp_path / "acme_pool" / "server_entry.json").read_text())
    assert entry["name"] == "AcmePool"
    assert entry["type"] == "python3"
    assert entry["executable"] == "main.py"
    assert entry["runAs"] == "admin"
    assert entry["status"] == "active"
    assert entry["purchaseOptions"] == []
    assert entry["install"] == "install.sh"
    assert entry["developer"] == DEVELOPER_EMAIL
    assert entry["author"] == DEVELOPER_NAME
    for key in ("oauth", "aiPrompt", "aiTools", "authorize"):
        assert key not in entry


@pytest.mark.asyncio
async def test_server_entry_runas_uses_developer_configs_default_run_as(tmp_path):
    result = await _generate(tmp_path, _base_args(), default_run_as="polyglot")
    assert "error" not in result
    entry = json.loads((tmp_path / "acme_pool" / "server_entry.json").read_text())
    assert entry["runAs"] == "polyglot"


@pytest.mark.asyncio
async def test_server_entry_runas_explicit_value_overrides_developer_default(tmp_path):
    args = _base_args(server_entry={"name": "AcmePool", "runAs": "custom_user"})
    result = await _generate(tmp_path, args, default_run_as="polyglot")
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
    assert source.index("os.execv") < source.index("import udi_interface")


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
    args = _base_args(node_classes={"pump_node": '"""Pump node."""\n\nclass Pump:\n    pass\n'})
    result = await _generate(tmp_path, args)
    assert "error" not in result
    assert (tmp_path / "acme_pool" / "pump_node.py").is_file()


@pytest.mark.asyncio
async def test_invalid_node_class_syntax_is_rejected(tmp_path):
    args = _base_args(node_classes={"pump_node": "def broken(:\n"})
    result = await _generate(tmp_path, args)
    assert "error" in result
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
    assert "udi_interface>=3.0.57" in requirements
    assert "requirements.txt" in result["files_written"]


@pytest.mark.asyncio
async def test_requirements_txt_includes_extra_requirements_and_dedupes(tmp_path):
    args = _base_args(
        server_entry={
            "name": "AcmePool",
            "extra_requirements": ["requests>=2.31.0", "udi_interface>=3.0.57", "requests>=2.31.0"],
        }
    )
    result = await _generate(tmp_path, args)
    assert "error" not in result
    lines = (tmp_path / "acme_pool" / "requirements.txt").read_text().splitlines()
    assert lines == ["udi_interface>=3.0.57", "requests>=2.31.0"]


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
