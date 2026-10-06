"""generate_plugin_scaffold (design/developers/plugin_authoring_p4_impl.md
Stage 2) -- all against real tmp_path fixtures, no mocking needed since
generation is local-disk-only and never touches the hub (installing what
gets generated here is Stage 3's separate install_generated_plugin tool).
"""

from __future__ import annotations

import json

import pytest

from unified.plugin_authoring.evidence_ledger import EvidenceLedger
from unified.plugin_authoring.handlers.scaffold import generate_plugin_scaffold
from unified.plugin_authoring.secret_guard import OAUTH_PLACEHOLDER

WIRE_PROFILE = {
    "editors": [{"id": "ED_ONOFF", "ranges": [{"uom": "25", "subset": "0,1"}]}],
    "nodedefs": [
        {
            "id": "ND_SWITCH",
            "name": "Switch",
            "properties": [{"id": "ST", "editor": "ED_ONOFF"}],
            "cmds": {"sends": [], "accepts": [{"id": "DON"}]},
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


async def _generate(tmp_path, args, *, ledger=None, secret_values=None):
    return await generate_plugin_scaffold(
        None,
        args,
        ledger=ledger if ledger is not None else _ledger_with_evidence(),
        secret_values=secret_values or [],
        plugin_output_root=str(tmp_path),
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
    assert entry["oauth"]["client_id"] == OAUTH_PLACEHOLDER
    assert entry["oauth"]["client_secret"] == OAUTH_PLACEHOLDER


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
    assert entry["runAs"] == "eisyai"
    for key in ("oauth", "aiPrompt", "aiTools", "authorize"):
        assert key not in entry


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
    assert entry["aiTools"] == [{"name": "get_dates", "description": "x"}]


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


# --- data/ directory for any override body that needs to persist something
# beyond customParams/customData ---


@pytest.mark.asyncio
async def test_data_directory_is_created_on_disk(tmp_path):
    result = await _generate(tmp_path, _base_args())
    assert "error" not in result
    assert (tmp_path / "acme_pool" / "data").is_dir()
    assert "data/" in result["files_written"]


@pytest.mark.asyncio
async def test_rendered_plugin_py_points_data_dir_at_the_data_subdirectory(tmp_path):
    result = await _generate(tmp_path, _base_args())
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
    assert source.index("updateJsonProfile") < source.index("Controller(polyglot")


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
