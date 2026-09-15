"""``run_diagnostic_step`` -- backend-vetted diagnostic functions (e.g. PLM/
link queries), distinct from device/routine/variable data. Deliberately
on-demand (like ``list_variables``), not a standing prompt database --
diagnostics is an exception, not a routine part of every turn.

There's a single diagnostics tool for the narrow/hardware-locking/rare
steps, not a menu of named tools: the model calls ``get_diagnostics_prompt``
for the instructional prose and step catalog, then calls
``run_diagnostic_step`` as many times as it needs -- guided by that prose
and by what the customer actually described -- instead of the backend
pre-mapping every complaint to a canned plan. No session -- no start call
needed, nothing to explicitly end.

Three steps are promoted to standing top-level tools instead
(``get_full_system_config``/``get_core_services_status``/``get_device_family``,
below) -- they're cheap, side-effect-free, needed for the "Step 1, always"
INSTEON-diagnostics rule and for protocol-family questions outside
diagnostics entirely, and gating them behind ``get_diagnostics_prompt`` cost
a wasted round trip on every single use (confirmed against a real
production log: the model repeatedly guessed at a nonexistent log path
before ever calling ``get_diagnostics_prompt``). They still dispatch through
``NuCoreInterface.run_diagnostic_step`` under the hood (same backend
methods, same ``diagnose.md``-validated registry) -- only the model-facing
surface changed, not the implementation.

The step catalog/dispatch logic lives in ``NuCoreInterface.run_diagnostic_step``
(backend-owned) -- this module is a thin pass-through.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nucore import NuCoreInterface

# Read once at import time, like every other static-content constant in this
# codebase (e.g. prompt_builder.py's _HOST_PLATFORM) -- this file cannot
# change over the process's lifetime.
_DIAGNOSTICS_PROMPT = (
    Path(__file__).parent.parent / "diagnostics" / "prompts" / "diagnose.md"
).read_text(encoding="utf-8").strip()


async def run_diagnostic_step(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    step = args.get("step")
    if not step:
        return {"error": "step is required -- see get_diagnostics_prompt's step catalog"}
    params = args.get("params") or {}
    if isinstance(params, str):
        # Models occasionally send a stringified JSON object here instead of
        # real nesting -- recover it rather than letting **params below raise
        # a raw TypeError ("argument after ** must be a mapping, not str").
        try:
            params = json.loads(params)
        except json.JSONDecodeError:
            return {"error": f"params must be a JSON object, not a plain string: {params!r}"}
    if not isinstance(params, dict):
        return {"error": f"params must be a JSON object, got {type(params).__name__}"}
    return await nucore_interface.run_diagnostic_step(step, **params)


async def get_diagnostics_prompt(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    return _DIAGNOSTICS_PROMPT


async def get_full_system_config(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    return await nucore_interface.get_full_system_config()


async def get_core_services_status(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    return await nucore_interface.get_core_services_status()


async def get_device_family(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    device_id = args.get("device_id")
    if not device_id:
        return {"error": "device_id is required"}
    return await nucore_interface.get_device_family(device_id)
