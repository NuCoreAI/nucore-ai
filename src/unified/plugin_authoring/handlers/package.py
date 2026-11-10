"""``package_generated_plugin`` -- builds a publish-ready ``.tar.gz`` of a
generated plugin's directory, as the artifact that will eventually be
submitted to the plugin store. Submission itself is out of scope here --
this only builds the archive.

Denylist, not allowlist: walks the whole plugin directory and excludes a
known-bad set, rather than only including the exact files
``generation_inputs.json`` says were generated. This extends an existing,
already-reviewed precedent -- ``setup_github_repo``'s
``_REQUIRED_GITIGNORE_LINES`` (``.venv/``, ``__pycache__/``, ``*.pyc``,
``persist/``, ``data/``, ``.iox_env``) is exactly "what must never be
shared from a generated plugin's directory," written for git but equally
applicable here. An allowlist would silently drop any file a developer
hand-added to the plugin directory that scaffold didn't generate,
producing a broken published plugin missing a real dependency.

Beyond filename-based exclusion, also reuses ``secret_guard.find_secret``
-- the same check ``generate_plugin_scaffold``'s Guard 8 runs -- over
every included text file's content, refusing to write the archive at all
if a configured secret value is found anywhere. Symlinks are skipped
during the walk, consistent with ``path_confinement.py``'s own stance on
never following a symlinked target out of the allowed root.

Always overwrites any prior archive for the same plugin -- same
"cheap to regenerate" stance as ``setup_vscode_debug_config``'s
``.iox_env``/``launch.json``; this never touches plugin source, only a
build artifact under its own ``dist/`` subdirectory (itself excluded from
the walk, so a re-run never archives its own prior output).

Archive members are stored flat (no top-level directory) -- the plugin
store's own extraction step is expected to supply the destination
directory. The filename is ``{plugin name}-{version}.tar.gz``: the name
from ``server_entry.json``'s ``name`` field (read for this purpose only --
that file itself stays excluded from the archive), the version read
directly from ``version.py``'s rendered ``ud_plugin_version = "..."`` line
(the same value ``main.py`` passes to ``polyglot.start()``), not
``generation_inputs.json``'s own ``"version"`` field, which can drift from
it and is excluded from the package anyway.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import tarfile
from pathlib import Path
from typing import Any

from nucore import NuCoreInterface

from ..file_change_notify import notify_files_changed
from ..path_confinement import confine_path
from ..permissions_refresh import refresh_permissions_after_write
from ..secret_guard import find_secret
from .scaffold import (
    _CONTEXT_FILENAME,
    _DATA_DIRNAME,
    _DEFAULT_VERSION,
    _GENERATION_INPUTS_FILENAME,
    _PERSIST_DIRNAME,
    _SERVER_ENTRY_FILENAME,
    _SOURCES_FILENAME,
    _VERSION_FILENAME,
)

_DIST_DIRNAME = "dist"
_TESTS_DIRNAME = "tests"
_VERSION_PATTERN = re.compile(r'ud_plugin_version\s*=\s*"([^"]+)"')
_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")

_EXCLUDED_DIRNAMES = {
    ".venv",
    "__pycache__",
    _PERSIST_DIRNAME,
    _DATA_DIRNAME,
    _TESTS_DIRNAME,
    ".git",
    ".vscode",
    _DIST_DIRNAME,
}
_EXCLUDED_FILENAMES = {
    _SERVER_ENTRY_FILENAME,
    _SOURCES_FILENAME,
    _CONTEXT_FILENAME,
    _GENERATION_INPUTS_FILENAME,
    ".iox_env",
    ".gitignore",
    ".DS_Store",
}
_EXCLUDED_FILE_GLOBS = (
    "*.pyc",
    "*.pid",
    "*.lock",
    "*.tmp",
    "*.log",
    "*.pem",
    "*.key",
    "*.crt",
    "*.cer",
    "*.p12",
    "*.pfx",
    "*.env",
)


def _should_skip_file(name: str) -> bool:
    if name in _EXCLUDED_FILENAMES:
        return True
    return any(fnmatch.fnmatch(name, pattern) for pattern in _EXCLUDED_FILE_GLOBS)


def _collect_archive_members(plugin_dir: Path) -> list[Path]:
    members: list[Path] = []
    for root, dirnames, filenames in os.walk(plugin_dir):
        dirnames[:] = sorted(d for d in dirnames if d not in _EXCLUDED_DIRNAMES)
        root_path = Path(root)
        for filename in sorted(filenames):
            if _should_skip_file(filename):
                continue
            file_path = root_path / filename
            if file_path.is_symlink():
                continue
            members.append(file_path)
    return members


def _scan_for_secrets(members: list[Path], secret_values: list[str]) -> str | None:
    texts = []
    for path in members:
        try:
            texts.append(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, OSError):
            continue
    return find_secret(texts, secret_values)


def _load_version(plugin_dir: Path) -> str:
    """Reads the real, authoritative version straight from version.py's
    rendered ``ud_plugin_version = "..."`` line (plugin_skeleton.
    render_version_py's exact output -- the same value main.py passes to
    polyglot.start()), not generation_inputs.json's own "version" field,
    which can drift from it and is excluded from the package anyway."""
    version_path = plugin_dir / _VERSION_FILENAME
    if version_path.is_file():
        match = _VERSION_PATTERN.search(version_path.read_text(encoding="utf-8"))
        if match:
            return match.group(1)
    return _DEFAULT_VERSION


def _load_plugin_name(plugin_dir: Path, location: str) -> str:
    """server_entry.json's top-level "name" field -- the same field
    setup_vscode_debug_config already falls back from (entry.get("name")
    or location) -- read for filename purposes only; server_entry.json
    itself stays excluded from the archive's contents."""
    entry_path = plugin_dir / _SERVER_ENTRY_FILENAME
    name = location
    if entry_path.is_file():
        try:
            data = json.loads(entry_path.read_text(encoding="utf-8"))
            if isinstance(data.get("name"), str) and data["name"].strip():
                name = data["name"].strip()
        except Exception:
            pass
    return _UNSAFE_NAME_CHARS.sub("-", name)


async def package_generated_plugin(
    nucore_interface: NuCoreInterface, args: dict[str, Any], *, plugin_output_root: str, secret_values: list[str]
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

    members = _collect_archive_members(plugin_dir)
    if not members:
        return {"error": f"no publishable files found at '{location}'"}

    secret_hit = _scan_for_secrets(members, secret_values)
    if secret_hit:
        return {
            "error": "a file in this plugin's directory appears to contain a configured "
            "secret value; refusing to package it"
        }

    plugin_name = _load_plugin_name(plugin_dir, location)
    version = _load_version(plugin_dir)
    dist_dir = plugin_dir / _DIST_DIRNAME
    dist_dir.mkdir(exist_ok=True)
    archive_path = dist_dir / f"{plugin_name}-{version}.tar.gz"

    with tarfile.open(archive_path, "w:gz") as tar:
        for member_path in members:
            arcname = str(member_path.relative_to(plugin_dir))
            tar.add(member_path, arcname=arcname, recursive=False)

    permissions_refresh_error = await refresh_permissions_after_write(nucore_interface)
    files_changed_notify_error = await notify_files_changed(nucore_interface, [str(archive_path)])

    result = {
        "location": location,
        "plugin_name": plugin_name,
        "version": version,
        "archive_path": str(archive_path),
        "included_files": sorted(str(p.relative_to(plugin_dir)) for p in members),
    }
    if permissions_refresh_error is not None:
        result["permissions_refresh_error"] = permissions_refresh_error
    if files_changed_notify_error is not None:
        result["files_changed_notify_error"] = files_changed_notify_error
    return result
