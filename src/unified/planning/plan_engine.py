"""``start_plan``/``run_plan_step`` -- the write-heavy counterpart to
Diagnostics: proposes and commits configuration changes (devices, folders,
scenes, automations, variables) instead of just investigating existing
state.

Mirrors ``iox.diagnostics.iox_diagnostics.IoXDiagnostics``'s catalog/dispatch
pattern almost exactly (a fenced ```json``` step catalog parsed out of a
prompt file and validated against real backend methods, ``conclude``/``stop``
as the only terminal steps) plus a single in-flight session dict on top --
Diagnostics dropped its own session/terminal-steps entirely (every step is
always callable, no start/conclude/stop), but Plan keeps one since staging
changes across multiple turns before ``apply_plan`` is real state that has to
survive between calls, unlike Diagnostics' plain reads. Plan lives here in
``unified`` rather than being delegated through ``NuCoreInterface`` the way
Diagnostics is. Diagnostics needs that delegation because its steps are raw,
protocol-specific SOAP calls that only make sense for an IoX/INSTEON backend.
Plan's steps already go through existing ``NuCoreInterface``-level handler
functions in ``unified.handlers`` (device pairing is its own standalone
global tool, ``unified.handlers.pair_device``, not a Plan step) -- putting
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

from unified.handlers import group_scene_ops, routine_automation, routine_status_ops, variable_ops
from utils import get_logger

logger = get_logger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_JSON_BLOCK_RE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)

# (module, attr_name) for every tool in dispatch._PLAN_STAGEABLE_TOOLS -- kept
# as a local mirror rather than importing dispatch.TOOL_HANDLERS directly,
# since dispatch.py -> handlers.plan -> planning.plan_engine already, and
# importing back from dispatch here would be circular. Keep in sync by hand
# with that set. Resolved via getattr at call time (not stored as a direct
# function reference) so monkeypatching the module attribute -- in tests, or
# otherwise -- still takes effect, same as the plain `module.func(...)` calls
# this replaces.
_STAGEABLE_HANDLERS = {
    "create_or_update_routine": (routine_automation, "create_or_update_routine"),
    "variable_op": (variable_ops, "variable_op"),
    "multi_device_scene": (group_scene_ops, "multi_device_scene"),
    "group_scene_op": (group_scene_ops, "group_scene_op"),
    "routine_status_op": (routine_status_ops, "routine_status_op"),
}


def _describe_staged(tool: str, args: dict[str, Any]) -> str:
    """Short, human-readable label for one staged tool call, for review_plan
    and the response a staging call itself returns."""
    if tool == "create_or_update_routine":
        name = args.get("name", "?")
        comment = args.get("comment") or "(no description given)"
        return f'Routine "{name}": {comment}'
    if tool == "variable_op":
        kind = "integer" if args.get("type") == 1 else "state"
        return f'Variable "{args.get("name", "?")}" ({kind})'
    if tool == "multi_device_scene":
        devices = args.get("devices") or []
        return f'Scene "{args.get("group_name") or "(unnamed)"}" with {len(devices)} device(s)'
    if tool == "group_scene_op":
        target = args.get("group") or args.get("controller") or "?"
        return f'Scene adjustment on "{target}"'
    if tool == "routine_status_op":
        return f'Routine status change: {args.get("operation", "?")} on routine {args.get("id", "?")}'
    return f"{tool}({args})"


# The only two steps with no backend function -- ended by run_plan_step's own
# dispatch logic, by literal name, before any getattr lookup.
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
    ``(full_text, step_registry)`` -- same idea as
    ``IoXDiagnostics._parse_diagnostic_config``: the fenced ```json block is
    the single source of truth for both the model-facing step descriptions
    and the ``_<step>`` methods they dispatch to (Plan's own convention;
    Diagnostics dispatches to same-named methods, no leading underscore).
    Raises ``RuntimeError`` at load time (not first use) if the prompt and the code
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
    """One in-flight plan session, system-wide (not per-conversation).
    Attached lazily to a ``nucore_interface`` instance (see
    ``unified.handlers.plan._get_engine``) rather than constructed eagerly,
    so its lifetime still matches that instance's without ``iox`` ever
    importing from ``unified``.
    """

    _PLAN_TIMEOUT_S = 300  # 5 minutes -- a backstop against an abandoned session, not an expected duration

    # Populated once, at class-definition time (see bottom of this module) --
    # shared by every instance, not re-parsed per PlanEngine().
    _CONFIGS: dict[str, tuple[str, dict[str, dict[str, Any]]]] = {}

    def __init__(self) -> None:
        self._plan_state: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Session management -- Plan's staged-ops flow needs real cross-call
    # state (stage_tool_call -> apply_plan), unlike Diagnostics, which has no
    # session at all any more.
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
                # Re-show the ORIGINALLY started plan type's instruction/steps
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
            # No hub-side session to defensively clean up here -- Plan's
            # remaining steps are either immediate REST calls or purely
            # in-memory staged ops, neither leaves anything in flight on
            # the hub. (Device pairing, which does drive a real hub-side
            # session for some protocols, is its own standalone tool now --
            # see unified.handlers.pair_device -- not part of Plan.)
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
    # Staging -- called from dispatch.execute_tool (via handlers.plan.
    # stage_tool_call), not a run_plan_step step itself. Any call to a tool in
    # dispatch._PLAN_STAGEABLE_TOOLS made while this plan is open lands here
    # instead of hitting the real handler.
    # ------------------------------------------------------------------

    def stage_tool_call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        state = self._plan_state
        op_id = state["next_op_id"]
        state["next_op_id"] += 1
        entry = {"id": op_id, "tool": tool, "args": args, "status": "proposed"}
        state["staged_ops"].append(entry)
        return {
            "status": "staged",
            "staged_id": op_id,
            "summary": _describe_staged(tool, args),
            "message": "Staged -- nothing has been created on the hub yet. Call apply_plan to commit, or revise_plan to change it.",
        }

    async def _review_plan(self, nucore_interface: Any, **kwargs) -> Any:
        return {
            "staged_ops": [
                {
                    "id": e["id"],
                    "tool": e["tool"],
                    "status": e["status"],
                    "summary": _describe_staged(e["tool"], e["args"]),
                }
                for e in self._plan_state["staged_ops"]
            ]
        }

    async def _revise_plan(
        self, nucore_interface: Any, id: int | None = None, args: dict[str, Any] | None = None, remove: bool = False, **kwargs
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
        if args is not None:
            entry["args"] = args
        return {
            "id": entry["id"],
            "tool": entry["tool"],
            "status": entry["status"],
            "summary": _describe_staged(entry["tool"], entry["args"]),
        }

    # ------------------------------------------------------------------
    # Commit
    # ------------------------------------------------------------------

    async def _apply_plan(self, nucore_interface: Any, **kwargs) -> Any:
        results: list[dict[str, Any]] = []
        for entry in self._plan_state["staged_ops"]:
            if entry["status"] != "proposed":
                continue

            tool = entry["tool"]
            module_attr = _STAGEABLE_HANDLERS.get(tool)
            try:
                if module_attr is None:
                    result = {"error": f"unknown staged tool '{tool}'"}
                else:
                    module, attr = module_attr
                    result = await getattr(module, attr)(nucore_interface, entry["args"])
            except Exception as ex:
                result = {"error": str(ex)}

            ok = isinstance(result, dict) and "error" not in result
            entry["status"] = "applied" if ok else f"failed: {result.get('error') if isinstance(result, dict) else result}"
            results.append({"id": entry["id"], "tool": tool, "successful": ok, "result": result})

        successful = sum(1 for r in results if r["successful"])
        return {
            "summary": {"total": len(results), "successful": successful, "failed": len(results) - successful},
            "results": results,
        }


PlanEngine._CONFIGS = _load_plan_configs(PlanEngine)
