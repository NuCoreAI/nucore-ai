from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Sequence


from .base import BaseIntentHandler
from .loader import IntentHandlerRegistry
from .models import IntentHandlerResult, RoutePlanStep, RouteResult
from .router import IntentRouter
from .session_store import SessionStore
from .stream_handler import RouterStreamHandler, StreamHandler
from .adapters import LLMAdapter
from .directory_monitor import DirectoryMonitor
from nucore import NuCoreInterface
from utils import get_logger
logger = get_logger(__name__)

SubscriberCallback = Callable[[Any], None]


_PROVIDER_CAPABILITIES: dict[str, dict[str, Any]] = {
    # Anthropic's SDK accepts a dedicated system prompt and we already map
    # system-role messages onto that field inside the adapter.
    "claude": {"supports_system_role": True},
    "anthropic": {"supports_system_role": True},
    "openai": {"supports_system_role": True},
    "gpt": {"supports_system_role": True},
    "gemini": {"supports_system_role": True},
    "google": {"supports_system_role": True},
    "grok": {"supports_system_role": True},
    "xai": {"supports_system_role": True},
    "x.ai": {"supports_system_role": True},
    "llamacpp": {"supports_system_role": True},
    "llama_cpp": {"supports_system_role": True},
    "llama.cpp": {"supports_system_role": True},
}


def _normalize_provider_name(provider: str | None) -> str:
    value = str(provider or "").strip().lower()
    if value == "anthropic":
        return "claude"
    if value == "gpt":
        return "openai"
    if value in {"google"}:
        return "gemini"
    if value in {"xai", "x.ai"}:
        return "grok"
    if value in {"llamacpp", "llama_cpp"}:
        return "llama.cpp"
    return value


def _coerce_runtime_profile(
    profile_name: str,
    payload: dict[str, Any],
    *,
    stream_handler: StreamHandler | None,
) -> dict[str, Any]:
    """Normalize one ``nucore_runtime`` profile into dispatch-ready shape."""
    provider = _normalize_provider_name(payload.get("provider"))
    if not provider:
        raise ValueError(f"nucore_runtime.{profile_name} must define a non-empty 'provider'")

    capabilities = _PROVIDER_CAPABILITIES.get(provider, {})
    result: dict[str, Any] = {
        "provider": provider,
        "model": payload.get("model"),
        "api_key": payload.get("api_key"),
        "url": payload.get("url"),
        "max_turns": int(payload.get("max_turns", 20)),
        "temperature": payload.get("temperature"),
        "max_tokens": payload.get("max_tokens"),
        "supports_system_role": bool(
            payload.get("supports_system_role", capabilities.get("supports_system_role", True))
        ),
    }
    if stream_handler is not None:
        result["stream"] = True
        result["stream_handler"] = stream_handler.handle_stream_chunk
    else:
        result["stream"] = False
    return result

def _load_runtime_config(
    path: str,
    stream_handler: StreamHandler,
) -> dict[str, Any]:
    """Load and normalize CLI-provided runtime profiles.

    Expected file format:

    {
      "nucore_runtime": {
        "default": {...},
        "router": {...},
        "intent_name": {...}
      }
    }
    """
    if not path:
        raise ValueError("A runtime profile JSON path is required")

    runtime_profile_path = Path(path).expanduser().resolve()
    if not runtime_profile_path.exists() or not runtime_profile_path.is_file():
        raise FileNotFoundError(f"Runtime profile file not found: {runtime_profile_path}")

    with runtime_profile_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if not isinstance(payload, dict):
        raise ValueError("Runtime profile must be a JSON object at top level")

    raw_runtime = payload.get("nucore_runtime")
    if not isinstance(raw_runtime, dict):
        raise ValueError("Runtime profile must contain an object key 'nucore_runtime'")

    raw_default = raw_runtime.get("default")
    if not isinstance(raw_default, dict):
        raise ValueError("nucore_runtime.default must be an object")

    default_profile = _coerce_runtime_profile("default", raw_default, stream_handler=stream_handler)

    supported_llms: dict[str, dict[str, Any]] = {"default": default_profile}
    normalized_profiles: dict[str, dict[str, Any]] = {"default": default_profile}

    raw_router = raw_runtime.get("router")
    if raw_router is not None:
        if not isinstance(raw_router, dict):
            raise ValueError("nucore_runtime.router must be an object when provided")
        router_profile = _coerce_runtime_profile("router", raw_router, stream_handler=stream_handler)
        supported_llms["router"] = router_profile
        normalized_profiles["router"] = router_profile

    for profile_name, profile_payload in raw_runtime.items():
        if profile_name in {"default", "router"}:
            continue
        if not isinstance(profile_payload, dict):
            raise ValueError(f"nucore_runtime.{profile_name} must be an object")
        normalized_profile = _coerce_runtime_profile(
            profile_name,
            profile_payload,
            stream_handler=stream_handler,
        )
        supported_llms[profile_name] = normalized_profile
        normalized_profiles[profile_name] = normalized_profile

    default_max_turns = int(default_profile.get("max_turns", 20))
    return {
        "nucore_runtime": normalized_profiles,
        "supported_llms": supported_llms,
        "default_llm": "default",
        "router_llm": "router" if "router" in supported_llms else "default",
        "default_max_turns": default_max_turns,
        "provider_capabilities": dict(_PROVIDER_CAPABILITIES),
    }


class IntentRuntime:
    """Orchestrates intent routing, handler execution, session management, and hot-reload.

    Responsibilities:
    - Owns an :class:`~loader.IntentHandlerRegistry` and keeps it in sync with
      the filesystem via a background :class:`~directory_monitor.DirectoryMonitor`.
    - Routes incoming queries to the best intent using :class:`~router.IntentRouter`.
    - Resolves and executes dependency chains in topological order.
    - Maintains per-session conversation history through :class:`~session_store.SessionStore`.
    - Caches instantiated handler objects and evicts them when their source
      files change (via ``mtime_ns`` signatures).
        - Resolves profile-based LLM config (``nucore_runtime.default`` → optional
            per-intent ``config.json`` ``llm_config`` overlay, or full
            ``nucore_runtime.<intent_name>`` override) before each handler call.

    Lifecycle::

        runtime = IntentRuntime(intent_handler_directory=..., llm_client=..., ...)
        result  = await runtime.handle_query("Turn on the lights", session_id="sess-1")
        runtime.shutdown()   # stops the directory monitor and backend API
    """

    def __init__(
        self,
        intent_handler_directory: str | Path,
        *,
        llm_client: LLMAdapter,
        nucore_interface: NuCoreInterface,
        runtime_config_path: str | Path ,
        stream_handler: StreamHandler | None = None, 
        websocket: Any = None,
    ) -> None:
        """Initialise and start the intent runtime.

        Constructs all internal subsystems (registry, router, session store,
        directory monitor), performs an initial :meth:`refresh`, starts the
        background directory monitor, and subscribes the auto-refresh callback.

        Args:
            intent_handler_directory: Path to the directory containing intent
                                      sub-directories.
            llm_client:               LLM dispatch adapter used for all
                                      generation calls (routing + handling).
            nucore_interface:         Backend API instance injected into every
                                      handler.
            runtime_config_path:      Path to runtime profile JSON containing
                                      top-level ``nucore_runtime``.
            stream_handler:           Optional stream handler whose callback is
                                      wired into LLM configs for token streaming.
            websocket:                Optional WebSocket connection passed to the stream handler for real-time streaming output.

        Raises:
            ValueError: If any of ``llm_client``, ``nucore_interface``, or
                        ``runtime_config_path`` is ``None``.
        """
        if llm_client is None or nucore_interface is None or runtime_config_path is None:
            raise ValueError("llm_client, nucore_interface, and runtime_config_path are required")
        self.intent_handler_directory = Path(intent_handler_directory).expanduser().resolve()
        self.registry = IntentHandlerRegistry(self.intent_handler_directory, websocket=websocket)
        self.llm_client = llm_client
        self.nucore_interface = nucore_interface
        self.router = IntentRouter(self.registry, llm_client, nucore_interface)
        self.runtime_config_path = runtime_config_path
        self.stream_handler = stream_handler
        # Separate stream handler used exclusively by the router so its chunk
        # counter does not interfere with per-intent handler stream state.
        self.router_stream_handler = RouterStreamHandler()
        self.stream_handler.set_websocket(websocket)
        self.router_stream_handler.set_websocket(websocket)
        self.runtime_config: dict[str, Any] = {}
        # Handler instance cache: intent_name → handler object.
        self._handler_instances: dict[str, BaseIntentHandler] = {}
        # Signature cache: intent_name → (handler_mtime_ns, config_mtime_ns, prompt_mtime_ns).
        self._handler_signatures: dict[str, tuple[int, int, int]] = {}
        self.session_store: SessionStore = SessionStore()
        self._directory_monitor = DirectoryMonitor(self._get_monitored_directories(), poll_interval_s=10)
        self.refresh()
        self.start_directory_monitor()
        self.subscribe_to_directory_changes(lambda event: self._handle_directory_change(event))

    # ------------------------------------------------------------------
    # Directory monitor callbacks
    # ------------------------------------------------------------------

    def _handle_directory_change(self, event: Any) -> None:
        """Subscriber callback invoked by the directory monitor on any file-system change.

        Performs a full :meth:`refresh` on every event.  Incremental cache
        updates are handled inside :meth:`_reconcile_handler_cache`, so only
        handlers whose source files have actually changed are evicted.
        """
        self.refresh()

    def _get_monitored_directories(self) -> Sequence[Path]:
        """Return the list of directories watched by the background poll loop."""
        assets_directory = Path(__file__).resolve().parent / "runtime_assets"
        return [self.intent_handler_directory, assets_directory]


    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Re-scan the intent directory and reload the runtime config.

        Clears per-handler signature caches, refreshes the registry, reloads
        the runtime profile JSON, validates the config,
        reconciles the handler instance cache (evicting stale entries), and
        resets the stream handler state.

        Called automatically on startup and on every directory-change event.
        """
        self._handler_instances = {}
        self._handler_signatures = {}
        self.registry.refresh()
        self.runtime_config = _load_runtime_config(
            path=self.runtime_config_path,
            stream_handler=self.stream_handler,
        )
        self._validate_runtime_config()
        self._reconcile_handler_cache()
        # Reset stream state so stale chunk counters from a previous call don't
        # bleed into the first query processed after a hot-reload.
        self.reset_stream_handler()

    def reset_stream_handler(self) -> None:
        """Reset per-call stream handler state (chunk counter, buffered data, etc.)."""
        if self.stream_handler is not None:
            self.stream_handler.reset_stream_state()

    def get_stream_chunk_count(self) -> int:
        """Return the number of streaming chunks received during the last generation call."""
        if self.stream_handler is not None:
            return self.stream_handler.get_stream_chunk_count()
        return 0

    # ------------------------------------------------------------------
    # Public query API
    # ------------------------------------------------------------------

    async def route(self, query: str, history=None) -> RouteResult:
        """Route ``query`` to an intent name without executing the handler.

        Useful for inspecting routing decisions in tests or debug tooling.

        Args:
            query:   The user query string.
            history: Optional :class:`~models.ConversationHistory` for context.

        Returns:
            A :class:`~models.RouteResult` with the selected intent name.
        """
        router_llm_config = self._resolve_router_llm_config()
        return await self.router.route(query, llm_config_override=router_llm_config, history=history)

    async def handle_agent_response(
        self,
        query: str,
        context: str,
        agent_response: str,
        *,
        framework_context: str | None = None,
        session_id: str | None = None,
    ) -> IntentHandlerResult:
        """Feed a tool/agent result back through the ``router`` to generate a final response. 

        Called after :meth:`handle_query` returns tool results that need to be
        converted into a final user-facing text response by the LLM.

        Args:
            query:             Stringified tool results to process.
            context:           Additional context to provide to the router to be used in system message. 
            agent_response:    The response from the agent to convert to human-readable form.
            framework_context: Optional extra context string forwarded to the handler.
            session_id:        Session ID for history look-up (currently unused
                               for agent responses — history is not appended).

        Returns:
            Nothing
        """
        default_max_turns: int = int(self.runtime_config.get("default_max_turns", 20))
        active_llm_key = self.runtime_config.get("default_llm")
        active_llm_cfg = self.runtime_config.get("supported_llms", {}).get(active_llm_key or "", {})
        max_turns: int = int(active_llm_cfg.get("max_turns", default_max_turns))

        history = self.session_store.get(session_id, max_turns=max_turns) if session_id else None

        return await self.router.handle_agent_response(query, context=context, agent_response=agent_response,history=history)

    async def handle_query(
        self,
        query: str,
        *,
        framework_context: str | None = None,
        session_id: str | None = None,
    ) -> list[IntentHandlerResult]:
        """Route a query, execute selected intent(s), and return the final result.

        Full execution pipeline:
        1. Retrieve (or create) the conversation history for ``session_id``.
        2. Route the query to an intent via :meth:`route`.
          3. If the router returns a multi-intent ``route_plan``, execute each
              planned step in order using its step-specific query.
                    4. Otherwise execute one intent deterministically.
          5. After execution completes, append the final text response to the
           session history (when a session ID is provided).

        Args:
            query:             Raw user input.
            framework_context: Optional extra context string forwarded to every
                               handler in the chain.
            session_id:        Optional session identifier.  When provided,
                               conversation history is loaded before the call
                               and updated after it.

        Returns:
            :class:`~models.IntentHandlerResult` list of results 
        """
        default_max_turns: int = int(self.runtime_config.get("default_max_turns", 20))
        active_llm_key = self.runtime_config.get("default_llm")
        active_llm_cfg = self.runtime_config.get("supported_llms", {}).get(active_llm_key or "", {})
        max_turns: int = int(active_llm_cfg.get("max_turns", default_max_turns))

        history = self.session_store.get(session_id, max_turns=max_turns) if session_id else None

        route_result = await self.route(query, history=history)
        if route_result is None:
            return None

        # Natural Language Mode: the router answered directly (no intent matched).
        # Return the response as a synthetic result so the caller can persist it
        # in session history, enabling the next turn to reference this reply.
        if route_result.intent is None:
            nl_text = route_result.notes or ""
            return IntentHandlerResult(
                intent="",
                output={"text": nl_text},
            )

        route_plan = route_result.route_plan or []
        if len(route_plan) > 1:
            return await self._execute_route_plan(
                initial_route=route_result,
                route_plan=route_plan,
                history=history,
                framework_context=framework_context,
            )

        return await self._execute_resolved_route(
            query=query,
            route_result=route_result,
            history=history,
            framework_context=framework_context,
        )

    async def _execute_route_plan(
        self,
        *,
        initial_route: RouteResult,
        route_plan: list[RoutePlanStep],
        history: Any,
        framework_context: str | None,
    ) -> list[IntentHandlerResult | None]:
        """Execute router-planned multi-intent steps in the returned order."""
        results: list[IntentHandlerResult | None] = [] 

        for index, step in enumerate(route_plan, start=1):
            step_route = RouteResult(
                intent=step.intent,
                confidence=initial_route.confidence,
                notes=step.notes or initial_route.notes,
                #route_context=step.route_context,
                route_context=initial_route.route_context,
                resolved_query=step.user_query,
                raw_response=initial_route.raw_response,
            )
            logger.debug(
                "Executing route-plan step %s/%s: intent='%s' query='%s'",
                index,
                len(route_plan),
                step.intent,
                step.user_query,
            )
            results.extend(await self._execute_resolved_route(
                query=step.user_query,
                route_result=step_route,
                history=history,
                framework_context=framework_context,
            ))
            if results is None:
                results = []
            await self.nucore_interface._refresh_routines_database()

        return results

    async def _execute_resolved_route(
        self,
        *,
        query: str,
        route_result: RouteResult,
        history: Any,
        framework_context: str | None,
    ) -> list[IntentHandlerResult | None]:
        """Execute the routed intent for one resolved route."""

        execution_chain = self._resolve_execution_chain(route_result.intent)

        results: list[IntentHandlerResult | None] = []

        for intent_name in execution_chain:
            logger.debug(f"Handling intent '{intent_name}' for query '{query}' with history: {history}")
            handler = self._get_or_create_handler(intent_name)
            step_llm_config = self._resolve_runtime_llm_config(intent_name)
            handler.set_runtime_llm_config(step_llm_config)
            handler.set_current_history(history)

            # Dependency intents receive a derived RouteResult that carries the
            # original routing metadata while naming the dependency as its intent.
            step_route_result = (
                route_result
                if intent_name == route_result.intent
                else RouteResult(
                    intent=intent_name,
                    confidence=route_result.confidence,
                    notes=f"Dependency step for '{route_result.intent}'",
                    resolved_query=route_result.resolved_query,
                    raw_response=route_result.raw_response,
                    route_context=route_result.route_context,
                )
            )

            effective_query = step_route_result.resolved_query or query

            messages = await handler.build_messages(
                effective_query,
                framework_context=framework_context,
                route_result=step_route_result,
                history=history,
            )
            raw_response = await handler.call_llm(messages=messages)
            extracted_tool_calls = raw_response.get_tool_calls() if raw_response else []

            result = raw_response
            if extracted_tool_calls:
                # Process each tool call as its own step so post-processing and
                # structure refresh happen between calls.
                for tool_call in extracted_tool_calls:
                    step_result = await handler.handle(
                        effective_query,
                        route_result=step_route_result,
                        framework_context=framework_context,
                        raw_response=result,
                        tool_calls=[tool_call],
                    )
                    if step_result is not None:
                        result = step_result
                    #await self.nucore_interface._refresh_routines_database
            else:
                step_result = await handler.handle(
                    effective_query,
                    route_result=step_route_result,
                    framework_context=framework_context,
                    raw_response=raw_response,
                    tool_calls=[],
                )
                if step_result is not None:
                    result = step_result

            if result is None:
                result = raw_response
            if result:
                result.set_route_result(route_result=step_route_result)
                result.set_effective_query(effective_query)
                result.add_tool_result_context(
                    context=await handler.get_tool_result_context(
                        registry=self.registry,
                        query=effective_query,
                        framework_context=framework_context,
                        route_result=step_route_result,
                    ))
                results.append(result)

        return results

    def available_intents(self) -> list[str]:
        """Return the names of all currently loaded intent handlers."""
        return self.registry.names()

    # ------------------------------------------------------------------
    # Directory monitor management
    # ------------------------------------------------------------------

    def subscribe_to_directory_changes(self, callback: SubscriberCallback) -> int:
        """Register a callback invoked whenever a monitored directory changes.

        Returns:
            Subscriber ID that can be passed to
            :meth:`unsubscribe_from_directory_changes`.
        """
        return self._directory_monitor.subscribe(callback)

    def unsubscribe_from_directory_changes(self, subscriber_id: int) -> None:
        """Remove a previously registered directory-change subscriber."""
        self._directory_monitor.unsubscribe(subscriber_id)

    def start_directory_monitor(self, poll_interval_s: float = 1.0) -> None:
        """Start (or reconfigure) the background directory poll loop."""
        self._directory_monitor.set_poll_interval(poll_interval_s)
        self._directory_monitor.start()

    def stop_directory_monitor(self) -> None:
        """Stop the background directory poll loop (best-effort, waits briefly)."""
        self._directory_monitor.stop()

    def shutdown(self) -> None:
        """Best-effort cleanup for background workers owned by the runtime."""
        try:
            self.stop_directory_monitor()
        except Exception:
            pass

        shutdown_fn = getattr(self.nucore_interface, "shutdown", None)
        if callable(shutdown_fn):
            try:
                shutdown_fn()
            except Exception:
                pass

    def poll_directory_changes(self) -> Any | None:
        """Manually trigger one directory poll and return any change event."""
        return self._directory_monitor.poll_once()

    def router_prompt(self) -> str:
        """Return the fully assembled router system prompt string."""
        return self.router.build_router_prompt()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_execution_chain(self, target_intent: str) -> list[str]:
        """Return the intents to execute for ``target_intent``.

        Execution is single-intent: only the routed intent is executed.
        """
        if not target_intent:
            return []
        return [target_intent]

    def _safe_json_data(self, value: Any) -> Any:
        """Return ``value`` if JSON-serialisable, otherwise its ``str()`` representation."""
        try:
            json.dumps(value)
            return value
        except TypeError:
            return str(value)

#    def _load_runtime_config(self) -> dict[str, Any]:
#        if not self.runtime_config_path.exists():
#            return {
#                "supported_llms": {},
#                "default_llm": None,
#                "router_llm": None,
#            }
#
#        with self.runtime_config_path.open("r", encoding="utf-8") as handle:
#            loaded = json.load(handle)
#
#        return {
#            "supported_llms": dict(loaded.get("supported_llms", {})),
#            "default_llm": loaded.get("default_llm"),
#            "router_llm": loaded.get("router_llm"),
#        }

    def _validate_runtime_config(self) -> None:
        """Validate the loaded runtime config for internal consistency.

        Checks:
        - ``supported_llms`` is a dict and has at least ``default``.
        - ``nucore_runtime.default`` exists and is an object.
        - Intent ``config.json`` ``llm_config`` values are objects when provided.

        Raises:
            ValueError: On any consistency violation.
        """
        supported = self.runtime_config.get("supported_llms", {})
        runtime_profiles = self.runtime_config.get("nucore_runtime", {})

        if not isinstance(supported, dict):
            raise ValueError("runtime_config.supported_llms must be a dictionary")
        if "default" not in supported:
            raise ValueError("runtime_config.supported_llms must include 'default'")
        if not isinstance(runtime_profiles, dict):
            raise ValueError("runtime_config.nucore_runtime must be a dictionary")
        if not isinstance(runtime_profiles.get("default"), dict):
            raise ValueError("runtime_config.nucore_runtime.default must be a dictionary")

        for definition in self.registry.definitions():  # validate all intents, not just routable ones
            llm_cfg = definition.config.get("llm_config")
            if llm_cfg is None:
                continue
            if not isinstance(llm_cfg, dict):
                raise ValueError(
                    f"Intent '{definition.name}' llm_config must be an object when provided"
                )

    def _resolve_runtime_llm_config(self, intent_name: str) -> dict[str, Any]:
        """Resolve intent runtime LLM config with profile-first precedence.

        Resolution order:
        1. ``nucore_runtime.<intent_name>`` (full override).
        2. ``nucore_runtime.default`` overlaid by intent ``config.json``
           ``llm_config`` fields.
        """
        supported = self.runtime_config.get("supported_llms", {})
        if not supported:
            return {}

        definition = self.registry.get(intent_name)
        if intent_name in supported:
            selected = dict(supported.get(intent_name, {}))
            selected["llm_key"] = intent_name
            return selected

        default_key = self.runtime_config.get("default_llm") or "default"
        if default_key not in supported:
            raise ValueError(f"Default runtime profile '{default_key}' is not available")

        merged = dict(supported.get(default_key, {}))
        intent_overlay = definition.config.get("llm_config", {})
        if intent_overlay is not None and not isinstance(intent_overlay, dict):
            raise ValueError(
                f"Intent '{intent_name}' llm_config must be an object when provided"
            )
        if isinstance(intent_overlay, dict):
            merged.update(intent_overlay)

        provider = _normalize_provider_name(merged.get("provider"))
        merged["provider"] = provider
        if "supports_system_role" not in merged:
            provider_caps = self.runtime_config.get("provider_capabilities", {})
            merged["supports_system_role"] = bool(
                provider_caps.get(provider, {}).get("supports_system_role", True)
            )
        merged["llm_key"] = default_key
        return merged

    def _resolve_router_llm_config(self) -> dict[str, Any]:
        """Build the LLM config dict used by the intent router.

        Uses ``nucore_runtime.router`` when present, otherwise falls back to
        ``nucore_runtime.default``.
        """
        supported = self.runtime_config.get("supported_llms", {})
        if not supported:
            return {}

        selected_key = self.runtime_config.get("router_llm") or self.runtime_config.get("default_llm")
        if selected_key is None:
            selected_key = next(iter(supported.keys()))
        if selected_key not in supported:
            raise ValueError(
                f"Router llm '{selected_key}' is not in runtime_config.supported_llms"
            )

        selected = dict(supported.get(selected_key, {}))
        provider = _normalize_provider_name(selected.get("provider"))
        selected["provider"] = provider
        selected["llm_key"] = selected_key
        # Override the stream handler with the router-specific one so chunk
        # counts for routing calls are tracked separately from handler calls.
        if selected.get("stream"):
            selected["stream_handler"] = self.router_stream_handler.handle_stream_chunk
        return selected

    def _intent_signature(self, intent_name: str) -> tuple[int, int, int]:
        """Return a ``(handler_mtime, config_mtime, prompt_mtime)`` tuple for cache invalidation.

        A change in any of the three ``mtime_ns`` values means the cached
        handler instance is stale and must be recreated.  Missing files are
        represented as ``-1`` so a later creation also triggers a cache miss.
        """
        definition = self.registry.get(intent_name)
        prompt_path = definition.directory / "prompt.md"

        def _mtime_ns(path: Path) -> int:
            try:
                return path.stat().st_mtime_ns
            except FileNotFoundError:
                return -1

        return (
            _mtime_ns(definition.handler_path),
            _mtime_ns(definition.config_path),
            _mtime_ns(prompt_path),
        )

    def _get_or_create_handler(self, intent_name: str) -> BaseIntentHandler:
        """Return a cached handler instance, recreating it if source files have changed.

        Compares the current ``mtime_ns`` signature (handler, config, prompt)
        against the value stored at last instantiation.  A mismatch causes the
        handler to be re-instantiated from disk so hot-reloaded code takes
        effect on the next query without a process restart.
        """
        current_signature = self._intent_signature(intent_name)
        cached_handler = self._handler_instances.get(intent_name)
        cached_signature = self._handler_signatures.get(intent_name)

        # Cache hit: source files unchanged since last instantiation.
        if cached_handler is not None and cached_signature == current_signature:
            return cached_handler

        # Cache miss: instantiate fresh and update both caches.
        handler = self.registry.instantiate(
            intent_name,
            llm_client=self.llm_client,
            nucore_interface=self.nucore_interface,
        )
        self._handler_instances[intent_name] = handler
        self._handler_signatures[intent_name] = current_signature
        return handler

    def _reconcile_handler_cache(self) -> None:
        """Evict cached handler instances for intents that no longer exist in the registry.

        Called after every :meth:`refresh` to prevent stale references to
        handlers whose directories were deleted or renamed between scans.
        """
        valid_names = set(self.registry.names())
        stale_names = [name for name in self._handler_instances if name not in valid_names]
        for name in stale_names:
            self._handler_instances.pop(name, None)
            self._handler_signatures.pop(name, None)