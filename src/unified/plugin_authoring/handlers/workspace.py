"""Workspace discovery (design/developers/impl_plan.md Phase 3): what's
already been generated under ``--plugin-output-root``, and loading one back
into context -- so a customer coming back later gets the AI picking up where
it left off, not starting blind. The "new vs. history" half of the
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


async def read_generated_plugin(
    nucore_interface: NuCoreInterface, args: dict[str, Any], *, plugin_output_root: str
) -> Any:
    """*nucore_interface* is unused -- this tool never touches the hub.
    Loads one previously-generated plugin's files back into context so the
    model has something to modify, rather than starting blind -- including
    its accumulated ``sources.md`` evidence history, so a resumed session
    can check what's already been searched/fetched before spending another
    search or fetch call on the same ground."""
    location = (args.get("location") or "").strip()
    if not location:
        return {"error": "location is required"}

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

    plugin_py_path = plugin_dir / _PLUGIN_FILENAME
    plugin_py = plugin_py_path.read_text(encoding="utf-8") if plugin_py_path.is_file() else None

    readme_path = plugin_dir / _README_FILENAME
    readme = readme_path.read_text(encoding="utf-8") if readme_path.is_file() else None

    license_path = plugin_dir / _LICENSE_FILENAME
    license_md = license_path.read_text(encoding="utf-8") if license_path.is_file() else None

    tests = {
        test_path.name: test_path.read_text(encoding="utf-8")
        for test_path in sorted((plugin_dir / "tests").glob("test_*.py"))
        if test_path.is_file()
    }

    context_path = plugin_dir / _CONTEXT_FILENAME
    context = context_path.read_text(encoding="utf-8") if context_path.is_file() else None

    sources_path = plugin_dir / _SOURCES_FILENAME
    sources = sources_path.read_text(encoding="utf-8") if sources_path.is_file() else None

    return {
        "location": location,
        "profile": profile,
        "plugin_py": plugin_py,
        "readme": readme,
        "license": license_md,
        "tests": tests,
        "context": context,
        "sources": sources,
    }
