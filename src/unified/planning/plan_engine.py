"""``get_plan_prompt``/``pair_device``/``create_folder``/``propose_scene``/
``propose_automation``/``propose_variable``/``review_plan``/``revise_plan``/
``apply_plan``/``discard_plan`` -- the write-heavy counterpart to
Diagnostics: proposes and commits configuration changes (devices, folders,
scenes, automations, variables) instead of just investigating existing
state.

No session wrapper (no start/conclude/stop) -- each of these is an ordinary,
always-available tool, same shape as ``iox.diagnostics.iox_diagnostics.
IoXDiagnostics``'s promoted diagnostic tools. The one piece of real
cross-call state Diagnostics never needed -- staged-but-uncommitted changes
(``propose_scene``/``propose_automation``/``propose_variable`` ->
``apply_plan``) -- lives here per ``session_id`` (one bucket per
conversation, see ``_get_or_create_session``), not behind a single global
lock that would block unrelated conversations' unrelated tools.

Lives in ``unified`` rather than being delegated through ``NuCoreInterface``
the way Diagnostics is, because Plan's steps (aside from device pairing)
already go through existing ``NuCoreInterface``-level handler functions in
``unified.handlers`` -- putting this state inside ``IoXWrapper`` would force
``iox`` to import from ``unified``, which is backwards (``unified`` depends
on ``iox``, never the reverse). ``pair_device`` is the one step that touches
real PLM hardware; it joins the *same* shared PLM lock the four promoted
diagnostic tools use (``NuCoreInterface.begin_plm_op``/``end_plm_op``,
delegating to ``IoXDiagnostics._begin_plm_op``/``_end_plm_op``) rather than
inventing a second, independent one -- pairing and a diagnostic link read
share the same one PLM serial connection and must not run concurrently.

Only one plan type is actually implemented (``new_installation``) -- every
other name in ``_PLAN_TYPES`` is recognized (so a typo'd/unknown type gets a
clear error, not a silent no-op) but maps to ``None``, and ``get_plan_prompt``
returns a plain "not yet available" response for those.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from unified.handlers import group_scene_ops, node_ops, routine_automation, variable_ops
from utils import get_logger

logger = get_logger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# Every plan type from design/plan-design.md's catalog. Only "new_installation"
# maps to a real prompt file; every other name is recognized (for a clean
# error on typos) but stubbed -- get_plan_prompt returns "not_implemented" for
# those instead of a session status, but the shape of the message is the same
# either way.
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


def _load_plan_prompts() -> dict[str, str]:
    """Load and concatenate each implemented plan type's prompt text once, at
    import time -- shared across every PlanEngine instance rather than
    re-read per instance (PlanEngine is constructed lazily, potentially more
    than once)."""
    common_text = (_PROMPTS_DIR / "plan_common.md").read_text(encoding="utf-8").strip()
    prompts: dict[str, str] = {}
    for plan_type, filename in _PLAN_TYPES.items():
        if filename is None:
            continue
        text = (_PROMPTS_DIR / filename).read_text(encoding="utf-8").strip()
        prompts[plan_type] = f"{common_text}\n\n{text}"
    return prompts


class PlanEngine:
    """Session-scoped staged-changes state, one bucket per ``session_id``.
    Attached lazily to a ``nucore_interface`` instance (see
    ``unified.handlers.plan._get_engine``) rather than constructed eagerly,
    so its lifetime still matches that instance's without ``iox`` ever
    importing from ``unified``.
    """

    # Staleness backstop for an abandoned conversation's staged-but-never-
    # applied-or-discarded changes -- same rationale as
    # IoXDiagnostics._PLM_OP_TIMEOUT_S: not an expected planning duration,
    # just a ceiling so stale state can't resurface much later. Resets on
    # every touch (propose/review/revise/apply/discard), not just creation,
    # so a genuinely active back-and-forth with the customer never gets cut
    # off mid-conversation.
    _PLAN_TIMEOUT_S = 300

    # Populated once, at module load (see bottom of this module) -- shared by
    # every instance, not re-parsed per PlanEngine().
    _PROMPTS: dict[str, str] = {}

    def __init__(self) -> None:
        self._plan_sessions: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _session_key(session_id: str | None) -> str:
        return session_id or "default"

    def _get_or_create_session(self, session_id: str | None) -> dict[str, Any]:
        key = self._session_key(session_id)
        session = self._plan_sessions.get(key)
        if session is not None and time.monotonic() - session["started_at"] >= self._PLAN_TIMEOUT_S:
            logger.warning(f"plan session '{key}' exceeded {self._PLAN_TIMEOUT_S}s idle; clearing stale staged ops")
            session = None
        if session is None:
            session = {"staged_ops": [], "next_op_id": 1, "started_at": time.monotonic()}
            self._plan_sessions[key] = session
        else:
            session["started_at"] = time.monotonic()  # touched -- reset the idle clock
        return session

    # ------------------------------------------------------------------
    # get_plan_prompt -- static content, no session
    # ------------------------------------------------------------------

    async def get_plan_prompt(self, plan_type: str = "new_installation") -> Any:
        if plan_type not in _PLAN_TYPES:
            return {"error": f"'{plan_type}' is not a known plan type. Known types: {sorted(_PLAN_TYPES)}"}
        prompt = self._PROMPTS.get(plan_type)
        if prompt is None:
            return {
                "status": "not_implemented",
                "plan_type": plan_type,
                "message": f"The '{plan_type}' plan is not yet available. Currently only 'new_installation' is supported.",
            }
        return {"plan_type": plan_type, "prompt": prompt}

    # ------------------------------------------------------------------
    # Immediate-commit, no staged state
    # ------------------------------------------------------------------

    async def create_folder(self, nucore_interface: Any, new_name: str | None = None, **kwargs) -> Any:
        if not new_name:
            return {"error": "new_name is required"}
        return await node_ops.node_op(nucore_interface, {"operation": "add_folder", "new_name": new_name})

    async def pair_device(
        self, nucore_interface: Any, protocol: str, device_address: str | None = None, **kwargs
    ) -> Any:
        if protocol != "insteon":
            return (
                f"Pairing for '{protocol}' is not yet supported -- guide the customer "
                "through the vendor's manual pairing procedure instead."
            )
        if not device_address:
            return {"error": "device_address is required"}
        # Same shared PLM lock the four promoted diagnostic tools use -- pairing
        # and a diagnostic link read both drive the one real PLM connection and
        # must not run concurrently. Only the targeted, self-contained
        # add-by-address call -- never the discover_devices()/
        # finish_device_discovery() batch session, which has no reliable way to
        # map an anonymously-linked address back to which room/name the
        # customer actually meant.
        busy = await nucore_interface.begin_plm_op("pair_device")
        if busy is not None:
            return busy
        try:
            return await nucore_interface.add_device(device_address)
        finally:
            await nucore_interface.end_plm_op()

    # ------------------------------------------------------------------
    # Staging (session-scoped)
    # ------------------------------------------------------------------

    def _stage_op(self, session_id: str | None, op: str, params: dict[str, Any]) -> dict[str, Any]:
        session = self._get_or_create_session(session_id)
        op_id = session["next_op_id"]
        session["next_op_id"] += 1
        entry = {"id": op_id, "op": op, "params": params, "status": "proposed"}
        session["staged_ops"].append(entry)
        return entry

    async def propose_scene(self, session_id: str | None = None, **params) -> Any:
        return self._stage_op(session_id, "scene", params)

    async def propose_automation(self, session_id: str | None = None, **params) -> Any:
        return self._stage_op(session_id, "automation", params)

    async def propose_variable(self, session_id: str | None = None, **params) -> Any:
        return self._stage_op(session_id, "variable", params)

    async def review_plan(self, session_id: str | None = None, **kwargs) -> Any:
        session = self._get_or_create_session(session_id)
        return {"staged_ops": session["staged_ops"]}

    async def revise_plan(
        self, session_id: str | None = None, id: int | None = None, params: dict[str, Any] | None = None,
        remove: bool = False, **kwargs,
    ) -> Any:
        if id is None:
            return {"error": "id is required"}
        session = self._get_or_create_session(session_id)
        staged_ops = session["staged_ops"]
        entry = next((e for e in staged_ops if e["id"] == id), None)
        if entry is None:
            return {"error": f"no staged item with id {id}"}
        if remove:
            staged_ops.remove(entry)
            return {"id": id, "status": "removed"}
        if params is not None:
            entry["params"] = params
        return entry

    async def discard_plan(self, session_id: str | None = None, **kwargs) -> Any:
        key = self._session_key(session_id)
        existed = self._plan_sessions.pop(key, None) is not None
        return {"status": "discarded" if existed else "nothing_to_discard"}

    # ------------------------------------------------------------------
    # Commit (session-scoped)
    # ------------------------------------------------------------------

    async def apply_plan(self, nucore_interface: Any, session_id: str | None = None, **kwargs) -> Any:
        session = self._get_or_create_session(session_id)
        results: list[dict[str, Any]] = []
        for entry in session["staged_ops"]:
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


PlanEngine._PROMPTS = _load_plan_prompts()
