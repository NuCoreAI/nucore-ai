"""install_generated_plugin (design/developers/plugin_authoring_p4_impl.md
Stage 3) -- register -> install -> start, each stage's failure reported
distinctly via a 'stage' key. FakeBackend is a plain duck-typed stand-in
(not a full NuCoreInterface subclass) since this handler only ever calls
four of its methods.
"""

from __future__ import annotations

import json

import pytest

from unified.plugin_authoring.handlers.install import install_generated_plugin


class FakeBackend:
    def __init__(self):
        self.register_response = {"successful": True, "data": {}}
        self.install_response = {"successful": True, "data": {"profileNum": 7}}
        self.installed_response = {"successful": True, "data": [{"profileNum": 7, "name": "AcmePool", "state": "stopped"}]}
        self.plugin_ops_response = {"successful": True, "data": {}}
        self.register_calls = []
        self.install_calls = []
        self.plugin_ops_calls = []

    async def register_local_plugin(self, entry):
        self.register_calls.append(entry)
        return self.register_response

    async def install_local_plugin(self, nsid, profile_num=None):
        self.install_calls.append((nsid, profile_num))
        return self.install_response

    async def get_installed_plugins(self):
        return self.installed_response

    async def plugin_ops(self, plugin_id, operation):
        self.plugin_ops_calls.append((plugin_id, operation))
        return self.plugin_ops_response


def _write_server_entry(plugin_dir, entry=None):
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "server_entry.json").write_text(
        json.dumps(entry or {"name": "AcmePool", "type": "python3", "path": str(plugin_dir), "executable": "main.py", "runAs": "eisyai"})
    )


@pytest.mark.asyncio
async def test_requires_location(tmp_path):
    backend = FakeBackend()
    result = await install_generated_plugin(backend, {}, plugin_output_root=str(tmp_path))
    assert result["stage"] == "read"


@pytest.mark.asyncio
async def test_rejects_path_traversal(tmp_path):
    backend = FakeBackend()
    result = await install_generated_plugin(backend, {"location": "../escape"}, plugin_output_root=str(tmp_path))
    assert result["stage"] == "read"


@pytest.mark.asyncio
async def test_missing_server_entry_file(tmp_path):
    (tmp_path / "acme_pool").mkdir()
    backend = FakeBackend()
    result = await install_generated_plugin(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))
    assert result["stage"] == "read"
    assert not backend.register_calls


@pytest.mark.asyncio
async def test_malformed_server_entry_json(tmp_path):
    plugin_dir = tmp_path / "acme_pool"
    plugin_dir.mkdir()
    (plugin_dir / "server_entry.json").write_text("{not valid json")
    backend = FakeBackend()
    result = await install_generated_plugin(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))
    assert result["stage"] == "read"


@pytest.mark.asyncio
async def test_register_failure_reported_distinctly(tmp_path):
    _write_server_entry(tmp_path / "acme_pool")
    backend = FakeBackend()
    backend.register_response = {"successful": False}
    result = await install_generated_plugin(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))
    assert result["stage"] == "register"
    assert not backend.install_calls


@pytest.mark.asyncio
async def test_install_failure_reported_distinctly(tmp_path):
    _write_server_entry(tmp_path / "acme_pool")
    backend = FakeBackend()
    backend.install_response = None
    result = await install_generated_plugin(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))
    assert result["stage"] == "install"
    assert not backend.plugin_ops_calls


@pytest.mark.asyncio
async def test_install_without_profile_num_is_an_install_failure(tmp_path):
    _write_server_entry(tmp_path / "acme_pool")
    backend = FakeBackend()
    backend.install_response = {"successful": True, "data": {}}
    result = await install_generated_plugin(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))
    assert result["stage"] == "install"


@pytest.mark.asyncio
async def test_installed_plugin_not_found_in_list_is_an_install_failure(tmp_path):
    _write_server_entry(tmp_path / "acme_pool")
    backend = FakeBackend()
    backend.installed_response = {"successful": True, "data": []}
    result = await install_generated_plugin(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))
    assert result["stage"] == "install"
    assert not backend.plugin_ops_calls


@pytest.mark.asyncio
async def test_start_failure_reported_distinctly(tmp_path):
    _write_server_entry(tmp_path / "acme_pool")
    backend = FakeBackend()
    backend.plugin_ops_response = {"successful": False}
    result = await install_generated_plugin(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))
    assert result["stage"] == "start"


@pytest.mark.asyncio
async def test_full_success(tmp_path):
    entry = {"name": "AcmePool", "type": "python3", "path": str(tmp_path / "acme_pool"), "executable": "main.py", "runAs": "eisyai"}
    _write_server_entry(tmp_path / "acme_pool", entry)
    backend = FakeBackend()

    result = await install_generated_plugin(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

    assert result == {"location": "acme_pool", "nsid": "local.acme_pool", "plugin_id": 7, "name": "AcmePool", "started": True}
    assert backend.register_calls == [entry]
    assert backend.install_calls == [("local.acme_pool", None)]
    assert backend.plugin_ops_calls == [(7, "start")]
