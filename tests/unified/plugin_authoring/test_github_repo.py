"""setup_github_repo -- one-time version-control bootstrap (git init,
.gitignore, first commit, remote link, first push). Plain git only -- it
never shells out to any GitHub-specific CLI or API to create a repository,
so every command here is real git, run for real against tmp_path (fast,
local, no network) via unified.handlers.shell.run_shell_command's actual
subprocess machinery -- no interception needed. A real local bare repo
under a second tmp_path stands in for "GitHub" for push-path coverage.
"""

from __future__ import annotations

import subprocess

import pytest

from unified.plugin_authoring import developer_config
from unified.plugin_authoring.handlers.github_repo import setup_github_repo


def _make_plugin_dir(tmp_path, name="acme_pool"):
    plugin_dir = tmp_path / name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.py").write_text("# generated plugin\n")
    return plugin_dir


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


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
async def test_commissioned_developer_identity_used_when_author_not_given(tmp_path):
    plugin_dir = _make_plugin_dir(tmp_path)
    developer_config.write_developer_config(str(tmp_path), email="real.dev@example.com", name="Real Dev")

    await setup_github_repo(None, {"location": "acme_pool", "push": False}, plugin_output_root=str(tmp_path))

    author = _git(plugin_dir, "log", "-1", "--pretty=%an <%ae>").stdout.strip()
    assert author == "Real Dev <real.dev@example.com>"


@pytest.mark.asyncio
async def test_explicit_author_overrides_commissioned_developer_identity(tmp_path):
    plugin_dir = _make_plugin_dir(tmp_path)
    developer_config.write_developer_config(str(tmp_path), email="real.dev@example.com", name="Real Dev")

    await setup_github_repo(
        None,
        {"location": "acme_pool", "push": False, "author_name": "Override", "author_email": "override@example.com"},
        plugin_output_root=str(tmp_path),
    )

    author = _git(plugin_dir, "log", "-1", "--pretty=%an <%ae>").stdout.strip()
    assert author == "Override <override@example.com>"


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
async def test_remote_url_links_origin(tmp_path):
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
async def test_no_remote_url_returns_non_fatal_warning_without_creating_one(tmp_path):
    _make_plugin_dir(tmp_path)

    result = await setup_github_repo(None, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

    assert result["committed"] is True
    assert result["remote_url"] is None
    assert result["pushed"] is False
    assert "warning" in result
    assert "remote_url" in result["warning"]
    assert "gh" not in result["warning"]


@pytest.mark.asyncio
async def test_never_shells_out_to_gh(tmp_path, monkeypatch):
    """Regression guard: setup_github_repo must only ever run plain `git`
    commands -- no GitHub-specific CLI, with or without a remote_url."""
    _make_plugin_dir(tmp_path)

    import unified.handlers.shell as real_shell

    real_run_shell_command = real_shell.run_shell_command
    calls = []

    async def tracking_run_shell_command(nucore_interface, args):
        calls.append(args["command"])
        return await real_run_shell_command(nucore_interface, args)

    import unified.plugin_authoring.handlers.github_repo as github_repo_module

    monkeypatch.setattr(github_repo_module.shell, "run_shell_command", tracking_run_shell_command)

    result = await setup_github_repo(None, {"location": "acme_pool", "push": False}, plugin_output_root=str(tmp_path))

    assert result["remote_url"] is None
    assert all(command.startswith("git") for command in calls)
    assert not any("gh " in command or command.startswith("gh") for command in calls)
