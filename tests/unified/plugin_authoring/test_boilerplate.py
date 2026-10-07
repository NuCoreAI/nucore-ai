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


def _ledger_with_evidence() -> EvidenceLedger:
    ledger = EvidenceLedger()
    ledger.record_source(tier="github", url="https://github.com/example/repo", title="example/repo", license="MIT", license_ok=True)
    return ledger


async def _generate(tmp_path, args):
    developer_config.write_developer_config(str(tmp_path), email=DEVELOPER_EMAIL, name=DEVELOPER_NAME)
    return await generate_plugin_scaffold(
        None, args, ledger=_ledger_with_evidence(), secret_values=[], plugin_output_root=str(tmp_path)
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

    result = await regenerate_plugin_boilerplate(None, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

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
async def test_authorize_and_ai_enabled_are_preserved_across_refresh(tmp_path):
    args = _base_args(
        server_entry={
            "name": "AcmePool",
            "authorize": True,
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

    result = await regenerate_plugin_boilerplate(None, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))
    assert "error" not in result

    plugin_py = (plugin_dir / "plugin.py").read_text()
    assert "udi_interface.OAuth(self.poly)" in plugin_py
    assert "custom start" in plugin_py
    assert "def handle_custom_request(self, request):" in plugin_py
    assert "def _get_dates(self, payload):" in plugin_py
