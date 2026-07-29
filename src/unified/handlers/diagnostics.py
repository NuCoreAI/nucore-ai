"""``start_diagnostics``/``run_diagnostic_step`` -- backend-vetted diagnostic
functions (e.g. PLM/link queries), distinct from device/routine/variable
data. Deliberately on-demand (like ``list_variables``), not a standing
prompt database -- diagnostics is an exception, not a routine part of every
turn.

There's a single diagnostics flow, not a menu of named plans: start_diagnostics
opens a session and hands back an "instruction" (loaded from a prompt) plus
the shared step catalog, and the model calls run_diagnostic_step as many
times as it needs -- guided by that instruction and by what the customer
actually described -- instead of the backend pre-mapping every complaint to a
canned plan. Ends with the dedicated "conclude" step (or "stop" to abandon
early).

The single-in-flight concurrency lock and session state all live in
``NuCoreInterface.start_diagnostics``/``run_diagnostic_step`` (backend-owned
state) -- this module is a thin pass-through. ``session_id`` (identifying
which conversation is calling) is threaded through from ``execute_tool``, not
read from ``args`` -- it's context the dispatch layer supplies, not something
the model provides.
"""

from __future__ import annotations

import json
from typing import Any

from nucore import NuCoreInterface


async def start_diagnostics(
    nucore_interface: NuCoreInterface, args: dict[str, Any], *, session_id: str | None = None
) -> Any:
    kwargs: dict[str, Any] = {}
    if args.get("candidate_devices"):
        kwargs["candidate_devices"] = args["candidate_devices"]
    if args.get("candidate_routines"):
        kwargs["candidate_routines"] = args["candidate_routines"]
    return await nucore_interface.start_diagnostics(session_id=session_id, **kwargs)


async def run_diagnostic_step(
    nucore_interface: NuCoreInterface, args: dict[str, Any], *, session_id: str | None = None
) -> Any:
    step = args.get("step")
    if not step:
        return {"error": "step is required -- see start_diagnostics' available_tools"}
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
    return await nucore_interface.run_diagnostic_step(step, session_id=session_id, **params)
