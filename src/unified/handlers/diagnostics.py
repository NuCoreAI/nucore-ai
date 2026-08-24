"""Eight standalone diagnostic tools -- backend-vetted diagnostic functions
(e.g. PLM/link queries) plus one prompt-retrieval tool, distinct from
device/routine/variable data. Deliberately on-demand (like
``list_variables``), not a standing prompt database -- diagnostics is an
exception, not a routine part of every turn.

No session wrapper (no start/conclude/stop) -- each of these is an ordinary,
always-available tool, the same shape as everything else in ``dispatch.py``.
``get_dev_links_table``/``compare_device_links``/``get_all_plm_links``/
``quick_plm_sanity_check`` drive the single PLM serial connection directly
and refuse immediately (no waiting) if another of the four is already in
flight -- enforced in ``IoXDiagnostics``, not here; this module is a thin
pass-through, same as ``node_ops.py``/``plugin_management.py``.

``get_diagnostics_prompt`` is different in kind from the other seven: it
returns static prose (INSTEON link reasoning, workflow, known fixes, the
core/plugin service catalog), not backend data, so it reads
``prompt/diagnostics.md`` directly rather than going through
``NuCoreInterface``/``IoXWrapper``/``IoXDiagnostics`` -- there is no backend
logic to delegate to. That content used to be unconditionally baked into
every system prompt (``<<diagnostics>>`` in ``system_prompt.md``); moving it
behind an ordinary on-demand tool call keeps its ~2,500 tokens off the vast
majority of turns that never touch diagnostics, at the one-time cost of the
model fetching it when it actually does.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nucore import NuCoreInterface

# Read once at import time, like every other static-content constant in this
# codebase (e.g. prompt_builder.py's _HOST_PLATFORM) -- this file cannot
# change over the process's lifetime.
_DIAGNOSTICS_PROMPT = (Path(__file__).parent.parent / "prompt" / "diagnostics.md").read_text(encoding="utf-8").strip()


async def get_full_system_config(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    return await nucore_interface.diagnostics_get_full_system_config()


async def get_device_family(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    device_id = args.get("device_id")
    if not device_id:
        return {"error": "device_id is required"}
    return await nucore_interface.diagnostics_get_device_family(device_id=device_id)


async def get_dev_links_table(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    device_id = args.get("device_id")
    if not device_id:
        return {"error": "device_id is required"}
    return await nucore_interface.diagnostics_get_dev_links_table(device_id=device_id)


async def get_iox_links_table(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    device_id = args.get("device_id")
    if not device_id:
        return {"error": "device_id is required"}
    return await nucore_interface.diagnostics_get_iox_links_table(device_id=device_id)


async def compare_device_links(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    device_id = args.get("device_id")
    if not device_id:
        return {"error": "device_id is required"}
    return await nucore_interface.diagnostics_compare_device_links(device_id=device_id)


async def get_all_plm_links(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    refresh_plm_links = bool(args.get("refresh_plm_links", False))
    return await nucore_interface.diagnostics_get_all_plm_links(refresh_plm_links=refresh_plm_links)


async def quick_plm_sanity_check(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    return await nucore_interface.diagnostics_quick_plm_sanity_check()


async def get_diagnostics_prompt(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    return _DIAGNOSTICS_PROMPT
