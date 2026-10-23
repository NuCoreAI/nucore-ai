"""regenerate_plugin_boilerplate -- reads back generation_inputs.json
(written by generate_plugin_scaffold itself) and re-renders only
plugin.py/main.py/version.py/install.sh with today's template code,
without the caller re-supplying override_bodies/authorize/ai_enabled/etc.
Builds real fixture plugins via the actual generate_plugin_scaffold
pipeline first (so generation_inputs.json genuinely exists), same posture
as test_scaffold.py -- no mocking, local-disk-only.
"""

from __future__ import annotations

import json

import pytest

from unified.plugin_authoring import developer_config
from unified.plugin_authoring.evidence_ledger import EvidenceLedger
from unified.plugin_authoring.handlers.boilerplate import regenerate_plugin_boilerplate
from unified.plugin_authoring.handlers.scaffold import generate_plugin_scaffold

DEVELOPER_EMAIL = "dev@example.com"
DEVELOPER_NAME = "Dev Name"

# See test_scaffold.py's own COMPLETE_OAUTH_CONFIG -- satisfies the name/auth_endpoint/
# token_endpoint required-when-authorize guard.
COMPLETE_OAUTH_CONFIG = {
    "name": "Acme API",
    "auth_endpoint": "https://x/auth",
    "token_endpoint": "https://x/token",
}

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


class _FakeNuCoreInterface:
    async def refresh_eisy_ai_permissions(self):
        return {"successful": True}

    async def notify_plugin_files_changed(self, paths):
        return {"successful": True}


def _ledger_with_evidence() -> EvidenceLedger:
    ledger = EvidenceLedger()
    ledger.record_source(tier="github", url="https://github.com/example/repo", title="example/repo", license="MIT", license_ok=True)
    return ledger


async def _generate(tmp_path, args):
    developer_config.write_developer_config(str(tmp_path), email=DEVELOPER_EMAIL, name=DEVELOPER_NAME)
    return await generate_plugin_scaffold(
        _FakeNuCoreInterface(), args, ledger=_ledger_with_evidence(), secret_values=[], plugin_output_root=str(tmp_path)
    )


def _base_args(**overrides):
    args = {
        "location": "acme_pool",
        "profile": WIRE_PROFILE,
        "server_entry": {"name": "AcmePool", "desc": "Acme pool controller"},
        "override_bodies": {"start": 'def start(self):\n    LOGGER.info("custom start")\n'},
        "readme_body": "This plugin integrates with the Acme Pool API.",
        "tests": {},
        "iteration_note": "Initial generation.",
    }
    args.update(overrides)
    return args


@pytest.mark.asyncio
async def test_requires_location(tmp_path):
    result = await regenerate_plugin_boilerplate(None, {}, plugin_output_root=str(tmp_path))
    assert "error" in result


@pytest.mark.asyncio
async def test_rejects_path_traversal(tmp_path):
    result = await regenerate_plugin_boilerplate(None, {"location": "../escape"}, plugin_output_root=str(tmp_path))
    assert "error" in result


@pytest.mark.asyncio
async def test_missing_generation_inputs_json(tmp_path):
    plugin_dir = tmp_path / "acme_pool"
    plugin_dir.mkdir()
    (plugin_dir / "profile.json").write_text("{}")  # a plugin generated before this feature existed

    result = await regenerate_plugin_boilerplate(None, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

    assert "error" in result
    assert "generate_plugin_scaffold" in result["error"]


@pytest.mark.asyncio
async def test_malformed_generation_inputs_json(tmp_path):
    plugin_dir = tmp_path / "acme_pool"
    plugin_dir.mkdir()
    (plugin_dir / "generation_inputs.json").write_text("{not valid json")

    result = await regenerate_plugin_boilerplate(None, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

    assert "error" in result


@pytest.mark.asyncio
async def test_regenerates_boilerplate_files_only(tmp_path):
    gen_result = await _generate(tmp_path, _base_args())
    assert "error" not in gen_result
    plugin_dir = tmp_path / "acme_pool"

    # Simulate a template fix landing after this plugin was generated, by
    # hand-editing generation_inputs.json's stored version -- the refresh
    # must pick this up without anything else being re-supplied.
    inputs_path = plugin_dir / "generation_inputs.json"
    inputs = json.loads(inputs_path.read_text())
    inputs["version"] = "9.9.9"
    inputs_path.write_text(json.dumps(inputs))

    profile_before = (plugin_dir / "profile.json").read_text()
    server_entry_before = (plugin_dir / "server_entry.json").read_text()
    readme_before = (plugin_dir / "README.md").read_text()

    result = await regenerate_plugin_boilerplate(
        _FakeNuCoreInterface(), {"location": "acme_pool"}, plugin_output_root=str(tmp_path)
    )

    assert result == {
        "location": "acme_pool",
        "regenerated": ["plugin.py", "main.py", "version.py", "install.sh"],
    }
    assert 'ud_plugin_version = "9.9.9"' in (plugin_dir / "version.py").read_text()
    plugin_py = (plugin_dir / "plugin.py").read_text()
    assert "custom start" in plugin_py  # the original override body survived
    assert "self.configDone.wait(timeout=10)" in plugin_py  # picks up today's wiring

    # Untouched -- never part of this tool's scope.
    assert (plugin_dir / "profile.json").read_text() == profile_before
    assert (plugin_dir / "server_entry.json").read_text() == server_entry_before
    assert (plugin_dir / "README.md").read_text() == readme_before


@pytest.mark.asyncio
async def test_files_changed_notify_failure_does_not_block_write(tmp_path):
    gen_result = await _generate(tmp_path, _base_args())
    assert "error" not in gen_result

    class _FailingNotify(_FakeNuCoreInterface):
        async def notify_plugin_files_changed(self, paths):
            return {"error": "boom"}

    result = await regenerate_plugin_boilerplate(
        _FailingNotify(), {"location": "acme_pool"}, plugin_output_root=str(tmp_path)
    )
    assert result["regenerated"] == ["plugin.py", "main.py", "version.py", "install.sh"]
    assert result["files_changed_notify_error"] == "boom"


@pytest.mark.asyncio
async def test_regeneration_keeps_the_controller_id_and_address_fixed_literals(tmp_path):
    gen_result = await _generate(tmp_path, _base_args())
    assert "error" not in gen_result
    plugin_dir = tmp_path / "acme_pool"

    result = await regenerate_plugin_boilerplate(
        _FakeNuCoreInterface(), {"location": "acme_pool"}, plugin_output_root=str(tmp_path)
    )
    assert "error" not in result

    plugin_py = (plugin_dir / "plugin.py").read_text()
    assert "id = 'controller'" in plugin_py
    assert "'ctl'" in plugin_py


@pytest.mark.asyncio
async def test_regeneration_rebuilds_controller_node_name_from_stored_server_entry_name(tmp_path):
    gen_result = await _generate(tmp_path, _base_args())
    assert "error" not in gen_result
    plugin_dir = tmp_path / "acme_pool"

    result = await regenerate_plugin_boilerplate(
        _FakeNuCoreInterface(), {"location": "acme_pool"}, plugin_output_root=str(tmp_path)
    )
    assert "error" not in result

    plugin_py = (plugin_dir / "plugin.py").read_text()
    main_py = (plugin_dir / "main.py").read_text()
    assert "name='AcmePool Controller'" in plugin_py
    assert "'AcmePool Controller'" in main_py


@pytest.mark.asyncio
async def test_regeneration_preserves_the_controller_st_driver_and_conn_status(tmp_path):
    gen_result = await _generate(tmp_path, _base_args())
    assert "error" not in gen_result
    plugin_dir = tmp_path / "acme_pool"

    result = await regenerate_plugin_boilerplate(
        _FakeNuCoreInterface(), {"location": "acme_pool"}, plugin_output_root=str(tmp_path)
    )
    assert "error" not in result

    plugin_py = (plugin_dir / "plugin.py").read_text()
    assert "drivers = [{'driver': 'ST', 'value': 0, 'uom': 25, 'name': 'Status'}]" in plugin_py
    assert "self.poly.addNode(self, conn_status='ST')" in plugin_py


@pytest.mark.asyncio
async def test_regeneration_rebuilds_the_commands_dispatch_dict(tmp_path):
    profile = json.loads(json.dumps(WIRE_PROFILE))
    for nodedef in profile["nodedefs"]:
        if nodedef["id"] == "controller":
            nodedef["cmds"]["accepts"] = [{"id": "QUERYALL"}]
    gen_result = await _generate(tmp_path, _base_args(profile=profile))
    assert "error" not in gen_result
    plugin_dir = tmp_path / "acme_pool"

    result = await regenerate_plugin_boilerplate(
        _FakeNuCoreInterface(), {"location": "acme_pool"}, plugin_output_root=str(tmp_path)
    )
    assert "error" not in result

    plugin_py = (plugin_dir / "plugin.py").read_text()
    assert "def queryall(self, *args, **kwargs):" in plugin_py
    assert "'QUERYALL': queryall" in plugin_py


@pytest.mark.asyncio
async def test_authorize_and_ai_enabled_are_preserved_across_refresh(tmp_path):
    args = _base_args(
        server_entry={
            "name": "AcmePool",
            "authorize": True,
            "oauth": dict(COMPLETE_OAUTH_CONFIG),
            "ai_enabled": True,
            "aiTools": [{"name": "get_dates", "description": "x"}],
        },
        override_bodies={
            "start": 'def start(self):\n    LOGGER.info("custom start")\n',
            "handle_custom_request": "def handle_custom_request(self, request):\n    pass\n",
            "_get_dates": "def _get_dates(self, payload):\n    pass\n",
        },
    )
    gen_result = await _generate(tmp_path, args)
    assert "error" not in gen_result
    plugin_dir = tmp_path / "acme_pool"

    result = await regenerate_plugin_boilerplate(
        _FakeNuCoreInterface(), {"location": "acme_pool"}, plugin_output_root=str(tmp_path)
    )
    assert "error" not in result

    plugin_py = (plugin_dir / "plugin.py").read_text()
    assert "udi_interface.OAuth(self.poly)" in plugin_py
    assert "custom start" in plugin_py
    assert "def handle_custom_request(self, request):" in plugin_py
    assert "def _get_dates(self, payload):" in plugin_py
    assert "self.startCalled = False" in plugin_py
    assert 'if key in ("oauth", "oauthTokens"):' in plugin_py
    assert "self.oauthService.oauthHandler(token)" in plugin_py
    assert "def __runStartOnce(self):" in plugin_py


@pytest.mark.asyncio
async def test_webhook_enabled_preserved_across_refresh(tmp_path):
    args = _base_args(
        server_entry={"name": "AcmePool", "webhook_enabled": True},
        override_bodies={
            "start": 'def start(self):\n    LOGGER.info("custom start")\n',
            "handle_webhook": "def handle_webhook(self, data):\n    pass\n",
        },
    )
    gen_result = await _generate(tmp_path, args)
    assert "error" not in gen_result
    plugin_dir = tmp_path / "acme_pool"

    result = await regenerate_plugin_boilerplate(
        _FakeNuCoreInterface(), {"location": "acme_pool"}, plugin_output_root=str(tmp_path)
    )
    assert "error" not in result

    plugin_py = (plugin_dir / "plugin.py").read_text()
    assert "self.poly.subscribe(self.poly.WEBHOOK, self.handle_webhook)" in plugin_py
    assert "self.poly.webhookStart()" in plugin_py
    assert "def handle_webhook(self, data):" in plugin_py

    main_py = (plugin_dir / "main.py").read_text()
    assert 'udi_interface.Interface([], {"enableWebhook": True})' in main_py


@pytest.mark.asyncio
async def test_custom_typed_params_preserved_across_refresh(tmp_path):
    typed_params = [{"name": "host", "title": "Host", "isRequired": True}]
    args = _base_args(
        custom_typed_params=typed_params,
        override_bodies={
            "start": 'def start(self):\n    LOGGER.info("custom start")\n',
            "customTypedDataHandler": "def customTypedDataHandler(self, custom_params):\n    self.CustomTypedData.load(custom_params)\n",
        },
    )
    gen_result = await _generate(tmp_path, args)
    assert "error" not in gen_result
    plugin_dir = tmp_path / "acme_pool"

    result = await regenerate_plugin_boilerplate(
        _FakeNuCoreInterface(), {"location": "acme_pool"}, plugin_output_root=str(tmp_path)
    )
    assert "error" not in result

    plugin_py = (plugin_dir / "plugin.py").read_text()
    assert "self.CustomTypedParams = udi_interface.Custom(self.poly, 'customtypedparams')" in plugin_py
    assert "self.CustomTypedData = udi_interface.Custom(self.poly, 'customtypeddata')" in plugin_py
    assert "self.poly.subscribe(self.poly.CUSTOMTYPEDDATA, self.customTypedDataHandler)" in plugin_py
    assert "def customTypedDataHandler(self, custom_params):" in plugin_py
