"""``setup_github_repo`` -- local/dev-testing only: one-time bootstrap of
version control for a generated plugin. Writes/merges a ``.gitignore`` that
always excludes ``.iox_env`` (``vscode_debug.py``'s ``_IOX_ENV_FILENAME`` --
holds the live ``PG3INIT`` MQTT credential in plaintext) before the first
commit ever runs, so that secret can never end up in a (possibly public)
GitHub repo regardless of what else is in the directory.

Idempotent by design, same spirit as ``setup_dev_venv``: a second call skips
``git init`` when ``.git`` already exists, skips remote creation when
``origin`` is already configured, and reports "no changes" rather than
erroring when there's nothing new to commit -- safe to call once right after
``generate_plugin_scaffold``/``install_generated_plugin`` and again later to
commit and push whatever changed since.

Everyday git work after this one-time setup -- stash, status, diff, log,
pull, later ad hoc commits/pushes -- goes through ``run_shell_command``
directly; this module only covers the one step with a real secret-leak edge
case, not a general git wrapper.

Reuses ``unified.handlers.shell.run_shell_command``'s subprocess machinery
(timeout, bounded output capture) rather than reimplementing it -- every
command run here is built server-side from validated/quoted arguments, not
raw LLM-supplied text, the same safe-internal-reuse reasoning ``dev_venv.py``
already documents for its own venv/pip invocation.

Identity (``user.name``/``user.email``) is passed via ``git -c`` flags, not
environment variables or a real ``~/.gitconfig`` -- ``run_shell_command``'s
child process gets a deliberately minimal environment with no ``HOME``, so a
global gitconfig is never read here.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from nucore import NuCoreInterface

from ...handlers import shell
from ..path_confinement import confine_path

_GITIGNORE_FILENAME = ".gitignore"
_REQUIRED_GITIGNORE_LINES = [
    ".venv/",
    "__pycache__/",
    "*.pyc",
    "persist/",
    "data/",
    ".iox_env",  # vscode_debug.py's _IOX_ENV_FILENAME -- holds the live PG3INIT secret.
]
_DEFAULT_AUTHOR_NAME = "NuCore Plugin Author"
_DEFAULT_AUTHOR_EMAIL = "plugin-author@users.noreply.github.com"
_DEFAULT_COMMIT_MESSAGE = "Initial commit"
_DEFAULT_BRANCH = "main"
_DEFAULT_REMOTE = "origin"


def _ensure_gitignore(plugin_dir: Path) -> bool:
    """Create or extend .gitignore so every _REQUIRED_GITIGNORE_LINES entry
    is present, preserving anything already there untouched. Returns whether
    the file was created or changed."""
    gitignore_path = plugin_dir / _GITIGNORE_FILENAME
    existing_lines = gitignore_path.read_text(encoding="utf-8").splitlines() if gitignore_path.is_file() else []

    missing = [line for line in _REQUIRED_GITIGNORE_LINES if line not in existing_lines]
    if not missing:
        return False

    new_lines = list(existing_lines)
    if new_lines and new_lines[-1].strip():
        new_lines.append("")
    new_lines.extend(missing)
    gitignore_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return True


async def _run(nucore_interface: NuCoreInterface, command: str, cwd: str) -> dict[str, Any]:
    return await shell.run_shell_command(nucore_interface, {"command": command, "cwd": cwd})


async def setup_github_repo(
    nucore_interface: NuCoreInterface, args: dict[str, Any], *, plugin_output_root: str
) -> Any:
    location = (args.get("location") or "").strip()
    if not location:
        return {"error": "location is required"}

    try:
        plugin_dir = confine_path(plugin_output_root, location)
    except ValueError as exc:
        return {"error": str(exc)}

    if not plugin_dir.is_dir():
        return {"error": f"no plugin directory found at '{location}'"}

    cwd = str(plugin_dir)
    visibility = args.get("visibility") or "private"
    if visibility not in ("private", "public"):
        return {"error": "visibility must be 'private' or 'public'"}
    repo_name = (args.get("repo_name") or location).strip()
    remote_url = (args.get("remote_url") or "").strip() or None
    commit_message = (args.get("commit_message") or _DEFAULT_COMMIT_MESSAGE).strip()
    author_name = (args.get("author_name") or _DEFAULT_AUTHOR_NAME).strip()
    author_email = (args.get("author_email") or _DEFAULT_AUTHOR_EMAIL).strip()
    push = args.get("push", True)

    result: dict[str, Any] = {"location": location}

    result["gitignore_updated"] = _ensure_gitignore(plugin_dir)

    git_initialized = False
    if not (plugin_dir / ".git").is_dir():
        init_result = await _run(nucore_interface, f"git init -b {_DEFAULT_BRANCH}", cwd)
        if init_result.get("error"):
            return init_result
        if init_result.get("exit_code") != 0:
            return {"error": "git init failed", **init_result}
        git_initialized = True
    result["git_initialized"] = git_initialized

    add_result = await _run(nucore_interface, "git add -A", cwd)
    if add_result.get("error"):
        return add_result
    if add_result.get("exit_code") != 0:
        return {"error": "git add failed", **add_result}

    commit_cmd = (
        f"git -c user.name={shlex.quote(author_name)} -c user.email={shlex.quote(author_email)} "
        f"commit -m {shlex.quote(commit_message)}"
    )
    commit_result = await _run(nucore_interface, commit_cmd, cwd)
    if commit_result.get("error"):
        return commit_result
    if commit_result.get("exit_code") != 0:
        if "nothing to commit" in (commit_result.get("stdout") or ""):
            result["committed"] = False
            result["reason"] = "no changes"
        else:
            return {"error": "git commit failed", **commit_result}
    else:
        result["committed"] = True
        result["commit_message"] = commit_message

    existing_remote = await _run(nucore_interface, "git remote get-url origin", cwd)
    has_remote = not existing_remote.get("error") and existing_remote.get("exit_code") == 0

    if remote_url:
        remote_cmd = (
            f"git remote set-url {_DEFAULT_REMOTE} {shlex.quote(remote_url)}"
            if has_remote
            else f"git remote add {_DEFAULT_REMOTE} {shlex.quote(remote_url)}"
        )
        remote_result = await _run(nucore_interface, remote_cmd, cwd)
        if remote_result.get("error"):
            return remote_result
        if remote_result.get("exit_code") != 0:
            return {"error": "failed to set git remote", **remote_result}
        result["remote_url"] = remote_url
    elif has_remote:
        result["remote_url"] = (existing_remote.get("stdout") or "").strip() or None
    else:
        auth_check = await _run(nucore_interface, "gh auth status", cwd)
        if auth_check.get("error") or auth_check.get("exit_code") != 0:
            result["remote_url"] = None
            result["pushed"] = False
            result["warning"] = (
                "the gh CLI isn't available or authenticated on this host -- create an empty "
                "repo on GitHub.com yourself and re-call setup_github_repo with remote_url set "
                "to its URL"
            )
            return result

        create_cmd = f"gh repo create {shlex.quote(repo_name)} --{visibility} --source=. --remote={_DEFAULT_REMOTE}"
        create_result = await _run(nucore_interface, create_cmd, cwd)
        if create_result.get("error"):
            return create_result
        if create_result.get("exit_code") != 0:
            return {"error": "gh repo create failed", **create_result}
        result["remote_url"] = (create_result.get("stdout") or "").strip() or None

    if not push:
        result["pushed"] = False
        return result

    if not result.get("remote_url"):
        result["pushed"] = False
        return result

    branch_result = await _run(nucore_interface, "git rev-parse --abbrev-ref HEAD", cwd)
    branch = (branch_result.get("stdout") or "").strip() or _DEFAULT_BRANCH

    push_result = await _run(nucore_interface, f"git push -u {_DEFAULT_REMOTE} {shlex.quote(branch)}", cwd)
    if push_result.get("error"):
        return push_result
    if push_result.get("exit_code") != 0:
        return {"error": "git push failed", **push_result}

    result["pushed"] = True
    return result
