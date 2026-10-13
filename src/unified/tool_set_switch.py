"""``switch_to_developer_mode``/``switch_to_customer_mode`` -- the model-driven
half of dynamic tool-set switching (see design/developers/merged-toolsets.md).
The explicit ``/developer``/``/customer`` chat command performs the exact same
operation; both paths go through :func:`make_switch_handler`.

Success and failure are both ordinary tool-call outcomes, matching every
other handler in this codebase (``dispatch.execute_tool``'s
``{"error": ...}`` convention) -- there is no special-cased error path. A
successful switch returns a dict carrying ``loop.SWITCH_TOOL_SET_KEY``, which
``AgenticLoop.run`` recognizes and unwinds on; a disabled target or an
exhausted switch-count cap returns a plain ``{"error": ...}`` dict, which is
just a normal tool result the model incorporates into its reply like any
other tool failure.
"""

from __future__ import annotations

from typing import Any, Callable

from nucore import NuCoreInterface

from .loop import SWITCH_TOOL_SET_KEY

_LABELS = {"unified": "customer", "plugin_authoring": "developer"}


def disabled_target_message(target_tool_set: str) -> str:
    """Exact wording for 'switch to target_tool_set refused because it isn't
    enabled' -- shared by the model's own switch-tool handler (below) and
    the explicit /developer//customer command path
    (run_unified_runtime.py), so the two can never say different things for
    the identical failure (see design/developers/merged-toolsets.md's 'no
    separate code path with its own messaging')."""
    label = _LABELS.get(target_tool_set, target_tool_set)
    return f"{label.capitalize()} tools are not enabled on this installation."


def switched_message(target_tool_set: str) -> str:
    """Confirmation text for a successful explicit /developer//customer
    switch. Only the explicit command path uses this -- a model-driven
    switch has no equivalent message of its own; the new tool set's reply
    *is* the confirmation."""
    label = _LABELS.get(target_tool_set, target_tool_set)
    return f"Switched to {label} tools."


def make_switch_handler(
    target_tool_set: str,
    *,
    is_target_enabled: Callable[[], bool],
    get_switch_count: Callable[[], int],
    max_switches: int,
) -> Callable[[NuCoreInterface, dict[str, Any]], Any]:
    """Build the dispatch handler for a switch-to-*target_tool_set* tool.

    *is_target_enabled*/*get_switch_count* are callables, not plain values,
    because both can change between calls on the same long-lived connection:
    *is_target_enabled* reflects whichever `runtime_config` this turn
    resolved (an operator can flip ``enabled`` between connections --
    see merged-toolsets.md), and *get_switch_count* reflects how many
    switches have already happened so far in *this* turn, reset to zero by
    the caller (`UnifiedRuntime.handle_query`) at the start of every turn.
    """
    async def _switch(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
        if not is_target_enabled():
            return {"error": disabled_target_message(target_tool_set)}
        if get_switch_count() >= max_switches:
            return {
                "error": (
                    f"Already switched tool sets the maximum number of times allowed for one "
                    f"request ({max_switches}). Finish with the current tool set, or ask the "
                    f"user to split this into separate requests."
                )
            }
        result: dict[str, Any] = {SWITCH_TOOL_SET_KEY: target_tool_set}
        # The tool schema marks this required, but a missing/empty value
        # still succeeds the switch -- see design/developers/merged-
        # toolsets.md's "Mid-round chaining": no handoff note just means
        # the new tool set's first message carries no extra context, the
        # original (pre-fix) behavior, not a reason to block the switch.
        handoff_summary = args.get("handoff_summary")
        if handoff_summary:
            result["handoff_summary"] = handoff_summary
        return result

    return _switch
