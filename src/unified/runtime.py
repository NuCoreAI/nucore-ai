"""``UnifiedRuntime`` -- the runtime: a genuine multi-turn agentic
tool-calling loop, invoked by ``run_unified_runtime.py``'s ``_run_once``/
``_run_loop``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

from .adapters import LLMAdapter
from .models import IntentHandlerResult
from .session_store import SessionStore
from nucore import NuCoreInterface

from .dispatch import execute_tool
from .history_compaction import maybe_compact_history
from .loop import AgenticLoop, ToolDispatch
from .prompt_builder import build_system_prompt_sections
from .stream_handler import StreamHandler

_TOOLS_DIR = Path(__file__).parent / "tools"

SystemPromptBuilder = Callable[[NuCoreInterface], Awaitable[list[str]]]


class UnifiedRuntime:
    def __init__(
        self,
        *,
        nucore_interface: NuCoreInterface,
        llm_client: LLMAdapter,
        runtime_config: dict[str, Any],
        max_iterations: int = 8,
        session_store: SessionStore | None = None,
        tool_spec_paths: Iterable[Path] | None = None,
        dispatch: ToolDispatch | None = None,
        system_prompt_builder: SystemPromptBuilder | None = None,
    ) -> None:
        """*tool_spec_paths*/*dispatch*/*system_prompt_builder* let a caller
        swap in an alternate tool set instead of the customer-facing default
        (this module's own ``_TOOLS_DIR`` glob / ``dispatch.execute_tool`` /
        ``prompt_builder.build_system_prompt_sections``) -- see
        ``unified.dev_tools`` for the one existing alternate tool set, and
        ``run_unified_runtime.py``'s ``--tool-set`` flag for how it's wired
        in. Omitting all three reproduces today's exact customer-path
        behavior; that's the only combination covered by the existing test
        suite; the override path is additive.
        """
        self.nucore_interface = nucore_interface
        self.llm_client = llm_client
        self.runtime_config = runtime_config
        # A caller serving multiple connections for what can be the same
        # logical customer (see run_unified_runtime._run_websocket_server)
        # passes one shared SessionStore so a reconnect finds its history
        # instead of starting empty -- see that class's own docstring for
        # why the per-session lock it provides is what keeps sharing it
        # safe. Every other caller (single-shot --query, the stdin REPL,
        # and any test that doesn't pass one) keeps today's behavior: its
        # own private store, unaffected by any other instance.
        self.session_store = session_store or SessionStore()
        paths = list(tool_spec_paths) if tool_spec_paths is not None else sorted(_TOOLS_DIR.glob("tool_*.json"))
        self.tool_specs = LLMAdapter.tools_spec_from_files(paths)
        self.max_iterations = max_iterations
        self._dispatch_override = dispatch
        self._system_prompt_builder = system_prompt_builder or build_system_prompt_sections
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

    def _resolve_llm_config(self) -> dict[str, Any]:
        """Pick the LLM profile for the unified path: an optional dedicated
        ``nucore_runtime.unified`` profile, falling back to ``default`` --
        the same fallback pattern the router's own ``nucore_runtime.router``
        already uses."""
        supported = self.runtime_config.get("supported_llms", {})
        if not supported:
            return {}
        key = "unified" if "unified" in supported else (self.runtime_config.get("default_llm") or "default")
        if key not in supported:
            key = next(iter(supported.keys()))
        return dict(supported.get(key, {}))

    async def _dispatch(self, name: str, args: dict[str, Any]) -> Any:
        if self._dispatch_override is not None:
            return await self._dispatch_override(name, args)
        return await execute_tool(name, args, nucore_interface=self.nucore_interface)

    def reset_stream_handler(self) -> None:
        """Reset the attached stream handler's chunk counter before a new
        query, so a stale count from a previous turn doesn't linger. No-op
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

    async def handle_query(
        self,
        query: str,
        *,
        session_id: str = "default",
        framework_context: dict[str, Any] | None = None,
    ) -> IntentHandlerResult:
        session_id = session_id or "default"
        # Holds this session's lock for the entire read-history -> generate
        # -> append-history sequence below, so a second request for the
        # *same* session_id (a stale reconnect, a second tab -- whatever a
        # caller sharing one SessionStore across connections might produce,
        # see run_unified_runtime._run_websocket_server) waits for this one
        # to fully finish instead of reading a half-updated history or
        # racing the same ConversationHistory object. A no-op in practice
        # for a caller using a private, unshared SessionStore (single-shot
        # --query, the stdin REPL): nothing else ever contends for that
        # lock, so it's acquired and released immediately.
        async with self.session_store.lock(session_id):
            history = self.session_store.get(
                session_id, max_turns=int(self.runtime_config.get("default_max_turns", 20))
            )
            await maybe_compact_history(
                history,
                llm_client=self.llm_client,
                llm_config=self._resolve_llm_config(),
                token_budget=int(self.runtime_config.get("history_token_budget", 20000)),
            )

            system_prompt = await self._system_prompt_builder(self.nucore_interface)

            history_messages: list[dict[str, Any]] = []
            for turn in history.turns:
                history_messages.append({"role": "user", "content": turn.query})
                history_messages.append({"role": "assistant", "content": turn.response})

            user_message = query
            if framework_context:
                user_message = f"<ui_context>{framework_context}</ui_context>\n\n{query}"

            async def dispatch(name: str, args: dict[str, Any]) -> Any:
                return await self._dispatch(name, args)

            loop = AgenticLoop(
                llm_client=self.llm_client,
                tool_specs=self.tool_specs,
                dispatch=dispatch,
                max_iterations=self.max_iterations,
                fabrication_guard_mode=self.runtime_config.get("fabrication_guard_mode", "log"),
                max_fabrication_retries=int(self.runtime_config.get("max_fabrication_retries", 1)),
            )
            llm_config = self._resolve_llm_config()
            # Grok's adapter reads this back out to set the x-grok-conv-id header
            # that keeps repeat calls in this conversation routed to the same
            # cache-warm server -- every other adapter ignores the extra key.
            llm_config["session_id"] = session_id
            final_text, _ = await loop.run(
                system_prompt=system_prompt,
                history_messages=history_messages,
                user_message=user_message,
                llm_config=llm_config,
            )

            history.append(query, final_text)
        return IntentHandlerResult(intent="unified", output={"text": final_text})
