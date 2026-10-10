"""setup_github_repo -- one-time version-control bootstrap (git init,
.gitignore, first commit, remote create/link, first push). Real git
commands are allowed to run for real against tmp_path (fast, local,
no network) via unified.handlers.shell.run_shell_command's actual subprocess
machinery -- only `gh ...` invocations are intercepted (no dependency on a
real gh CLI/network/auth in tests). A real local bare repo under a second
tmp_path stands in for "GitHub" for push-path coverage.
"""

from __future__ import annotations

import subprocess

import pytest

import unified.handlers.shell as real_shell
from unified.plugin_authoring.handlers import github_repo
from unified.plugin_authoring.handlers.github_repo import setup_github_repo

# Captured before any test monkeypatches github_repo.shell.run_shell_command --
# that attribute lives on this same module object, so a late lookup of
# real_shell.run_shell_command would see the patched version too.
_REAL_RUN_SHELL_COMMAND = real_shell.run_shell_command


def _make_plugin_dir(tmp_path, name="acme_pool"):
    plugin_dir = tmp_path / name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.py").write_text("# generated plugin\n")
    return plugin_dir


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def _intercept_gh(gh_result):
    """A shell.run_shell_command replacement that answers `gh ...` commands
    with *gh_result* and runs everything else (git) for real."""

    async def fake_run_shell_command(nucore_interface, args):
        if args["command"].startswith("gh "):
            return gh_result
        return await _REAL_RUN_SHELL_COMMAND(nucore_interface, args)

    return fake_run_shell_command


@pytest.fixture(autouse=True)
def _no_real_gh(monkeypatch):
    # Default for every test unless overridden: `gh` is unavailable/unauthenticated.
    monkeypatch.setattr(
        github_repo.shell,
        "run_shell_command",
        _intercept_gh({"exit_code": 1, "stdout": "", "stderr": "gh: not authenticated"}),
    )


@pytest.mark.asyncio
async def test_requires_location(tmp_path):
    result = await setup_github_repo(None, {}, plugin_output_root=str(tmp_path))
    assert "error" in result


@pytest.mark.asyncio
async def test_rejects_path_traversal(tmp_path):
    result = await setup_github_repo(None, {"location": "../escape"}, plugin_output_root=str(tmp_path))
    assert "error" in result


@pytest.mark.asyncio
async def test_missing_plugin_directory(tmp_path):
    result = await setup_github_repo(None, {"location": "nope"}, plugin_output_root=str(tmp_path))
    assert "error" in result


@pytest.mark.asyncio
async def test_rejects_invalid_visibility(tmp_path):
    _make_plugin_dir(tmp_path)
    result = await setup_github_repo(
        None, {"location": "acme_pool", "visibility": "public-ish", "push": False}, plugin_output_root=str(tmp_path)
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_gitignore_created_and_secret_file_excluded_from_first_commit(tmp_path):
    plugin_dir = _make_plugin_dir(tmp_path)
    (plugin_dir / ".iox_env").write_text("PG3INIT=super-secret-token\n")

    result = await setup_github_repo(
        None,
        {"location": "acme_pool", "push": False, "author_name": "Dev", "author_email": "dev@example.com"},
        plugin_output_root=str(tmp_path),
    )

    assert result["gitignore_updated"] is True
    assert result["git_initialized"] is True
    assert result["committed"] is True

    gitignore_text = (plugin_dir / ".gitignore").read_text()
    for line in (".venv/", "__pycache__/", "*.pyc", "persist/", "data/", ".iox_env"):
        assert line in gitignore_text.splitlines()

    tracked = _git(plugin_dir, "ls-files").stdout.splitlines()
    assert ".iox_env" not in tracked
    assert "plugin.py" in tracked
    assert ".gitignore" in tracked

    author = _git(plugin_dir, "log", "-1", "--pretty=%an <%ae>").stdout.strip()
    assert author == "Dev <dev@example.com>"


@pytest.mark.asyncio
async def test_default_author_used_when_not_given(tmp_path):
    plugin_dir = _make_plugin_dir(tmp_path)

    await setup_github_repo(None, {"location": "acme_pool", "push": False}, plugin_output_root=str(tmp_path))

    author = _git(plugin_dir, "log", "-1", "--pretty=%an <%ae>").stdout.strip()
    assert author == "NuCore Plugin Author <plugin-author@users.noreply.github.com>"


@pytest.mark.asyncio
async def test_gitignore_merge_preserves_existing_custom_lines(tmp_path):
    plugin_dir = _make_plugin_dir(tmp_path)
    (plugin_dir / ".gitignore").write_text("my_custom_ignore.txt\n")

    result = await setup_github_repo(None, {"location": "acme_pool", "push": False}, plugin_output_root=str(tmp_path))

    assert result["gitignore_updated"] is True
    gitignore_lines = (plugin_dir / ".gitignore").read_text().splitlines()
    assert "my_custom_ignore.txt" in gitignore_lines
    assert ".iox_env" in gitignore_lines


@pytest.mark.asyncio
async def test_second_call_with_no_changes_reports_no_changes_not_an_error(tmp_path):
    _make_plugin_dir(tmp_path)

    first = await setup_github_repo(None, {"location": "acme_pool", "push": False}, plugin_output_root=str(tmp_path))
    assert first["committed"] is True

    second = await setup_github_repo(None, {"location": "acme_pool", "push": False}, plugin_output_root=str(tmp_path))
    assert second["git_initialized"] is False
    assert second["gitignore_updated"] is False
    assert second["committed"] is False
    assert second["reason"] == "no changes"


@pytest.mark.asyncio
async def test_remote_url_links_origin_without_gh(tmp_path):
    plugin_dir = _make_plugin_dir(tmp_path)
    remote_dir = tmp_path / "remote.git"
    remote_dir.mkdir()
    _git(remote_dir, "init", "--bare")

    result = await setup_github_repo(
        None,
        {"location": "acme_pool", "remote_url": str(remote_dir), "push": False},
        plugin_output_root=str(tmp_path),
    )

    assert result["remote_url"] == str(remote_dir)
    assert result["pushed"] is False
    assert _git(plugin_dir, "remote", "get-url", "origin").stdout.strip() == str(remote_dir)


@pytest.mark.asyncio
async def test_push_true_pushes_to_the_linked_remote(tmp_path):
    plugin_dir = _make_plugin_dir(tmp_path)
    remote_dir = tmp_path / "remote.git"
    remote_dir.mkdir()
    _git(remote_dir, "init", "--bare")

    result = await setup_github_repo(
        None, {"location": "acme_pool", "remote_url": str(remote_dir), "push": True}, plugin_output_root=str(tmp_path)
    )

    assert result["pushed"] is True
    remote_branches = _git(remote_dir, "branch", "--list").stdout
    assert "main" in remote_branches


@pytest.mark.asyncio
async def test_second_remote_url_call_updates_existing_origin(tmp_path):
    plugin_dir = _make_plugin_dir(tmp_path)
    first_remote = tmp_path / "first.git"
    first_remote.mkdir()
    _git(first_remote, "init", "--bare")
    second_remote = tmp_path / "second.git"
    second_remote.mkdir()
    _git(second_remote, "init", "--bare")

    await setup_github_repo(
        None, {"location": "acme_pool", "remote_url": str(first_remote), "push": False}, plugin_output_root=str(tmp_path)
    )
    result = await setup_github_repo(
        None, {"location": "acme_pool", "remote_url": str(second_remote), "push": False}, plugin_output_root=str(tmp_path)
    )

    assert result["remote_url"] == str(second_remote)
    assert _git(plugin_dir, "remote", "get-url", "origin").stdout.strip() == str(second_remote)


@pytest.mark.asyncio
async def test_gh_unavailable_returns_non_fatal_warning(tmp_path):
    _make_plugin_dir(tmp_path)

    result = await setup_github_repo(None, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

    assert result["committed"] is True
    assert result["remote_url"] is None
    assert result["pushed"] is False
    assert "warning" in result
    assert "remote_url" in result["warning"]


@pytest.mark.asyncio
async def test_gh_repo_create_used_when_authenticated_and_no_remote_url(tmp_path, monkeypatch):
    _make_plugin_dir(tmp_path)

    calls = []

    async def fake_run_shell_command(nucore_interface, args):
        calls.append(args["command"])
        if args["command"] == "gh auth status":
            return {"exit_code": 0, "stdout": "", "stderr": ""}
        if args["command"].startswith("gh repo create"):
            return {"exit_code": 0, "stdout": "https://github.com/example/acme_pool\n", "stderr": ""}
        return await _REAL_RUN_SHELL_COMMAND(nucore_interface, args)

    monkeypatch.setattr(github_repo.shell, "run_shell_command", fake_run_shell_command)

    result = await setup_github_repo(None, {"location": "acme_pool", "push": False}, plugin_output_root=str(tmp_path))

    assert result["remote_url"] == "https://github.com/example/acme_pool"
    assert any(c.startswith("gh repo create") for c in calls)
