"""``UnifiedRuntime`` -- the runtime: a genuine multi-turn agentic
tool-calling loop, invoked by ``run_unified_runtime.py``'s ``_run_once``/
``_run_loop``.

Supports more than one tool set (today: ``unified``/customer and
``plugin_authoring``) live on the same runtime, switching which one services
a turn via an explicit ``active_tool_set`` argument to :meth:`handle_query`
and/or mid-turn via the ``switch_to_developer_mode``/``switch_to_customer_mode``
tools -- see ``design/developers/merged-toolsets.md`` for the full design.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, NamedTuple

from .adapters import LLMAdapter
from .models import IntentHandlerResult
from .session_store import SessionStore
from nucore import NuCoreInterface

from .dispatch import execute_tool
from .history_compaction import maybe_compact_history
from .loop import AgenticLoop, ToolDispatch, ToolSetSwitchRequested
from .prompt_builder import build_system_prompt_sections
from .stream_handler import StreamHandler

_TOOLS_DIR = Path(__file__).parent / "tools"

SystemPromptBuilder = Callable[[NuCoreInterface], Awaitable[list[str]]]


def _build_user_message(
    query: str, *, framework_context: dict[str, Any] | None, handoff_notes: list[str]
) -> str:
    """Wrap *query* with whatever extra context this turn has accumulated so
    far: the UI's own context payload (unconditional, every turn), and --
    only once a mid-turn switch has happened -- the switching tool set's own
    ``handoff_summary`` (see loop.py's ``ToolSetSwitchRequested``). The new
    tool set's conversation history deliberately never includes the other
    tool set's turns (see merged-toolsets.md's "Session history"), so this
    is the only way it learns what's going on rather than seeing the bare
    trigger message (e.g. a lone "4" in reply to a menu it never saw) with
    zero context -- see merged-toolsets.md's "Mid-round chaining" for the
    "4." regression this fixes.

    Rebuilt from scratch (not appended to) on every switch, from the
    original *query* and the full list of notes accumulated so far, so a
    chain of more than one switch in one turn doesn't nest an already-
    wrapped message inside another wrapper.
    """
    message = query
    if framework_context:
        message = f"<ui_context>{framework_context}</ui_context>\n\n{message}"
    if handoff_notes:
        notes = "\n".join(f"- {note}" for note in handoff_notes)
        message = (
            f"(Handoff note -- the tool set you were just in has its own separate history and "
            f"cannot see it; this is the only context carried over:\n{notes})\n\n{message}"
        )
    return message


def resolve_llm_profile(runtime_config: dict[str, Any], *, preferred_key: str = "unified") -> dict[str, Any]:
    """Pick an LLM profile dict out of ``runtime_config["supported_llms"]``:
    *preferred_key* if present, else whatever key comes first. Shared by
    :meth:`UnifiedRuntime._resolve_llm_config` and ``run_unified_runtime.py``
    (which needs to know the same resolved provider up front, before
    ``UnifiedRuntime`` exists, to decide whether plugin_authoring's
    native-vs-fallback web search applies) -- extracted once here so the two
    can never drift on the fallback order."""
    supported = runtime_config.get("supported_llms", {})
    if not supported:
        return {}
    key = preferred_key if preferred_key in supported else next(iter(supported.keys()))
    return dict(supported.get(key, {}))


class DispatchBundle(NamedTuple):
    """What a tool set's ``dispatch_factory`` returns -- generalized from
    plugin_authoring's own (formerly ``_PluginAuthoringDispatch``), since
    both tool sets now go through a factory (see ``ToolSetBundle``)."""

    dispatch: ToolDispatch
    extra_tools: list[dict[str, Any]] | None = None
    on_raw_response: Callable[[Any], Any] | None = None


class ToolSetBundle(NamedTuple):
    """Everything :class:`UnifiedRuntime` needs to build one tool set's
    :class:`AgenticLoop` for one turn.

    *dispatch_factory* is called at most once per connection, lazily, the
    first time this tool set is actually used on it (mirroring
    plugin_authoring's existing per-connection ``EvidenceLedger`` factory
    pattern) -- it takes whatever connection-identity object the caller
    threads through (``run_unified_runtime.py``'s ``EisyUIContext``) and
    returns a :class:`DispatchBundle`.
    """

    tool_spec_paths: list[Path] | None
    dispatch_factory: Callable[[Any], DispatchBundle]
    system_prompt_builder: SystemPromptBuilder


class _SwitchState:
    """Per-turn bookkeeping the switch-tool handlers read from -- reset
    fresh at the start of every :meth:`UnifiedRuntime.handle_query` call, so
    a cap enforced *inside* the tool handler (see ``tool_set_switch.py``)
    always sees this turn's count, not a stale one from an earlier turn."""

    def __init__(self, enabled_tool_sets: set[str], max_switches: int) -> None:
        self._enabled_tool_sets = enabled_tool_sets
        self.max_switches = max_switches
        self.switches_used = 0

    def is_enabled(self, tool_set_name: str) -> bool:
        return tool_set_name in self._enabled_tool_sets


def _wrap_dispatch_for_switching(
    base_dispatch: ToolDispatch,
    *,
    switch_tool_name: str,
    switch_target: str,
    switch_state: _SwitchState,
) -> ToolDispatch:
    """Intercept exactly one tool name (the switch tool) and route it to a
    fresh handler bound to *this turn's* ``switch_state``, delegating every
    other name to *base_dispatch* unchanged. Keeps the expensive, genuinely
    per-connection parts of a dispatch factory (e.g. plugin_authoring's
    ``EvidenceLedger``) built once per connection, while the switch tool's
    cap-check still sees fresh per-turn state."""
    from .tool_set_switch import make_switch_handler

    switch_handler = make_switch_handler(
        switch_target,
        is_target_enabled=lambda: switch_state.is_enabled(switch_target),
        get_switch_count=lambda: switch_state.switches_used,
        max_switches=switch_state.max_switches,
    )

    async def _dispatch(name: str, args: dict[str, Any]) -> Any:
        if name == switch_tool_name:
            return await switch_handler(None, args)
        return await base_dispatch(name, args)

    return _dispatch


class UnifiedRuntime:
    def __init__(
        self,
        *,
        nucore_interface: NuCoreInterface,
        llm_client: LLMAdapter,
        runtime_config: dict[str, Any],
        session_store: SessionStore | None = None,
        tool_sets: dict[str, ToolSetBundle] | None = None,
    ) -> None:
        """*runtime_config* is the full dict `_load_runtime_config` returns
        -- `runtime_config["nucore_runtime"][<tool_set_name>]` carries each
        enabled tool set's own model/max_iterations/fabrication settings
        (see `runtime_config.py`'s per-profile fields), and
        `runtime_config["enabled_profiles"]`/`["default_profile_name"]`
        say which tool sets exist and which one a new connection starts in.

        *tool_sets* maps each enabled tool-set name to its own
        :class:`ToolSetBundle`. Omitting it (or omitting a name that
        `runtime_config` doesn't mark enabled) falls back to the
        customer-facing default (`_TOOLS_DIR` glob / `dispatch.execute_tool`
        / `prompt_builder.build_system_prompt_sections`) for the `"unified"`
        tool set specifically -- the only combination the original test
        suite covered, kept so a caller that doesn't care about multiple
        tool sets at all can still omit *tool_sets* entirely.
        """
        self.nucore_interface = nucore_interface
        self.llm_client = llm_client
        self.runtime_config = runtime_config
        # A caller serving multiple connections for what can be the same
        # logical customer (see run_unified_runtime._run_websocket_server)
        # passes one shared SessionStore so a reconnect finds its history
        # instead of starting empty -- see that class's own docstring for
        # why the per-session lock it provides is what keeps sharing it
        # safe. Every other caller (the stdin REPL, and any test that
        # doesn't pass one) keeps today's behavior: its own private store,
        # unaffected by any other instance.
        self.session_store = session_store or SessionStore()
        self._tool_sets: dict[str, ToolSetBundle] = dict(tool_sets or {})
        if "unified" not in self._tool_sets:
            self._tool_sets["unified"] = ToolSetBundle(
                tool_spec_paths=None,
                dispatch_factory=lambda _ctx: DispatchBundle(dispatch=self._default_dispatch),
                system_prompt_builder=build_system_prompt_sections,
            )
        self.default_tool_set = runtime_config.get("default_profile_name", "unified")
        self.enabled_tool_sets = set(runtime_config.get("enabled_profiles", ["unified"]))
        # Resolved lazily, at most once per connection -- see
        # ToolSetBundle.dispatch_factory's docstring.
        self._dispatch_cache: dict[str, DispatchBundle] = {}
        self._tool_specs_cache: dict[str, list[Any]] = {}
        # Chunk-count bookkeeping from the classic (retired) runtime -- unused
        # here, present only so run_unified_runtime.py's
        # `if runtime.stream_state is not None:` guard is a no-op.
        self.stream_state = None
        # Set by run_unified_runtime.main() to the same StreamHandler passed
        # into runtime_config's per-profile LLM token streaming -- _run_once
        # uses it to deliver the final response text (over a WebSocket when
        # one is attached, stdout print otherwise). Only None if a caller
        # builds UnifiedRuntime directly without going through main().
        self.stream_handler: StreamHandler | None = None

    async def _default_dispatch(self, name: str, args: dict[str, Any]) -> Any:
        return await execute_tool(name, args, nucore_interface=self.nucore_interface)

    def _profile_config(self, tool_set_name: str) -> dict[str, Any]:
        return self.runtime_config.get("nucore_runtime", {}).get(tool_set_name, {})

    def _resolve_llm_config(self, tool_set_name: str) -> dict[str, Any]:
        return resolve_llm_profile(self.runtime_config, preferred_key=tool_set_name)

    def _tool_specs_for(self, tool_set_name: str) -> list[Any]:
        if tool_set_name not in self._tool_specs_cache:
            bundle = self._tool_sets[tool_set_name]
            paths = (
                list(bundle.tool_spec_paths)
                if bundle.tool_spec_paths is not None
                else sorted(_TOOLS_DIR.glob("tool_*.json"))
            )
            self._tool_specs_cache[tool_set_name] = LLMAdapter.tools_spec_from_files(paths)
        return self._tool_specs_cache[tool_set_name]

    def _dispatch_bundle_for(self, tool_set_name: str, connection_context: Any) -> DispatchBundle:
        if tool_set_name not in self._dispatch_cache:
            self._dispatch_cache[tool_set_name] = self._tool_sets[tool_set_name].dispatch_factory(
                connection_context
            )
        return self._dispatch_cache[tool_set_name]

    def reset_stream_handler(self) -> None:
        """Reset the attached stream handler's chunk counter before a new
        query, so a stale count from a previous turn does not linger. No-op
        when no handler is attached."""
        if self.stream_handler is not None:
            self.stream_handler.reset_stream_state()

    def shutdown(self) -> None:
        """Best-effort cleanup."""
        shutdown_fn = getattr(self.nucore_interface, "shutdown", None)
        if callable(shutdown_fn):
            try:
                shutdown_fn()
            except Exception:
                pass

    async def _build_loop(self, tool_set_name: str, connection_context: Any) -> AgenticLoop:
        profile_cfg = self._profile_config(tool_set_name)
        bundle = self._dispatch_bundle_for(tool_set_name, connection_context)
        switch_tool_name = {"unified": "switch_to_developer_mode", "plugin_authoring": "switch_to_customer_mode"}.get(
            tool_set_name
        )
        switch_target = {"unified": "plugin_authoring", "plugin_authoring": "unified"}.get(tool_set_name)
        dispatch = bundle.dispatch
        if switch_tool_name is not None and switch_target is not None:
            dispatch = _wrap_dispatch_for_switching(
                dispatch,
                switch_tool_name=switch_tool_name,
                switch_target=switch_target,
                switch_state=self._current_switch_state,
            )
        return AgenticLoop(
            llm_client=self.llm_client,
            tool_specs=self._tool_specs_for(tool_set_name),
            dispatch=dispatch,
            max_iterations=int(profile_cfg.get("max_iterations", 8)),
            fabrication_guard_mode=profile_cfg.get("fabrication_guard_mode", "log"),
            max_fabrication_retries=int(profile_cfg.get("max_fabrication_retries", 1)),
            extra_tools=bundle.extra_tools,
            on_raw_response=bundle.on_raw_response,
        )

    async def handle_query(
        self,
        query: str,
        *,
        session_id: str = "default",
        framework_context: dict[str, Any] | None = None,
        active_tool_set: str | None = None,
        connection_context: Any = None,
    ) -> IntentHandlerResult:
        """Run one turn, starting in *active_tool_set* (defaulting to this
        runtime's `default_profile_name`). If the model (or the explicit
        `/developer`/`/customer` command, handled by the caller before this
        is invoked) calls that tool set's switch tool, this chains a fresh
        :class:`AgenticLoop` for the other tool set and continues the *same*
        turn -- see ``design/developers/merged-toolsets.md``'s "Mid-round
        chaining". Returns ``(result, final_active_tool_set)`` would be the
        sticky-state-friendly shape, but to keep `IntentHandlerResult`'s
        contract unchanged for existing callers, the final active tool set
        is instead stamped onto the returned result's ``output`` dict as
        ``"active_tool_set"``, for the caller to persist back onto its own
        per-connection sticky state (e.g. `EisyUIContext`).
        """
        session_id = session_id or "default"
        current_tool_set = active_tool_set or self.default_tool_set
        # Composite key -- see merged-toolsets.md's "Session history": two
        # tool sets' conversations on one connection must not interleave
        # plain-text turns in one ConversationHistory.
        composite_session_id = f"{session_id}::{current_tool_set}"

        async with self.session_store.lock(composite_session_id):
            profile_cfg = self._profile_config(current_tool_set)
            history = self.session_store.get(
                composite_session_id, max_turns=int(profile_cfg.get("max_turns", 20))
            )
            await maybe_compact_history(
                history,
                llm_client=self.llm_client,
                llm_config=self._resolve_llm_config(current_tool_set),
                token_budget=int(profile_cfg.get("history_token_budget", 20000)),
            )

            history_messages: list[dict[str, Any]] = []
            for turn in history.turns:
                history_messages.append({"role": "user", "content": turn.query})
                history_messages.append({"role": "assistant", "content": turn.response})

            handoff_notes: list[str] = []
            user_message = _build_user_message(query, framework_context=framework_context, handoff_notes=handoff_notes)

            self._current_switch_state = _SwitchState(
                enabled_tool_sets=self.enabled_tool_sets,
                max_switches=int(self.runtime_config.get("max_tool_set_switches_per_turn", 1)),
            )

            while True:
                system_prompt = await self._tool_sets[current_tool_set].system_prompt_builder(self.nucore_interface)
                loop = await self._build_loop(current_tool_set, connection_context)
                llm_config = self._resolve_llm_config(current_tool_set)
                # Grok's adapter reads this back out to set the x-grok-conv-id
                # header that keeps repeat calls in this conversation routed to
                # the same cache-warm server -- every other adapter ignores it.
                llm_config["session_id"] = composite_session_id
                try:
                    final_text, _ = await loop.run(
                        system_prompt=system_prompt,
                        history_messages=history_messages,
                        user_message=user_message,
                        llm_config=llm_config,
                    )
                    break
                except ToolSetSwitchRequested as switch:
                    self._current_switch_state.switches_used += 1
                    current_tool_set = switch.target_tool_set
                    if switch.handoff_summary:
                        handoff_notes.append(switch.handoff_summary)
                    # Re-key history to the new tool set's own composite id --
                    # the chained loop's turn gets recorded under whichever
                    # tool set actually produced the final answer.
                    composite_session_id = f"{session_id}::{current_tool_set}"
                    history = self.session_store.get(
                        composite_session_id,
                        max_turns=int(self._profile_config(current_tool_set).get("max_turns", 20)),
                    )
                    history_messages = []
                    for turn in history.turns:
                        history_messages.append({"role": "user", "content": turn.query})
                        history_messages.append({"role": "assistant", "content": turn.response})
                    user_message = _build_user_message(
                        query, framework_context=framework_context, handoff_notes=handoff_notes
                    )
                    continue

            history.append(query, final_text)

        return IntentHandlerResult(
            intent="unified", output={"text": final_text, "active_tool_set": current_tool_set}
        )
