"""install_generated_plugin (design/developers/plugin_authoring_p4_impl.md
Stage 3) -- register -> install -> start, each stage's failure reported
distinctly via a 'stage' key. FakeBackend is a plain duck-typed stand-in
(not a full NuCoreInterface subclass) since these handlers only ever call
a handful of its methods.

Also covers update_registered_plugin/delete_registered_plugin (how a
reported "conflict" gets resolved) -- all in this one file since they
share FakeBackend and _write_server_entry.
"""

from __future__ import annotations

import json

import pytest

from unified.plugin_authoring.handlers.install import (
    delete_registered_plugin,
    install_generated_plugin,
    update_registered_plugin,
)


class FakeBackend:
    def __init__(self):
        self.register_response = {"successful": True, "data": {"nsid": "local.acme_pool"}}
        self.install_response = {"successful": True, "data": {"profileNum": 7}}
        self.installed_response = {
            "successful": True,
            "data": [{"profileNum": 7, "nsid": "local.acme_pool", "name": "AcmePool", "state": "stopped"}],
        }
        self.store_response = {"successful": True, "data": []}
        self.update_response = {"successful": True, "data": {}}
        self.delete_response = {"successful": True}
        self.plugin_ops_response = {"successful": True, "data": {}}
        self.register_calls = []
        self.install_calls = []
        self.plugin_ops_calls = []
        self.store_calls = 0
        self.update_calls = []
        self.delete_calls = []

    async def register_local_plugin(self, entry):
        self.register_calls.append(entry)
        return self.register_response

    async def install_local_plugin(self, nsid, profile_num=None):
        self.install_calls.append((nsid, profile_num))
        return self.install_response

    async def get_installed_plugins(self):
        return self.installed_response

    async def get_local_store_plugins(self):
        self.store_calls += 1
        return self.store_response

    async def update_local_plugin(self, nsid, entry):
        self.update_calls.append((nsid, entry))
        return self.update_response

    async def delete_local_plugin(self, nsid):
        self.delete_calls.append(nsid)
        return self.delete_response

    async def plugin_ops(self, plugin_id, operation):
        self.plugin_ops_calls.append((plugin_id, operation))
        return self.plugin_ops_response


def _write_server_entry(plugin_dir, entry=None):
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "server_entry.json").write_text(
        json.dumps(entry or {"name": "AcmePool", "type": "python3", "path": str(plugin_dir), "executable": "main.py", "runAs": "eisyai"})
    )


# --- install_generated_plugin: read/register/install/start stages ---


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
async def test_installed_plugin_matched_by_nsid_not_profile_num(tmp_path):
    # profileNum alone is no longer the match key -- a list entry with the
    # right profileNum but a different (or missing) nsid must not match.
    _write_server_entry(tmp_path / "acme_pool")
    backend = FakeBackend()
    backend.installed_response = {
        "successful": True,
        "data": [{"profileNum": 7, "nsid": "local.someone-else", "name": "Other", "state": "stopped"}],
    }
    result = await install_generated_plugin(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))
    assert result["stage"] == "install"
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_profile_num_mismatch_between_install_response_and_installed_list_is_an_install_failure(tmp_path):
    _write_server_entry(tmp_path / "acme_pool")
    backend = FakeBackend()
    backend.installed_response = {
        "successful": True,
        "data": [{"profileNum": 99, "nsid": "local.acme_pool", "name": "AcmePool", "state": "stopped"}],
    }
    result = await install_generated_plugin(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))
    assert result["stage"] == "install"
    assert "inconsistency" in result["error"]
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


@pytest.mark.asyncio
async def test_nsid_is_read_from_register_response_not_guessed_from_location(tmp_path):
    # The host assigns nsid and returns it in the register response -- it's
    # never something install_generated_plugin invents from 'location'.
    _write_server_entry(tmp_path / "acme_pool")
    backend = FakeBackend()
    backend.register_response = {"successful": True, "data": {"nsid": "local.a-totally-different-nsid"}}
    backend.installed_response = {
        "successful": True,
        "data": [{"profileNum": 7, "nsid": "local.a-totally-different-nsid", "name": "AcmePool", "state": "stopped"}],
    }

    result = await install_generated_plugin(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

    assert result["nsid"] == "local.a-totally-different-nsid"
    assert backend.install_calls == [("local.a-totally-different-nsid", None)]


@pytest.mark.asyncio
async def test_register_response_missing_nsid_is_a_register_stage_failure(tmp_path):
    _write_server_entry(tmp_path / "acme_pool")
    backend = FakeBackend()
    backend.register_response = {"successful": True, "data": {}}

    result = await install_generated_plugin(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

    assert result["stage"] == "register"
    assert not backend.install_calls


# --- nsid persistence after a fresh successful registration ---


@pytest.mark.asyncio
async def test_nsid_is_persisted_into_server_entry_after_success(tmp_path):
    plugin_dir = tmp_path / "acme_pool"
    entry = {"name": "AcmePool", "type": "python3", "path": str(plugin_dir), "executable": "main.py", "runAs": "eisyai"}
    _write_server_entry(plugin_dir, entry)
    backend = FakeBackend()

    result = await install_generated_plugin(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

    assert "error" not in result
    on_disk = json.loads((plugin_dir / "server_entry.json").read_text())
    assert on_disk["nsid"] == "local.acme_pool"
    assert on_disk["name"] == "AcmePool"


# --- nsid-known conflict detection ---


@pytest.mark.asyncio
async def test_known_nsid_registered_conflict_is_reported_and_nothing_proceeds(tmp_path):
    plugin_dir = tmp_path / "acme_pool"
    _write_server_entry(plugin_dir, {"name": "AcmePool", "nsid": "local.acme_pool"})
    backend = FakeBackend()
    backend.store_response = {
        "successful": True,
        "data": [{"nsid": "local.acme_pool", "name": "AcmePool", "type": "python3", "path": str(plugin_dir), "updatedAt": "2026-01-01"}],
    }
    backend.installed_response = {"successful": True, "data": []}

    result = await install_generated_plugin(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

    assert result["conflict"] is True
    assert result["nsid"] == "local.acme_pool"
    assert result["registered"]["name"] == "AcmePool"
    assert result["installed"] is None
    assert not backend.register_calls
    assert not backend.install_calls


@pytest.mark.asyncio
async def test_known_nsid_installed_conflict_is_reported_and_nothing_proceeds(tmp_path):
    plugin_dir = tmp_path / "acme_pool"
    _write_server_entry(plugin_dir, {"name": "AcmePool", "nsid": "local.acme_pool"})
    backend = FakeBackend()
    backend.store_response = {"successful": True, "data": []}
    backend.installed_response = {
        "successful": True,
        "data": [{"profileNum": 7, "nsid": "local.acme_pool", "name": "AcmePool", "state": "running"}],
    }

    result = await install_generated_plugin(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

    assert result["conflict"] is True
    assert result["registered"] is None
    assert result["installed"] == {"plugin_id": 7, "name": "AcmePool", "state": "running"}
    assert not backend.register_calls


@pytest.mark.asyncio
async def test_known_nsid_conflict_in_both_lists_reports_both(tmp_path):
    plugin_dir = tmp_path / "acme_pool"
    _write_server_entry(plugin_dir, {"name": "AcmePool", "nsid": "local.acme_pool"})
    backend = FakeBackend()
    backend.store_response = {
        "successful": True,
        "data": [{"nsid": "local.acme_pool", "name": "AcmePool", "type": "python3", "path": str(plugin_dir), "updatedAt": "2026-01-01"}],
    }
    backend.installed_response = {
        "successful": True,
        "data": [{"profileNum": 7, "nsid": "local.acme_pool", "name": "AcmePool", "state": "running"}],
    }

    result = await install_generated_plugin(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

    assert result["conflict"] is True
    assert result["registered"] is not None
    assert result["installed"] is not None
    assert not backend.register_calls


@pytest.mark.asyncio
async def test_stale_known_nsid_falls_through_to_a_fresh_register(tmp_path):
    # The locally-known nsid matches nothing in either list (e.g. deleted
    # on the host out-of-band) -- proceed exactly as if no nsid were known.
    plugin_dir = tmp_path / "acme_pool"
    _write_server_entry(plugin_dir, {"name": "AcmePool", "nsid": "local.stale-nsid"})
    backend = FakeBackend()
    backend.store_response = {"successful": True, "data": []}
    backend.installed_response = {
        "successful": True,
        "data": [{"profileNum": 7, "nsid": "local.acme_pool", "name": "AcmePool", "state": "stopped"}],
    }

    result = await install_generated_plugin(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

    assert "error" not in result
    assert result["started"] is True
    assert backend.register_calls, "register_local_plugin should have been called"


@pytest.mark.asyncio
async def test_known_nsid_is_never_resent_in_the_register_wire_body(tmp_path):
    plugin_dir = tmp_path / "acme_pool"
    _write_server_entry(plugin_dir, {"name": "AcmePool", "nsid": "local.stale-nsid"})
    backend = FakeBackend()
    backend.store_response = {"successful": True, "data": []}
    backend.installed_response = {
        "successful": True,
        "data": [{"profileNum": 7, "nsid": "local.acme_pool", "name": "AcmePool", "state": "stopped"}],
    }

    await install_generated_plugin(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

    assert "nsid" not in backend.register_calls[0]


@pytest.mark.asyncio
async def test_no_known_nsid_skips_the_conflict_check_entirely(tmp_path):
    _write_server_entry(tmp_path / "acme_pool")  # no nsid in the file at all
    backend = FakeBackend()

    result = await install_generated_plugin(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

    assert "error" not in result
    assert backend.store_calls == 0


# --- update_registered_plugin ---


@pytest.mark.asyncio
async def test_update_requires_a_known_nsid(tmp_path):
    _write_server_entry(tmp_path / "acme_pool")  # no nsid
    backend = FakeBackend()
    result = await update_registered_plugin(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))
    assert "error" in result
    assert not backend.update_calls


@pytest.mark.asyncio
async def test_update_success_pushes_entry_and_repersists_nsid(tmp_path):
    plugin_dir = tmp_path / "acme_pool"
    _write_server_entry(plugin_dir, {"name": "AcmePool", "nsid": "local.acme_pool", "desc": "updated desc"})
    backend = FakeBackend()

    result = await update_registered_plugin(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

    assert result == {"location": "acme_pool", "nsid": "local.acme_pool", "updated": True}
    assert backend.update_calls == [("local.acme_pool", {"name": "AcmePool", "desc": "updated desc"})]
    on_disk = json.loads((plugin_dir / "server_entry.json").read_text())
    assert on_disk["nsid"] == "local.acme_pool"
    assert on_disk["desc"] == "updated desc"


@pytest.mark.asyncio
async def test_update_failure_reported_distinctly(tmp_path):
    _write_server_entry(tmp_path / "acme_pool", {"name": "AcmePool", "nsid": "local.acme_pool"})
    backend = FakeBackend()
    backend.update_response = {"successful": False}

    result = await update_registered_plugin(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

    assert "error" in result


# --- delete_registered_plugin ---


@pytest.mark.asyncio
async def test_delete_requires_a_known_nsid(tmp_path):
    _write_server_entry(tmp_path / "acme_pool")  # no nsid
    backend = FakeBackend()
    result = await delete_registered_plugin(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))
    assert "error" in result
    assert not backend.delete_calls


@pytest.mark.asyncio
async def test_delete_success_clears_nsid_from_disk(tmp_path):
    plugin_dir = tmp_path / "acme_pool"
    _write_server_entry(plugin_dir, {"name": "AcmePool", "nsid": "local.acme_pool"})
    backend = FakeBackend()

    result = await delete_registered_plugin(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

    assert result == {"location": "acme_pool", "nsid": "local.acme_pool", "deleted": True}
    assert backend.delete_calls == ["local.acme_pool"]
    on_disk = json.loads((plugin_dir / "server_entry.json").read_text())
    assert "nsid" not in on_disk
    assert on_disk["name"] == "AcmePool"


@pytest.mark.asyncio
async def test_delete_failure_reported_distinctly(tmp_path):
    plugin_dir = tmp_path / "acme_pool"
    _write_server_entry(plugin_dir, {"name": "AcmePool", "nsid": "local.acme_pool"})
    backend = FakeBackend()
    backend.delete_response = {"successful": False}

    result = await delete_registered_plugin(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

    assert "error" in result
    on_disk = json.loads((plugin_dir / "server_entry.json").read_text())
    assert on_disk["nsid"] == "local.acme_pool", "nsid must not be cleared locally unless the host delete actually succeeded"


@pytest.mark.asyncio
async def test_delete_requires_location(tmp_path):
    backend = FakeBackend()
    result = await delete_registered_plugin(backend, {}, plugin_output_root=str(tmp_path))
    assert "error" in result


@pytest.mark.asyncio
async def test_update_requires_location(tmp_path):
    backend = FakeBackend()
    result = await update_registered_plugin(backend, {}, plugin_output_root=str(tmp_path))
    assert "error" in result
