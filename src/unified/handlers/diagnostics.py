"""``list_diagnostics``/``run_diagnostics`` -- backend-vetted diagnostic
functions (e.g. PLM/link queries), distinct from device/routine/variable
data. Deliberately on-demand (like ``list_variables``), not a standing
prompt database -- diagnostics is an exception, not a routine part of every
turn.

The single-in-flight concurrency lock, poll-same-function semantics, and
STOP-as-interrupt behavior all live in ``NuCoreInterface.run_diagnostics``
(backend-owned state) -- this module is a thin pass-through.
"""

from __future__ import annotations

from typing import Any

from nucore import NuCoreInterface


async def list_diagnostics(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    return {"diagnostics": nucore_interface.get_diagnostics_map()}


async def run_diagnostics(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    function = args.get("function")
    if not function:
        return {"error": "function is required -- call list_diagnostics first"}
    kwargs: dict[str, Any] = {}
    if args.get("candidate_devices"):
        kwargs["candidate_devices"] = args["candidate_devices"]
    if args.get("candidate_routines"):
        kwargs["candidate_routines"] = args["candidate_routines"]
    return await nucore_interface.run_diagnostics(function, **kwargs)
