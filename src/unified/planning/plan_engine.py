"""``start_plan``/``run_plan_step`` -- the write-heavy counterpart to
Diagnostics: proposes and commits configuration changes (devices, folders,
scenes, automations, variables) instead of just investigating existing
state.

Mirrors ``iox.diagnostics.iox_diagnostics.IoXDiagnostics``'s session/dispatch
pattern almost exactly (single in-flight session dict, a fenced ```json```
step catalog parsed out of a prompt file and validated against real ``_<step>``
methods, ``conclude``/``stop`` as the only terminal steps) -- but lives here in
``unified`` rather than being delegated through ``NuCoreInterface`` the way
Diagnostics is. Diagnostics needs that delegation because its steps are raw,
protocol-specific SOAP calls that only make sense for an IoX/INSTEON backend.
Plan's steps (aside from device pairing) already go through existing
``NuCoreInterface``-level handler functions in ``unified.handlers`` -- putting
Plan's state inside ``IoXWrapper`` would force ``iox`` to import from
``unified``, which is backwards (``unified`` depends on ``iox``, never the
reverse).

Only one plan type is actually implemented (``new_installation``) -- every
other name in ``_PLAN_TYPES`` is recognized (so a typo'd/unknown type gets a
clear error, not a silent no-op) but maps to ``None``, and ``start_plan``
returns a plain "not yet available" response for those instead of opening a
session.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from unified.handlers import group_scene_ops, node_ops, routine_automation, variable_ops
from utils import get_logger

logger = get_logger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_JSON_BLOCK_RE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)

# The only two steps with no backend function -- ended by run_plan_step's own
# dispatch logic, by literal name, before any getattr lookup. Same convention
# as IoXDiagnostics._TERMINAL_STEPS.
_TERMINAL_STEPS = frozenset({"conclude", "stop"})

# Every plan type from design/plan-design.md's catalog. Only "new_installation"
# maps to a real prompt file; every other name is recognized (for a clean
# error on typos) but stubbed -- start_plan returns "not_implemented" for them.
_PLAN_TYPES: dict[str, str | None] = {
    "new_installation": "plan_new_installation.md",
    "room_addition": None,
    "vacation": None,
    "holidays": None,
    "remodel": None,
    "move": None,
    "irrigation": None,
    "rental_turnover": None,
    "aging_in_place": None,
    "storm_prep": None,
    "party_mode": None,
    "downsizing": None,
    "nursery": None,
    "animal_protection": None,
    "safety_security": None,
    "serenity": None,
}


def _parse_plan_config(engine_cls: type, plan_type: str, text: str) -> tuple[str, dict[str, dict[str, Any]]]:
    """Parse *text* (a plan type's prompt file content) and return
    ``(full_text, step_registry)`` -- mirrors
    ``IoXDiagnostics._parse_diagnostic_config`` exactly: the fenced ```json
    block is the single source of truth for both the model-facing step
    descriptions and the ``_<step>`` methods they dispatch to. Raises
    ``RuntimeError`` at load time (not first use) if the prompt and the code
    have drifted apart.
    """
    match = _JSON_BLOCK_RE.search(text)
    if not match:
        raise RuntimeError(f"{plan_type}'s prompt file is missing its ```json steps block")

    try:
        steps = json.loads(match.group(1))
    except json.JSONDecodeError as ex:
        raise RuntimeError(f"{plan_type}'s ```json steps block is malformed: {ex}") from ex

    for name in steps:
        if name in _TERMINAL_STEPS:
            continue
        function_name = f"_{name}"
        if not callable(getattr(engine_cls, function_name, None)):
            raise RuntimeError(
                f"{plan_type}'s prompt declares step '{name}', but PlanEngine has no method '{function_name}'"
            )

    return text, steps


def _load_plan_configs(engine_cls: type) -> dict[str, tuple[str, dict[str, dict[str, Any]]]]:
    """Load and validate every non-stub plan type's prompt file once, at
    import time -- same "fail loudly at startup, not on first use" property
    Diagnostics has, but shared across every ``PlanEngine`` instance rather
    than re-parsed per instance (Diagnostics only ever has one instance in
    practice; Plan's engine is constructed lazily, potentially more than
    once, so this avoids re-parsing the same static file repeatedly).
    """
    common_text = (_PROMPTS_DIR / "plan_common.md").read_text(encoding="utf-8").strip()
    configs: dict[str, tuple[str, dict[str, dict[str, Any]]]] = {}
    for plan_type, filename in _PLAN_TYPES.items():
        if filename is None:
            continue
        text = (_PROMPTS_DIR / filename).read_text(encoding="utf-8").strip()
        full_text = f"{common_text}\n\n{text}"
        _, steps = _parse_plan_config(engine_cls, plan_type, text)
        configs[plan_type] = (full_text, steps)
    return configs


class PlanEngine:
    """One in-flight plan session, system-wide (not per-conversation) --
    same shape as ``IoXDiagnostics``'s ``_diagnostics_state``. Attached
    lazily to a ``nucore_interface`` instance (see
    ``unified.handlers.plan._get_engine``) rather than constructed eagerly,
    so its lifetime still matches that instance's without ``iox`` ever
    importing from ``unified``.
    """

    _PLAN_TIMEOUT_S = 300  # 5 minutes, mirrors IoXDiagnostics._DIAGNOSTICS_TIMEOUT_S

    # Populated once, at class-definition time (see bottom of this module) --
    # shared by every instance, not re-parsed per PlanEngine().
    _CONFIGS: dict[str, tuple[str, dict[str, dict[str, Any]]]] = {}

    def __init__(self) -> None:
        self._plan_state: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Session management -- mirrors IoXDiagnostics.start_diagnostics /
    # run_diagnostic_step / get_running_diagnostic almost exactly.
    # ------------------------------------------------------------------

    async def start_plan(self, plan_type: str, *, session_id: str | None = None) -> Any:
        if plan_type not in _PLAN_TYPES:
            return {
                "error": f"'{plan_type}' is not a known plan type. Known types: {sorted(_PLAN_TYPES)}"
            }

        state = self._plan_state
        if state is not None:
            elapsed = time.monotonic() - state["started_at"]
            if elapsed < self._PLAN_TIMEOUT_S:
                if state.get("session_id") != session_id:
                    return {
                        "error": (
                            f"a plan session is already in progress "
                            f"(started {int(elapsed)}s ago, times out after {self._PLAN_TIMEOUT_S}s) "
                            "for a different conversation -- wait for it to finish or time out"
                        )
                    }
                # Re-show the ORIGINALLY started plan type's instruction/steps,
                # same "re-show, don't restart" semantics as start_diagnostics
                # -- a second start_plan call in the same conversation doesn't
                # switch plan types mid-session.
                instruction, steps = self._CONFIGS[state["plan_type"]]
                return {
                    "status": "in_progress",
                    "plan_type": state["plan_type"],
                    "instruction": instruction,
                    "available_tools": list(steps.keys()),
                    "elapsed_s": int(elapsed),
                }
            logger.warning(f"plan session exceeded {self._PLAN_TIMEOUT_S}s; clearing stale lock")
            self._plan_state = None

        config = self._CONFIGS.get(plan_type)
        if config is None:
            return {
                "status": "not_implemented",
                "plan_type": plan_type,
                "message": f"The '{plan_type}' plan is not yet available. Currently only 'new_installation' is supported.",
            }

        instruction, steps = config
        self._plan_state = {
            "plan_type": plan_type,
            "started_at": time.monotonic(),
            "status": "in_progress",
            "session_id": session_id,
            "staged_ops": [],
            "next_op_id": 1,
        }
        return {
            "status": "in_progress",
            "plan_type": plan_type,
            "instruction": instruction,
            "available_tools": list(steps.keys()),
        }

    def get_running_plan(self) -> dict[str, Any] | None:
        state = self._plan_state
        if state is None:
            return None
        elapsed = time.monotonic() - state["started_at"]
        if elapsed >= self._PLAN_TIMEOUT_S:
            return None
        return {"status": "in_progress", "elapsed_s": int(elapsed), "session_id": state.get("session_id")}

    async def run_plan_step(self, nucore_interface: Any, step: str, *, session_id: str | None = None, **params) -> Any:
        state = self._plan_state
        if state is None or state["status"] != "in_progress":
            return {"error": "no plan session is in progress -- call start_plan first"}

        elapsed = time.monotonic() - state["started_at"]
        if elapsed >= self._PLAN_TIMEOUT_S:
            logger.warning(f"plan session exceeded {self._PLAN_TIMEOUT_S}s; clearing")
            self._plan_state = None
            return {"status": "timed_out"}

        if state.get("session_id") != session_id:
            return {"error": "this plan session belongs to a different conversation"}

        _, steps = self._CONFIGS[state["plan_type"]]
        if step not in steps:
            return {"error": f"'{step}' is not a known plan step; see start_plan's available_tools"}

        if step == "conclude":
            self._plan_state = None
            return {"status": "completed", "summary": params.get("summary")}

        if step == "stop":
            # No pairing session to defensively clean up: pair_device only
            # ever calls add_device, a self-contained, one-shot operation --
            # Plan never opens the discover_devices()/finish_device_discovery()
            # batch session, so there's nothing left in flight on the hub.
            self._plan_state = None
            return {"status": "stopped"}

        function = getattr(self, f"_{step}", None)
        if function is None or not callable(function):
            return {"error": f"plan step '{step}' is not yet implemented"}

        try:
            result = await function(nucore_interface, **params)
        except Exception as ex:
            logger.error(f"plan step '{step}' failed: {ex}")
            return {"error": f"plan step '{step}' failed: {ex}"}

        return {"step": step, "result": result}

    # ------------------------------------------------------------------
    # Gather
    # ------------------------------------------------------------------

    async def _list_variables(self, nucore_interface: Any, **params) -> Any:
        return await variable_ops.list_variables(nucore_interface, params)

    # ------------------------------------------------------------------
    # Pairing / immediate commit
    # ------------------------------------------------------------------

    async def _pair_device(self, nucore_interface: Any, protocol: str, device_address: str | None = None, **kwargs) -> Any:
        if protocol != "insteon":
            return (
                f"Pairing for '{protocol}' is not yet supported -- guide the customer "
                "through the vendor's manual pairing procedure instead."
            )
        if not device_address:
            return {"error": "device_address is required"}
        # Only the targeted, self-contained add-by-address call -- never the
        # discover_devices()/finish_device_discovery() batch session, which
        # has no reliable way to map an anonymously-linked address back to
        # which room/name the customer actually meant.
        return await nucore_interface.add_device(device_address)

    async def _create_folder(self, nucore_interface: Any, new_name: str | None = None, **kwargs) -> Any:
        if not new_name:
            return {"error": "new_name is required"}
        return await node_ops.node_op(nucore_interface, {"operation": "add_folder", "new_name": new_name})

    # ------------------------------------------------------------------
    # Staging
    # ------------------------------------------------------------------

    def _stage_op(self, op: str, params: dict[str, Any]) -> dict[str, Any]:
        state = self._plan_state
        op_id = state["next_op_id"]
        state["next_op_id"] += 1
        entry = {"id": op_id, "op": op, "params": params, "status": "proposed"}
        state["staged_ops"].append(entry)
        return entry

    async def _propose_scene(self, nucore_interface: Any, **params) -> Any:
        return self._stage_op("scene", params)

    async def _propose_automation(self, nucore_interface: Any, **params) -> Any:
        return self._stage_op("automation", params)

    async def _propose_variable(self, nucore_interface: Any, **params) -> Any:
        return self._stage_op("variable", params)

    async def _review_plan(self, nucore_interface: Any, **kwargs) -> Any:
        return {"staged_ops": self._plan_state["staged_ops"]}

    async def _revise_plan(
        self, nucore_interface: Any, id: int | None = None, params: dict[str, Any] | None = None, remove: bool = False, **kwargs
    ) -> Any:
        if id is None:
            return {"error": "id is required"}
        staged_ops = self._plan_state["staged_ops"]
        entry = next((e for e in staged_ops if e["id"] == id), None)
        if entry is None:
            return {"error": f"no staged item with id {id}"}
        if remove:
            staged_ops.remove(entry)
            return {"id": id, "status": "removed"}
        if params is not None:
            entry["params"] = params
        return entry

    # ------------------------------------------------------------------
    # Commit
    # ------------------------------------------------------------------

    async def _apply_plan(self, nucore_interface: Any, **kwargs) -> Any:
        results: list[dict[str, Any]] = []
        for entry in self._plan_state["staged_ops"]:
            if entry["status"] != "proposed":
                continue

            op = entry["op"]
            params = entry["params"]
            try:
                if op == "scene":
                    result = await group_scene_ops.multi_device_scene(nucore_interface, params)
                elif op == "automation":
                    result = await routine_automation.create_or_update_routine(nucore_interface, params)
                elif op == "variable":
                    result = await variable_ops.variable_op(nucore_interface, {**params, "operation": "create"})
                else:
                    result = {"error": f"unknown staged op '{op}'"}
            except Exception as ex:
                result = {"error": str(ex)}

            ok = isinstance(result, dict) and "error" not in result
            entry["status"] = "applied" if ok else f"failed: {result.get('error') if isinstance(result, dict) else result}"
            results.append({"id": entry["id"], "op": op, "successful": ok, "result": result})

        successful = sum(1 for r in results if r["successful"])
        return {
            "summary": {"total": len(results), "successful": successful, "failed": len(results) - successful},
            "results": results,
        }


PlanEngine._CONFIGS = _load_plan_configs(PlanEngine)
