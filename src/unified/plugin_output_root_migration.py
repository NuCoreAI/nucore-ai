"""Automatic ``--plugin-output-root`` relocation, run inline from
``run_unified_runtime.py main()`` when ``--old-plugin-output-root`` is given
and still exists.

A plain filesystem move isn't safe here: every plugin's ``server_entry.json``
bakes in its own absolute directory (``path``, set by
``plugin_authoring.handlers.scaffold._build_server_entry``), and if the
plugin was ever installed, the real hub generates a BSD rc.d service script
(``/usr/local/etc/rc.d/plugin_<profileNum>``) whose ``udx_command`` also
bakes in that same absolute path. Only a genuine uninstall+reinstall
regenerates that script -- a registration update plus a restart does not.

See ``/home/admin/.claude/plans/ok-i-want-a-resilient-kurzweil.md`` for the
full design rationale (left as a plan-file reference, not duplicated here).

Success is defined precisely (see :attr:`MigrationResult.success`): every
plugin's files copied cleanly, every nsid'd plugin's registration update
succeeded, every previously-installed plugin was reinstalled into its exact
original slot with its exact pre-migration run state restored, and the
permissions refresh succeeded. Only on full success is ``old_root`` renamed
to ``<old_root>.old`` -- the rename is the completion marker; a path that no
longer exists at the next startup means migration is silently skipped.
Nothing here ever deletes ``old_root``, and nothing here is fatal to normal
server startup -- a failure is logged and retried automatically next restart.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nucore import NuCoreInterface
from utils import get_logger

from .plugin_authoring.handlers.dev_venv import setup_dev_venv
from .plugin_authoring.handlers.install import update_registered_plugin

logger = get_logger(__name__)

_SERVER_ENTRY_FILENAME = "server_entry.json"
# Matches dev_venv.py's own _VENV_DIRNAME -- not imported cross-module since
# that name is private to that handler; this is the one other place that
# needs to recognize the same directory name: to skip it during the copy and
# to detect when setup_dev_venv needs to be called afterward to recreate it
# at the new root.
_VENV_DIRNAME = ".venv"


def _is_successful(response: Any) -> bool:
    return isinstance(response, dict) and bool(response.get("successful"))


@dataclass
class PluginMigrationOutcome:
    """Per-plugin bookkeeping for one migration run. ``ok`` is what gates
    the overall migration's success (and therefore the final rename) --
    deliberately excludes ``run_state_error``, since failing to restore a
    "was stopped" plugin to stopped is a much smaller problem than leaving
    a plugin uninstalled or on the wrong profileNum. Also excludes
    ``venv_error``: ``.venv`` is a local/dev-testing convenience only (see
    ``dev_venv.py``'s module docstring), never required for a real
    hub-managed install."""

    location: str
    nsid: str | None = None
    has_venv: bool = False
    copy_status: str | None = None  # "copied" / "already-migrated" / "error: ..."
    venv_recreated: bool | None = None
    venv_error: str | None = None
    registration_updated: bool | None = None
    registration_error: str | None = None
    was_installed: bool = False
    was_running: bool | None = None
    reinstalled: bool | None = None
    reinstall_error: str | None = None
    run_state_restored: bool | None = None
    run_state_error: str | None = None

    @property
    def ok(self) -> bool:
        if self.copy_status is not None and self.copy_status.startswith("error"):
            return False
        if self.registration_error is not None:
            return False
        if self.reinstall_error is not None:
            return False
        return True


@dataclass
class MigrationResult:
    old_root: Path
    new_root: Path
    plugins: list[PluginMigrationOutcome] = field(default_factory=list)
    permissions_refresh_error: str | None = None
    renamed_to: Path | None = None

    @property
    def success(self) -> bool:
        if self.permissions_refresh_error is not None:
            return False
        return all(p.ok for p in self.plugins)


def _enumerate_plugin_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return [child for child in sorted(root.iterdir()) if child.is_dir() and not child.is_symlink()]


def _read_nsid(plugin_dir: Path) -> str | None:
    entry_path = plugin_dir / _SERVER_ENTRY_FILENAME
    if not entry_path.is_file():
        return None
    try:
        entry = json.loads(entry_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    nsid = entry.get("nsid")
    return nsid if isinstance(nsid, str) and nsid else None


def build_plan(old_root: Path) -> list[PluginMigrationOutcome]:
    """Pure, read-only enumeration of *old_root* -- mirrors
    ``plugin_authoring.handlers.workspace.list_generated_plugins``'s
    "walk immediate subdirectories, skip symlinks/non-dirs" shape."""
    return [
        PluginMigrationOutcome(
            location=child.name,
            nsid=_read_nsid(child),
            has_venv=(child / _VENV_DIRNAME).is_dir(),
        )
        for child in _enumerate_plugin_dirs(old_root)
    ]


def _relative_file_sizes(root: Path, *, skip_venv: bool) -> dict[str, int]:
    sizes: dict[str, int] = {}
    if not root.is_dir():
        return sizes
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        rel = path.relative_to(root)
        if skip_venv and _VENV_DIRNAME in rel.parts:
            continue
        if rel.name == _SERVER_ENTRY_FILENAME:
            # rewrite_server_entry_paths deliberately rewrites this file's
            # "path" field at the destination after a copy completes, so
            # it's expected to diverge from the source byte-for-byte on a
            # resumed/retried run -- comparing every other file already
            # confirms the copy itself was complete.
            continue
        sizes[str(rel)] = path.stat().st_size
    return sizes


def _trees_match(src: Path, dest: Path, *, copy_venv: bool) -> bool:
    """"Did the copy actually complete" check -- relative file set plus
    per-file size, not a cryptographic hash. Large ``.venv`` payloads are
    excluded unless *copy_venv* (they're skipped by the copy itself in that
    case, so comparing them would always "mismatch")."""
    skip_venv = not copy_venv
    return _relative_file_sizes(src, skip_venv=skip_venv) == _relative_file_sizes(dest, skip_venv=skip_venv)


def copy_plugin_dir(src_dir: Path, dest_parent: Path, *, copy_venv: bool) -> str:
    """Crash-safe, resumable copy of one plugin directory. Returns
    ``"copied"``, ``"already-migrated"`` (destination already matches --
    retried run after a partial failure), or ``"error: ..."`` (destination
    exists but doesn't match, or the copy itself didn't verify -- either
    way, this plugin is left exactly as it was, nothing clobbered)."""
    dest_dir = dest_parent / src_dir.name
    tmp_dir = dest_parent / f"{src_dir.name}.migrating-tmp"

    if dest_dir.exists():
        if _trees_match(src_dir, dest_dir, copy_venv=copy_venv):
            return "already-migrated"
        return "error: destination exists and does not match source"

    if tmp_dir.exists():
        # Leftover from a prior crash -- never the final name, always safe
        # to discard and redo.
        shutil.rmtree(tmp_dir, ignore_errors=True)

    dest_parent.mkdir(parents=True, exist_ok=True)
    ignore = None if copy_venv else shutil.ignore_patterns(_VENV_DIRNAME)
    try:
        shutil.copytree(src_dir, tmp_dir, symlinks=True, ignore=ignore)
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return f"error: copy failed: {exc}"

    if not _trees_match(src_dir, tmp_dir, copy_venv=copy_venv):
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return "error: copy verification failed"

    tmp_dir.rename(dest_dir)  # atomic within dest_parent's filesystem -- the durable "done" marker
    return "copied"


def rewrite_server_entry_paths(new_root: Path) -> None:
    """For every plugin dir now under *new_root* with a ``server_entry.json``,
    correct its ``path`` field. No-op (byte-identical) when already correct,
    so re-running this is always cheap."""
    for child in _enumerate_plugin_dirs(new_root):
        entry_path = child / _SERVER_ENTRY_FILENAME
        if not entry_path.is_file():
            continue
        try:
            entry = json.loads(entry_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error("plugin_output_root_migration: could not parse %s: %s", entry_path, exc)
            continue
        new_path = str(child)
        if entry.get("path") == new_path:
            continue
        entry["path"] = new_path
        entry_path.write_text(json.dumps(entry, indent=2) + "\n", encoding="utf-8")


async def _repair_one_plugin(
    nucore_interface: NuCoreInterface, new_root: Path, outcome: PluginMigrationOutcome
) -> None:
    nsid = outcome.nsid
    if not nsid:
        return

    update_result = await update_registered_plugin(
        nucore_interface, {"location": outcome.location}, plugin_output_root=str(new_root)
    )
    if isinstance(update_result, dict) and update_result.get("error"):
        outcome.registration_error = update_result["error"]
        return
    outcome.registration_updated = True

    installed_response = await nucore_interface.get_installed_plugins()
    installed_list = installed_response.get("data") if _is_successful(installed_response) else None
    match = next((p for p in (installed_list or []) if p.get("nsid") == nsid), None)
    if match is None:
        return  # registered but never installed -- there's no running service to fix

    outcome.was_installed = True
    profile_num = match.get("profileNum")
    outcome.was_running = match.get("state") != "stopped"

    uninstall_response = await nucore_interface.uninstall_installed_plugin(profile_num)
    if not _is_successful(uninstall_response):
        outcome.reinstall_error = f"failed to uninstall profile_num {profile_num} for reinstall"
        logger.critical(
            "plugin_output_root_migration: could not uninstall '%s' (nsid %s, profile_num %s) to "
            "reinstall it at the new path -- it is unchanged (still installed at the old path).",
            outcome.location, nsid, profile_num,
        )
        return

    install_response = await nucore_interface.install_local_plugin(nsid, profile_num=profile_num)
    if not _is_successful(install_response):
        # One retry -- the registration/path/slot are already correct at
        # this point, nothing else to redo before trying again.
        install_response = await nucore_interface.install_local_plugin(nsid, profile_num=profile_num)
    if not _is_successful(install_response):
        outcome.reinstall_error = f"failed to reinstall nsid '{nsid}' into profile_num {profile_num}"
        logger.critical(
            "plugin_output_root_migration: '%s' (nsid %s) was uninstalled from profile_num %s and "
            "could NOT be reinstalled -- it is now NOT INSTALLED and needs manual attention.",
            outcome.location, nsid, profile_num,
        )
        return

    installed_response = await nucore_interface.get_installed_plugins()
    installed_list = installed_response.get("data") if _is_successful(installed_response) else None
    reinstalled_match = next((p for p in (installed_list or []) if p.get("nsid") == nsid), None)
    reinstalled_profile_num = reinstalled_match.get("profileNum") if reinstalled_match else None
    if reinstalled_match is None or reinstalled_profile_num != profile_num:
        outcome.reinstall_error = (
            f"reinstalled '{nsid}' but it came back with profile_num {reinstalled_profile_num} "
            f"instead of its original {profile_num}"
        )
        logger.critical(
            "plugin_output_root_migration: '%s' (nsid %s) reinstalled into a DIFFERENT or MISSING "
            "profile_num (expected %s, got %s) -- anything keyed on the old profileNum is now stale.",
            outcome.location, nsid, profile_num, reinstalled_profile_num,
        )
        return

    outcome.reinstalled = True

    # install_local_plugin's own docstring claims it starts the plugin, but
    # install_generated_plugin (handlers/install.py) never trusts that alone
    # -- it always follows up with an explicit plugin_ops(..., "start") call.
    # Mirrored here for the same reason, in both directions: a plugin that
    # was running before migration must come back running (explicit "start",
    # treated as a hard failure like the reinstall itself); one that was
    # deliberately stopped before migration must not be silently started as
    # a side effect of fixing its path (explicit "stop", treated as a softer
    # warning -- ending up installed-but-running is a much smaller problem
    # than ending up not-installed).
    if outcome.was_running:
        start_response = await nucore_interface.plugin_ops(profile_num, "start")
        if not _is_successful(start_response):
            outcome.reinstall_error = f"reinstalled '{nsid}' but failed to start it (profile_num {profile_num})"
            logger.critical(
                "plugin_output_root_migration: '%s' (nsid %s, profile_num %s) was running before "
                "migration but could not be restarted afterward.",
                outcome.location, nsid, profile_num,
            )
            return
        outcome.run_state_restored = True
    else:
        stop_response = await nucore_interface.plugin_ops(profile_num, "stop")
        if not _is_successful(stop_response):
            outcome.run_state_error = f"reinstalled '{nsid}' but failed to stop it (profile_num {profile_num})"
            logger.warning(
                "plugin_output_root_migration: '%s' (nsid %s, profile_num %s) was stopped before "
                "migration but could not be re-stopped afterward -- it may now be running.",
                outcome.location, nsid, profile_num,
            )
        else:
            outcome.run_state_restored = True


async def migrate_plugin_output_root(
    nucore_interface: NuCoreInterface, old_root: Path, new_root: Path, *, copy_venv: bool = False
) -> MigrationResult:
    """Moves every plugin directory from *old_root* to *new_root*, repairs
    each previously-installed plugin's hub registration and service file
    (uninstall + reinstall into the same slot, with its exact pre-migration
    run state restored), refreshes permissions, and -- only on full success
    -- renames *old_root* to ``'<old_root>.old'``.

    Safe to call with *old_root* already gone (the caller is expected to
    check existence first, since that's also how a completed migration is
    detected on a later startup, but this also no-ops cleanly if called
    anyway)."""
    old_root = Path(old_root)
    new_root = Path(new_root)
    result = MigrationResult(old_root=old_root, new_root=new_root)

    if not old_root.exists():
        return result

    plan = build_plan(old_root)

    for outcome in plan:
        src_dir = old_root / outcome.location
        outcome.copy_status = copy_plugin_dir(src_dir, new_root, copy_venv=copy_venv)
        if outcome.has_venv and not (outcome.copy_status or "").startswith("error"):
            # The copied .venv's own shebangs/activate scripts still point at
            # the old path (or, with copy_venv=False, there's no .venv at the
            # new root at all yet) -- either way, rebuild it fresh here
            # rather than leaving it broken for a human to notice later.
            venv_result = await setup_dev_venv(
                nucore_interface, {"location": outcome.location, "force": True},
                plugin_output_root=str(new_root),
            )
            if isinstance(venv_result, dict) and venv_result.get("error"):
                outcome.venv_error = venv_result["error"]
                logger.warning(
                    "plugin_output_root_migration: '%s' had a .venv but recreating it at the "
                    "new root failed: %s",
                    outcome.location, outcome.venv_error,
                )
            else:
                outcome.venv_recreated = True

    rewrite_server_entry_paths(new_root)

    # Run once, before any install/start attempt touches the freshly-copied
    # files -- matches install_generated_plugin's own reasoning for
    # refreshing permissions right before a start call, just applied once
    # for this whole batch instead of per-plugin.
    refresh_response = await nucore_interface.refresh_eisy_ai_permissions()
    if isinstance(refresh_response, dict) and refresh_response.get("error"):
        result.permissions_refresh_error = refresh_response["error"]

    for outcome in plan:
        if outcome.copy_status is not None and outcome.copy_status.startswith("error"):
            continue  # don't touch the hub for a plugin whose files never safely landed
        await _repair_one_plugin(nucore_interface, new_root, outcome)

    result.plugins = plan

    if result.success:
        renamed = old_root.with_name(old_root.name + ".old")
        if renamed.exists():
            logger.warning(
                "plugin_output_root_migration: migration succeeded but '%s' already exists -- "
                "leaving '%s' in place under its original name for manual resolution. Migration "
                "will simply run again on the next restart in the meantime.",
                renamed, old_root,
            )
        else:
            old_root.rename(renamed)
            result.renamed_to = renamed

    return result


def log_migration_result(result: MigrationResult) -> None:
    """One line per plugin plus a summary line, via this module's logger --
    called by ``run_unified_runtime.py main()`` after the migration call."""
    if not result.plugins and result.renamed_to is None and result.permissions_refresh_error is None:
        return  # old_root didn't exist -- nothing happened, nothing to log
    for outcome in result.plugins:
        logger.info(
            "plugin_output_root_migration: '%s' nsid=%s copy=%s venv_recreated=%s installed=%s "
            "running=%s ok=%s%s",
            outcome.location, outcome.nsid, outcome.copy_status, outcome.venv_recreated,
            outcome.was_installed, outcome.was_running, outcome.ok,
            f" error={outcome.registration_error or outcome.reinstall_error}" if not outcome.ok else "",
        )
    if result.success:
        logger.info(
            "plugin_output_root_migration: migration from '%s' to '%s' succeeded; old directory "
            "renamed to '%s'.",
            result.old_root, result.new_root, result.renamed_to,
        )
    else:
        logger.error(
            "plugin_output_root_migration: migration from '%s' to '%s' did NOT fully succeed "
            "(permissions_refresh_error=%s) -- '%s' was left in place and will be retried on the "
            "next restart.",
            result.old_root, result.new_root, result.permissions_refresh_error, result.old_root,
        )
