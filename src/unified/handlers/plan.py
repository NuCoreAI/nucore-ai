"""``start_plan``/``run_plan_step`` -- Plan is the write-heavy counterpart to
Diagnostics: proposes and commits configuration changes (devices, folders,
scenes, automations, variables) instead of just investigating existing state.

Unlike Diagnostics, Plan's session state is not delegated through
``NuCoreInterface`` -- it lives entirely in ``unified.planning.PlanEngine``
(see that module's docstring for why). ``_get_engine`` attaches one
``PlanEngine`` per ``nucore_interface`` instance lazily, the first time it's
needed, so its lifetime still matches that instance's without ``iox`` ever
importing from ``unified``.

Same string-params recovery as ``diagnostics.py``: models occasionally send
``params`` as a JSON-encoded string instead of a real object.
"""

from __future__ import annotations

import json
from typing import Any

from nucore import NuCoreInterface

from ..planning import PlanEngine


def _get_engine(nucore_interface: NuCoreInterface) -> PlanEngine:
    engine = getattr(nucore_interface, "_plan_engine", None)
    if engine is None:
        engine = PlanEngine()
        nucore_interface._plan_engine = engine
    return engine


def get_running_plan(nucore_interface: NuCoreInterface) -> dict[str, Any] | None:
    """Used by ``dispatch.execute_tool`` to gate every other tool call while
    a plan session is in flight -- mirrors ``NuCoreInterface.get_running_diagnostic``'s
    role, just not on the interface itself (see module docstring)."""
    return _get_engine(nucore_interface).get_running_plan()


async def start_plan(
    nucore_interface: NuCoreInterface, args: dict[str, Any], *, session_id: str | None = None
) -> Any:
    plan_type = args.get("plan_type")
    if not plan_type:
        return {"error": "plan_type is required"}
    return await _get_engine(nucore_interface).start_plan(plan_type, session_id=session_id)


async def run_plan_step(
    nucore_interface: NuCoreInterface, args: dict[str, Any], *, session_id: str | None = None
) -> Any:
    step = args.get("step")
    if not step:
        return {"error": "step is required -- see start_plan's available_tools"}
    params = args.get("params") or {}
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except json.JSONDecodeError:
            return {"error": f"params must be a JSON object, not a plain string: {params!r}"}
    if not isinstance(params, dict):
        return {"error": f"params must be a JSON object, got {type(params).__name__}"}
    return await _get_engine(nucore_interface).run_plan_step(
        nucore_interface, step, session_id=session_id, **params
    )
