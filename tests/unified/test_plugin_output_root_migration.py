"""plugin_output_root_migration.py -- automatic --plugin-output-root
relocation. FakeBackend is a plain duck-typed stand-in (not a full
NuCoreInterface subclass), mirroring tests/unified/plugin_authoring/
test_install.py's own FakeBackend pattern, extended with
uninstall_installed_plugin/install_local_plugin/plugin_ops call tracking
and failure-injection hooks needed to exercise the reinstall sequence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from unified.plugin_authoring.handlers import dev_venv
from unified.plugin_output_root_migration import (
    MigrationResult,
    build_plan,
    copy_plugin_dir,
    log_migration_result,
    migrate_plugin_output_root,
    rewrite_server_entry_paths,
)


class FakeBackend:
    def __init__(self):
        self.installed: list[dict] = []  # simulates the hub's live install-slot state
        self.update_calls: list[tuple] = []
        self.uninstall_calls: list[int] = []
        self.install_calls: list[tuple] = []
        self.plugin_ops_calls: list[tuple] = []
        self.notify_calls: list[list[str]] = []
        self.refresh_calls = 0

        self.update_response = {"successful": True, "data": {}}
        self.refresh_response = {"successful": True}
        self.notify_response = {"successful": True}

        self.uninstall_should_fail_for: set[int] = set()
        self.install_fail_count_for: dict[int, int] = {}
        self.reinstall_new_profile_num_for: dict[str, int] = {}
        self.reinstall_missing_for: set[str] = set()
        self.plugin_ops_should_fail_for: set[tuple[int, str]] = set()

    async def update_local_plugin(self, nsid, entry):
        self.update_calls.append((nsid, entry))
        return self.update_response

    async def get_installed_plugins(self):
        return {"successful": True, "data": [dict(p) for p in self.installed]}

    async def uninstall_installed_plugin(self, profile_num):
        self.uninstall_calls.append(profile_num)
        if profile_num in self.uninstall_should_fail_for:
            return {"successful": False}
        self.installed = [p for p in self.installed if p["profileNum"] != profile_num]
        return {"successful": True}

    async def install_local_plugin(self, nsid, profile_num=None):
        self.install_calls.append((nsid, profile_num))
        remaining = self.install_fail_count_for.get(profile_num, 0)
        if remaining > 0:
            self.install_fail_count_for[profile_num] = remaining - 1
            return {"successful": False}
        if nsid in self.reinstall_missing_for:
            return {"successful": True, "data": {"profileNum": profile_num}}
        reported = self.reinstall_new_profile_num_for.get(nsid, profile_num)
        self.installed.append({"profileNum": reported, "nsid": nsid, "name": nsid, "state": "starting"})
        return {"successful": True, "data": {"profileNum": reported}}

    async def plugin_ops(self, plugin_id, operation):
        self.plugin_ops_calls.append((plugin_id, operation))
        if (plugin_id, operation) in self.plugin_ops_should_fail_for:
            return {"successful": False}
        for p in self.installed:
            if p["profileNum"] == plugin_id:
                p["state"] = "connected" if operation == "start" else "stopped" if operation == "stop" else p["state"]
        return {"successful": True}

    async def notify_plugin_files_changed(self, paths):
        self.notify_calls.append(paths)
        return self.notify_response

    async def refresh_eisy_ai_permissions(self):
        self.refresh_calls += 1
        return self.refresh_response


def _write_plugin_dir(root, location, *, nsid=None, with_venv=False, extra_files=None):
    plugin_dir = root / location
    plugin_dir.mkdir(parents=True, exist_ok=True)
    entry = {"name": location, "type": "python3", "path": str(plugin_dir), "executable": "main.py", "runAs": "eisyai"}
    if nsid:
        entry["nsid"] = nsid
    (plugin_dir / "server_entry.json").write_text(json.dumps(entry))
    (plugin_dir / "plugin.py").write_text("# plugin code\n")
    if with_venv:
        venv_dir = plugin_dir / ".venv" / "bin"
        venv_dir.mkdir(parents=True)
        (venv_dir / "python3").write_text("#!/old/path/.venv/bin/python3\n")
        (plugin_dir / "requirements.txt").write_text("udi_interface>=3.0.57\n")
        install_script = plugin_dir / "install.sh"
        install_script.write_text("#!/usr/bin/env bash\n.venv/bin/pip3 install -r requirements.txt\n")
        install_script.chmod(0o755)
    for name, content in (extra_files or {}).items():
        (plugin_dir / name).write_text(content)
    return plugin_dir


# --- build_plan ---


def test_build_plan_reads_nsid_and_venv_without_mutating_anything(tmp_path):
    old_root = tmp_path / "old"
    _write_plugin_dir(old_root, "registered", nsid="local.acme")
    _write_plugin_dir(old_root, "unregistered")
    _write_plugin_dir(old_root, "with_venv", nsid="local.other", with_venv=True)
    before = {p: p.read_bytes() for p in old_root.rglob("*") if p.is_file()}

    plan = build_plan(old_root)

    by_location = {p.location: p for p in plan}
    assert by_location["registered"].nsid == "local.acme"
    assert by_location["unregistered"].nsid is None
    assert by_location["with_venv"].nsid == "local.other"
    assert by_location["with_venv"].has_venv is True
    assert by_location["registered"].has_venv is False
    after = {p: p.read_bytes() for p in old_root.rglob("*") if p.is_file()}
    assert before == after


def test_build_plan_on_missing_root_is_empty():
    assert build_plan(Path("/no/such/dir")) == []


# --- copy_plugin_dir ---


def test_copy_plugin_dir_fresh_copy(tmp_path):
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    src = _write_plugin_dir(old_root, "acme")

    status = copy_plugin_dir(src, new_root, copy_venv=False)

    assert status == "copied"
    assert (new_root / "acme" / "server_entry.json").is_file()
    assert not (new_root / "acme.migrating-tmp").exists()


def test_copy_plugin_dir_skips_venv_by_default(tmp_path):
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    src = _write_plugin_dir(old_root, "acme", with_venv=True)

    status = copy_plugin_dir(src, new_root, copy_venv=False)

    assert status == "copied"
    assert not (new_root / "acme" / ".venv").exists()


def test_copy_plugin_dir_copies_venv_when_asked(tmp_path):
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    src = _write_plugin_dir(old_root, "acme", with_venv=True)

    status = copy_plugin_dir(src, new_root, copy_venv=True)

    assert status == "copied"
    assert (new_root / "acme" / ".venv" / "bin" / "python3").is_file()


def test_copy_plugin_dir_is_a_noop_when_already_migrated(tmp_path):
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    src = _write_plugin_dir(old_root, "acme")
    copy_plugin_dir(src, new_root, copy_venv=False)

    status = copy_plugin_dir(src, new_root, copy_venv=False)

    assert status == "already-migrated"


def test_copy_plugin_dir_refuses_to_clobber_a_mismatched_destination(tmp_path):
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    src = _write_plugin_dir(old_root, "acme")
    _write_plugin_dir(new_root, "acme", extra_files={"unrelated.txt": "not from the migration"})

    status = copy_plugin_dir(src, new_root, copy_venv=False)

    assert status.startswith("error:")
    assert (new_root / "acme" / "unrelated.txt").is_file()  # untouched


def test_copy_plugin_dir_discards_a_stray_migrating_tmp_and_redoes_it(tmp_path):
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    src = _write_plugin_dir(old_root, "acme")
    new_root.mkdir()
    stray = new_root / "acme.migrating-tmp"
    stray.mkdir()
    (stray / "leftover.txt").write_text("from a prior crash")

    status = copy_plugin_dir(src, new_root, copy_venv=False)

    assert status == "copied"
    assert not stray.exists()
    assert (new_root / "acme" / "server_entry.json").is_file()


# --- rewrite_server_entry_paths ---


def test_rewrite_server_entry_paths_corrects_nsid_and_non_nsid_entries(tmp_path):
    new_root = tmp_path / "new"
    _write_plugin_dir(new_root, "registered", nsid="local.acme")
    _write_plugin_dir(new_root, "unregistered")

    rewrite_server_entry_paths(new_root)

    registered_entry = json.loads((new_root / "registered" / "server_entry.json").read_text())
    unregistered_entry = json.loads((new_root / "unregistered" / "server_entry.json").read_text())
    assert registered_entry["path"] == str(new_root / "registered")
    assert registered_entry["nsid"] == "local.acme"
    assert unregistered_entry["path"] == str(new_root / "unregistered")


def test_rewrite_server_entry_paths_is_a_noop_when_already_correct(tmp_path):
    new_root = tmp_path / "new"
    plugin_dir = new_root / "acme"
    plugin_dir.mkdir(parents=True)
    entry_path = plugin_dir / "server_entry.json"
    entry_path.write_text(json.dumps({"name": "acme", "path": str(plugin_dir)}))
    before = entry_path.read_bytes()

    rewrite_server_entry_paths(new_root)

    assert entry_path.read_bytes() == before


# --- migrate_plugin_output_root: end to end ---


@pytest.mark.asyncio
async def test_old_root_missing_is_a_clean_noop(tmp_path):
    backend = FakeBackend()
    result = await migrate_plugin_output_root(backend, tmp_path / "missing", tmp_path / "new")

    assert result.plugins == []
    assert result.renamed_to is None
    assert backend.update_calls == []


@pytest.mark.asyncio
async def test_dry_old_root_with_registered_but_never_installed_plugin_touches_no_hub_install_calls(tmp_path):
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    _write_plugin_dir(old_root, "acme", nsid="local.acme")
    backend = FakeBackend()  # not in backend.installed -- never installed

    result = await migrate_plugin_output_root(backend, old_root, new_root)

    assert result.success is True
    assert backend.update_calls and backend.update_calls[0][0] == "local.acme"
    assert backend.uninstall_calls == []
    assert backend.install_calls == []
    assert backend.plugin_ops_calls == []
    assert result.renamed_to == old_root.with_name("old.old")
    assert not old_root.exists()
    assert result.renamed_to.exists()


@pytest.mark.asyncio
async def test_happy_path_running_stopped_and_unregistered_plugins(tmp_path):
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    _write_plugin_dir(old_root, "running_plugin", nsid="local.running")
    _write_plugin_dir(old_root, "stopped_plugin", nsid="local.stopped")
    _write_plugin_dir(old_root, "never_installed", nsid="local.never")

    backend = FakeBackend()
    backend.installed = [
        {"profileNum": 1, "nsid": "local.running", "name": "running_plugin", "state": "connected"},
        {"profileNum": 2, "nsid": "local.stopped", "name": "stopped_plugin", "state": "stopped"},
    ]

    result = await migrate_plugin_output_root(backend, old_root, new_root)

    assert result.success is True
    assert set(backend.uninstall_calls) == {1, 2}
    assert {c[1] for c in backend.install_calls} == {1, 2}
    assert ("local.running", 1) in [(c[0], c[1]) for c in backend.install_calls]
    assert (1, "start") in backend.plugin_ops_calls
    assert (2, "stop") in backend.plugin_ops_calls
    assert (1, "stop") not in backend.plugin_ops_calls
    assert (2, "start") not in backend.plugin_ops_calls
    # never_installed only gets its registration updated, nothing else
    never_calls = [c for c in backend.install_calls if c[0] == "local.never"]
    assert never_calls == []

    for outcome in result.plugins:
        assert outcome.ok, outcome
    running = next(p for p in result.plugins if p.location == "running_plugin")
    stopped = next(p for p in result.plugins if p.location == "stopped_plugin")
    assert running.was_running is True and running.run_state_restored is True
    assert stopped.was_running is False and stopped.run_state_restored is True
    assert result.renamed_to is not None
    assert not old_root.exists()


@pytest.mark.asyncio
async def test_reinstall_retries_install_once_on_transient_failure(tmp_path):
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    _write_plugin_dir(old_root, "acme", nsid="local.acme")
    backend = FakeBackend()
    backend.installed = [{"profileNum": 5, "nsid": "local.acme", "name": "acme", "state": "connected"}]
    backend.install_fail_count_for[5] = 1  # fails once, succeeds on retry

    result = await migrate_plugin_output_root(backend, old_root, new_root)

    assert result.success is True
    assert len([c for c in backend.install_calls if c[1] == 5]) == 2
    assert result.renamed_to is not None


@pytest.mark.asyncio
async def test_reinstall_hard_failure_after_uninstall_is_critical_and_blocks_rename(tmp_path):
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    _write_plugin_dir(old_root, "acme", nsid="local.acme")
    _write_plugin_dir(old_root, "other", nsid="local.other")
    backend = FakeBackend()
    backend.installed = [
        {"profileNum": 5, "nsid": "local.acme", "name": "acme", "state": "connected"},
        {"profileNum": 6, "nsid": "local.other", "name": "other", "state": "connected"},
    ]
    backend.install_fail_count_for[5] = 99  # always fails, even after retry

    result = await migrate_plugin_output_root(backend, old_root, new_root)

    assert result.success is False
    assert result.renamed_to is None
    assert old_root.exists()
    failed = next(p for p in result.plugins if p.location == "acme")
    other = next(p for p in result.plugins if p.location == "other")
    assert failed.reinstall_error is not None
    assert not failed.ok
    # the other plugin still gets fully processed despite acme's failure
    assert other.ok
    assert (6, "start") in backend.plugin_ops_calls


@pytest.mark.asyncio
async def test_reinstall_into_a_different_profile_num_is_treated_as_a_hard_failure(tmp_path):
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    _write_plugin_dir(old_root, "acme", nsid="local.acme")
    backend = FakeBackend()
    backend.installed = [{"profileNum": 5, "nsid": "local.acme", "name": "acme", "state": "connected"}]
    backend.reinstall_new_profile_num_for["local.acme"] = 999  # simulate a slot mismatch

    result = await migrate_plugin_output_root(backend, old_root, new_root)

    assert result.success is False
    outcome = result.plugins[0]
    assert outcome.reinstall_error is not None
    assert "999" in outcome.reinstall_error or "profile_num" in outcome.reinstall_error


@pytest.mark.asyncio
async def test_stop_restoration_failure_is_a_warning_not_a_hard_failure(tmp_path):
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    _write_plugin_dir(old_root, "acme", nsid="local.acme")
    backend = FakeBackend()
    backend.installed = [{"profileNum": 5, "nsid": "local.acme", "name": "acme", "state": "stopped"}]
    backend.plugin_ops_should_fail_for.add((5, "stop"))

    result = await migrate_plugin_output_root(backend, old_root, new_root)

    outcome = result.plugins[0]
    assert outcome.run_state_error is not None
    assert outcome.ok is True  # does not gate overall success
    assert result.success is True
    assert result.renamed_to is not None


@pytest.mark.asyncio
async def test_permissions_refresh_failure_blocks_rename(tmp_path):
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    _write_plugin_dir(old_root, "acme")
    backend = FakeBackend()
    backend.refresh_response = {"error": "refresh failed"}

    result = await migrate_plugin_output_root(backend, old_root, new_root)

    assert result.success is False
    assert result.renamed_to is None
    assert old_root.exists()


@pytest.mark.asyncio
async def test_registration_update_failure_is_per_plugin_and_does_not_abort_others(tmp_path):
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    _write_plugin_dir(old_root, "bad", nsid="local.bad")
    _write_plugin_dir(old_root, "good", nsid="local.good")
    backend = FakeBackend()

    async def flaky_update_local_plugin(nsid, entry):
        backend.update_calls.append((nsid, entry))
        if nsid == "local.bad":
            return {"successful": False}
        return {"successful": True, "data": {}}

    backend.update_local_plugin = flaky_update_local_plugin

    result = await migrate_plugin_output_root(backend, old_root, new_root)

    assert result.success is False
    bad = next(p for p in result.plugins if p.location == "bad")
    good = next(p for p in result.plugins if p.location == "good")
    assert bad.registration_error is not None
    assert good.ok


@pytest.mark.asyncio
async def test_rerun_after_partial_failure_resumes_and_eventually_succeeds(tmp_path):
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    _write_plugin_dir(old_root, "acme", nsid="local.acme")
    backend = FakeBackend()
    backend.installed = [{"profileNum": 5, "nsid": "local.acme", "name": "acme", "state": "connected"}]
    backend.install_fail_count_for[5] = 99  # fails this run

    first = await migrate_plugin_output_root(backend, old_root, new_root)
    assert first.success is False
    assert old_root.exists()

    backend.install_fail_count_for[5] = 0  # fixed for the retry
    second = await migrate_plugin_output_root(backend, old_root, new_root)

    assert second.success is True
    assert second.renamed_to is not None
    assert not old_root.exists()
    # the plugin dir wasn't re-copied the second time -- already matched
    assert second.plugins[0].copy_status == "already-migrated"


@pytest.mark.asyncio
async def test_old_root_old_name_collision_leaves_old_root_in_place(tmp_path):
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    _write_plugin_dir(old_root, "acme")
    (tmp_path / "old.old").mkdir()  # pre-existing collision
    backend = FakeBackend()

    result = await migrate_plugin_output_root(backend, old_root, new_root)

    assert result.success is True  # migration itself succeeded
    assert result.renamed_to is None  # but the rename step was skipped
    assert old_root.exists()


# --- .venv recreation ---


@pytest.mark.asyncio
async def test_plugin_with_venv_gets_it_recreated_via_setup_dev_venv(tmp_path, monkeypatch):
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    _write_plugin_dir(old_root, "acme", with_venv=True)
    backend = FakeBackend()

    calls = []

    async def fake_run_shell_command(nucore_interface, args):
        calls.append(args)
        venv_bin = new_root / "acme" / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python3").write_text("#!/bin/sh\n")
        return {"exit_code": 0, "stdout": "installed", "stderr": ""}

    monkeypatch.setattr(dev_venv.shell, "run_shell_command", fake_run_shell_command)

    result = await migrate_plugin_output_root(backend, old_root, new_root)

    outcome = result.plugins[0]
    assert outcome.venv_recreated is True
    assert outcome.venv_error is None
    assert len(calls) == 1
    assert calls[0]["cwd"] == str(new_root / "acme")
    assert "--clear" in calls[0]["command"]
    assert "install.sh" in calls[0]["command"]
    assert result.success is True


@pytest.mark.asyncio
async def test_venv_recreation_failure_is_a_warning_not_a_hard_failure(tmp_path, monkeypatch):
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    _write_plugin_dir(old_root, "acme", with_venv=True)
    backend = FakeBackend()

    async def fake_run_shell_command(nucore_interface, args):
        return {"exit_code": 1, "stdout": "", "stderr": "pip: command not found"}

    monkeypatch.setattr(dev_venv.shell, "run_shell_command", fake_run_shell_command)

    result = await migrate_plugin_output_root(backend, old_root, new_root)

    outcome = result.plugins[0]
    assert outcome.venv_error is not None
    assert outcome.venv_recreated is None
    assert outcome.ok is True  # does not gate overall success
    assert result.success is True
    assert result.renamed_to is not None


@pytest.mark.asyncio
async def test_venv_not_recreated_when_copy_failed(tmp_path, monkeypatch):
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    _write_plugin_dir(old_root, "acme", with_venv=True)
    _write_plugin_dir(new_root, "acme", extra_files={"unrelated.txt": "not from the migration"})
    backend = FakeBackend()

    async def fail_if_called(nucore_interface, args):
        raise AssertionError("setup_dev_venv should not run when the copy itself failed")

    monkeypatch.setattr(dev_venv.shell, "run_shell_command", fail_if_called)

    result = await migrate_plugin_output_root(backend, old_root, new_root)

    outcome = result.plugins[0]
    assert outcome.copy_status.startswith("error")
    assert outcome.venv_recreated is None
    assert outcome.venv_error is None


def test_log_migration_result_does_not_raise_on_either_outcome(tmp_path):
    empty = MigrationResult(old_root=tmp_path / "old", new_root=tmp_path / "new")
    log_migration_result(empty)  # old_root never existed -- should be a clean no-op path

    non_empty = MigrationResult(old_root=tmp_path / "old", new_root=tmp_path / "new")
    non_empty.plugins = list(build_plan(tmp_path)) or []
    non_empty.permissions_refresh_error = "boom"
    log_migration_result(non_empty)  # failure path should also just log, not raise
