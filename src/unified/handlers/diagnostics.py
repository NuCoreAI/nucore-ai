"""``run_diagnostic_step`` -- backend-vetted diagnostic functions (e.g. PLM/
link queries), distinct from device/routine/variable data. Deliberately
on-demand (like ``list_variables``), not a standing prompt database --
diagnostics is an exception, not a routine part of every turn.

There's a single diagnostics tool, not a menu of named steps: the model
calls ``get_diagnostics_prompt`` for the instructional prose and step
catalog, then calls ``run_diagnostic_step`` as many times as it needs --
guided by that prose and by what the customer actually described -- instead
of the backend pre-mapping every complaint to a canned plan. No session --
no start call needed, nothing to explicitly end.

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
