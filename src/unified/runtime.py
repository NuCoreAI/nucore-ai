"""``UnifiedRuntime`` -- the runtime: a genuine multi-turn agentic
tool-calling loop, invoked by ``run_unified_runtime.py``'s ``_run_once``/
``_run_loop``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .adapters import LLMAdapter
from .models import IntentHandlerResult
from .session_store import SessionStore
from nucore import NuCoreInterface

from .dispatch import execute_tool
from .loop import AgenticLoop
from .prompt_builder import build_system_prompt

_TOOLS_DIR = Path(__file__).parent / "tools"


class UnifiedRuntime:
    def __init__(
        self,
        *,
        nucore_interface: NuCoreInterface,
        llm_client: LLMAdapter,
        runtime_config: dict[str, Any],
        max_iterations: int = 8,
    ) -> None:
        self.nucore_interface = nucore_interface
        self.llm_client = llm_client
        self.runtime_config = runtime_config
        self.session_store = SessionStore()
        self.tool_specs = LLMAdapter.tools_spec_from_files(sorted(_TOOLS_DIR.glob("tool_*.json")))
        self.max_iterations = max_iterations
        # v1 does not support streaming -- present so run_unified_runtime.py's
        # `if runtime.stream_state is not None:` guard is a no-op.
        self.stream_state = None

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
        return await execute_tool(name, args, nucore_interface=self.nucore_interface)

    def reset_stream_handler(self) -> None:
        """No-op -- v1 does not stream. Present so ``run_unified_runtime.py``'s
        ``_run_loop`` can call it unconditionally."""

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
        history = self.session_store.get(session_id, max_turns=int(self.runtime_config.get("default_max_turns", 20)))

        system_prompt = await build_system_prompt(self.nucore_interface)

        history_messages: list[dict[str, Any]] = []
        for turn in history.turns:
            history_messages.append({"role": "user", "content": turn.query})
            history_messages.append({"role": "assistant", "content": turn.response})

        user_message = query
        if framework_context:
            user_message = f"<context>{framework_context}</context>\n\n{query}"

        loop = AgenticLoop(
            llm_client=self.llm_client,
            tool_specs=self.tool_specs,
            dispatch=self._dispatch,
            max_iterations=self.max_iterations,
        )
        final_text, _ = await loop.run(
            system_prompt=system_prompt,
            history_messages=history_messages,
            user_message=user_message,
            llm_config=self._resolve_llm_config(),
        )

        history.append(query, final_text)
        return IntentHandlerResult(intent="unified", output={"text": final_text})
