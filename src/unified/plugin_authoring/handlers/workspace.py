"""Workspace discovery (design/developers/impl_plan.md Phase 3): what's
already been generated under this profile's ``plugin_output_root`` (runtime
config), and loading one back into context -- so a customer coming back
later gets the AI picking up where it left off, not starting blind. The
"new vs. history" half of the
authoring flow; actually modifying and rewriting a plugin goes through
``handlers/scaffold.py``'s ``generate_plugin_scaffold`` and its overwrite
flow (design/developers/plugin_authoring_p4_impl.md Stage 2).

Local-disk-only -- no external dependency, so both tools here are always
registered (see ``dispatch.build_tool_handlers``), unlike the Phase 2
discovery tools that depend on a configured search engine.

``context.md`` is the append-only iteration log ``generate_plugin_scaffold``
writes one entry to per call (most recent last) -- its latest entry is
preferred over README.md's heading for a listing's one-line description,
since it reflects what actually changed most recently rather than a
possibly-stale README.

``sources.md`` is the deduped, cross-session record of every evidence
source ``generate_plugin_scaffold`` has ever gathered for this plugin (see
``evidence_ledger.py``'s ``merge_sources``/``render_sources_md``) --
``read_generated_plugin`` hands it back here so a resumed session can see
what's already been searched/fetched before spending another search or
fetch call on the same ground.

``LICENSE.md`` is fully derived from ``sources.md`` plus the commissioned
developer's name (``handlers/scaffold.py``'s ``_render_license``) -- never
LLM-authored freeform text the way ``readme_body`` is, but still handed
back here for completeness.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nucore import NuCoreInterface

from ..path_confinement import confine_path

_PROFILE_FILENAME = "profile.json"
_PLUGIN_FILENAME = "plugin.py"
_README_FILENAME = "README.md"
_LICENSE_FILENAME = "LICENSE.md"
_CONTEXT_FILENAME = "context.md"
_SOURCES_FILENAME = "sources.md"
_MAIN_FILENAME = "main.py"
_VERSION_FILENAME = "version.py"
_SERVER_ENTRY_FILENAME = "server_entry.json"
_INSTALL_FILENAME = "install.sh"
_REQUIREMENTS_FILENAME = "requirements.txt"
_GENERATION_INPUTS_FILENAME = "generation_inputs.json"

_ALL_READABLE_KEYS = (
    "profile",
    "plugin_py",
    "main_py",
    "version_py",
    "server_entry",
    "install_sh",
    "requirements_txt",
    "generation_inputs",
    "readme",
    "license",
    "tests",
    "context",
    "sources",
)


def _latest_context_note(context_text: str | None) -> str | None:
    """``context.md``'s entries are ``## <timestamp>\\n\\n<note>\\n\\n``
    blocks, appended in order -- the latest note is whatever follows the
    last ``## `` header. ``None`` if there's no context.md yet or it has no
    headers (never raises on malformed content)."""
    if not context_text:
        return None
    blocks = context_text.split("\n## ")
    if len(blocks) < 2:
        return None
    last_block = blocks[-1]
    # last_block is "<timestamp>\n\n<note>\n\n" -- drop the timestamp line.
    _, _, note = last_block.partition("\n")
    note = note.strip()
    return note or None


def _best_effort_name_and_description(
    plugin_dir: Path, fallback_name: str, context_text: str | None = None
) -> tuple[str, str | None]:
    """Never raises -- a malformed or missing profile.json/README.md/
    context.md just means a less informative label, not a broken listing."""
    name = fallback_name
    description: str | None = None

    try:
        profile = json.loads((plugin_dir / _PROFILE_FILENAME).read_text(encoding="utf-8"))
        nodedefs = profile.get("nodedefs")
        if isinstance(nodedefs, list) and nodedefs and isinstance(nodedefs[0], dict):
            name = nodedefs[0].get("name") or name
    except Exception:
        pass

    description = _latest_context_note(context_text)

    if description is None:
        try:
            readme_text = (plugin_dir / _README_FILENAME).read_text(encoding="utf-8")
            for line in readme_text.splitlines():
                stripped = line.strip().lstrip("#").strip()
                if stripped:
                    description = stripped
                    break
        except Exception:
            pass

    return name, description


async def list_generated_plugins(
    nucore_interface: NuCoreInterface, args: dict[str, Any], *, plugin_output_root: str
) -> Any:
    """*nucore_interface* is unused -- this tool never touches the hub, only
    this server's own output directory. Returns every immediate
    subdirectory of *plugin_output_root* that contains a profile.json,
    sorted most-recently-modified first (the "history" ordering)."""
    root = Path(plugin_output_root).expanduser().resolve()
    if not root.is_dir():
        return {"plugins": []}

    entries = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.is_symlink():
            continue
        profile_path = child / _PROFILE_FILENAME
        if not profile_path.is_file():
            continue

        context_path = child / _CONTEXT_FILENAME
        context_text = context_path.read_text(encoding="utf-8") if context_path.is_file() else None
        name, description = _best_effort_name_and_description(child, fallback_name=child.name, context_text=context_text)
        mtime = datetime.fromtimestamp(child.stat().st_mtime, tz=timezone.utc).isoformat()
        entries.append(
            {
                "location": child.name,
                "name": name,
                "description": description,
                "last_modified_at": mtime,
            }
        )

    entries.sort(key=lambda e: e["last_modified_at"], reverse=True)
    return {"plugins": entries}


def _read_text_if_present(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


def _read_json_if_present(path: Path) -> Any | None:
    """Best-effort: a missing *or malformed* secondary file just means
    ``None`` here, not a broken call -- same "degrade gracefully" posture
    every other optional field in this function already has (``readme``/
    ``license``/``context``/``sources`` are all ``None`` when absent)."""
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


async def read_generated_plugin(
    nucore_interface: NuCoreInterface, args: dict[str, Any], *, plugin_output_root: str
) -> Any:
    """*nucore_interface* is unused -- this tool never touches the hub.
    Loads one previously-generated plugin's files back into context so the
    model has something to modify, rather than starting blind -- including
    its accumulated ``sources.md`` evidence history, so a resumed session
    can check what's already been searched/fetched before spending another
    search or fetch call on the same ground.

    Returns every file by default; pass ``files`` (a subset of
    ``_ALL_READABLE_KEYS``) to get back only the ones asked for -- useful
    when a plugin's tests/README are large and only, say, ``plugin_py`` is
    actually needed this turn."""
    location = (args.get("location") or "").strip()
    if not location:
        return {"error": "location is required"}

    requested_files = args.get("files")
    if requested_files is not None:
        if not isinstance(requested_files, list) or not all(isinstance(f, str) for f in requested_files):
            return {"error": "files must be a list of strings"}
        unknown = sorted(set(requested_files) - set(_ALL_READABLE_KEYS))
        if unknown:
            return {"error": f"unknown files entry {unknown} -- must be one of {sorted(_ALL_READABLE_KEYS)}"}

    try:
        plugin_dir = confine_path(plugin_output_root, location)
    except ValueError as exc:
        return {"error": str(exc)}

    profile_path = plugin_dir / _PROFILE_FILENAME
    if not profile_path.is_file():
        return {"error": f"no {_PROFILE_FILENAME} found at '{location}'"}

    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"error": f"{_PROFILE_FILENAME} at '{location}' is not valid JSON: {exc}"}

    tests = {
        test_path.name: test_path.read_text(encoding="utf-8")
        for test_path in sorted((plugin_dir / "tests").glob("test_*.py"))
        if test_path.is_file()
    }

    result = {
        "location": location,
        "profile": profile,
        "plugin_py": _read_text_if_present(plugin_dir / _PLUGIN_FILENAME),
        "main_py": _read_text_if_present(plugin_dir / _MAIN_FILENAME),
        "version_py": _read_text_if_present(plugin_dir / _VERSION_FILENAME),
        "server_entry": _read_json_if_present(plugin_dir / _SERVER_ENTRY_FILENAME),
        "install_sh": _read_text_if_present(plugin_dir / _INSTALL_FILENAME),
        "requirements_txt": _read_text_if_present(plugin_dir / _REQUIREMENTS_FILENAME),
        "generation_inputs": _read_json_if_present(plugin_dir / _GENERATION_INPUTS_FILENAME),
        "readme": _read_text_if_present(plugin_dir / _README_FILENAME),
        "license": _read_text_if_present(plugin_dir / _LICENSE_FILENAME),
        "tests": tests,
        "context": _read_text_if_present(plugin_dir / _CONTEXT_FILENAME),
        "sources": _read_text_if_present(plugin_dir / _SOURCES_FILENAME),
    }

    if requested_files is not None:
        result = {"location": location, **{key: result[key] for key in requested_files}}

    return result
