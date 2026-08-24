"""Ten standalone Plan tools -- backend-vetted configuration-change
functions (device pairing, folder creation, staged scene/automation/
variable proposals, and applying them), distinct from Diagnostics' read-only
tools. No session wrapper (no start/conclude/stop) -- each of these is an
ordinary, always-available tool, the same shape as ``handlers/diagnostics.py``.

Three of these (``get_plan_prompt``, ``create_folder``, ``pair_device``) need
nothing but their own arguments. The other seven need to find *this
conversation's* staged changes, so they take ``session_id`` -- see
``dispatch.py``'s ``_SESSION_SCOPED_TOOLS``, which now means only "needs
``session_id`` forwarded," not anything about locking (there is no more
blanket lock).

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


def _coerce_params(params: Any) -> dict[str, Any] | dict[str, str]:
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except json.JSONDecodeError:
            return {"__error__": f"params must be a JSON object, not a plain string: {params!r}"}
    if not isinstance(params, dict):
        return {"__error__": f"params must be a JSON object, got {type(params).__name__}"}
    return params


async def get_plan_prompt(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    plan_type = args.get("plan_type") or "new_installation"
    return await _get_engine(nucore_interface).get_plan_prompt(plan_type)


async def create_folder(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    new_name = args.get("new_name")
    if not new_name:
        return {"error": "new_name is required"}
    return await _get_engine(nucore_interface).create_folder(nucore_interface, new_name=new_name)


async def pair_device(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    protocol = args.get("protocol")
    if not protocol:
        return {"error": "protocol is required"}
    return await _get_engine(nucore_interface).pair_device(
        nucore_interface, protocol, device_address=args.get("device_address")
    )


async def propose_scene(
    nucore_interface: NuCoreInterface, args: dict[str, Any], *, session_id: str | None = None
) -> Any:
    params = _coerce_params(args.get("params") or {})
    if "__error__" in params:
        return {"error": params["__error__"]}
    return await _get_engine(nucore_interface).propose_scene(session_id=session_id, **params)


async def propose_automation(
    nucore_interface: NuCoreInterface, args: dict[str, Any], *, session_id: str | None = None
) -> Any:
    params = _coerce_params(args.get("params") or {})
    if "__error__" in params:
        return {"error": params["__error__"]}
    return await _get_engine(nucore_interface).propose_automation(session_id=session_id, **params)


async def propose_variable(
    nucore_interface: NuCoreInterface, args: dict[str, Any], *, session_id: str | None = None
) -> Any:
    params = _coerce_params(args.get("params") or {})
    if "__error__" in params:
        return {"error": params["__error__"]}
    return await _get_engine(nucore_interface).propose_variable(session_id=session_id, **params)


async def review_plan(
    nucore_interface: NuCoreInterface, args: dict[str, Any], *, session_id: str | None = None
) -> Any:
    return await _get_engine(nucore_interface).review_plan(session_id=session_id)


async def revise_plan(
    nucore_interface: NuCoreInterface, args: dict[str, Any], *, session_id: str | None = None
) -> Any:
    params = args.get("params")
    if params is not None:
        params = _coerce_params(params)
        if "__error__" in params:
            return {"error": params["__error__"]}
    return await _get_engine(nucore_interface).revise_plan(
        session_id=session_id, id=args.get("id"), params=params, remove=bool(args.get("remove", False))
    )


async def apply_plan(
    nucore_interface: NuCoreInterface, args: dict[str, Any], *, session_id: str | None = None
) -> Any:
    return await _get_engine(nucore_interface).apply_plan(nucore_interface, session_id=session_id)


async def discard_plan(
    nucore_interface: NuCoreInterface, args: dict[str, Any], *, session_id: str | None = None
) -> Any:
    return await _get_engine(nucore_interface).discard_plan(session_id=session_id)
