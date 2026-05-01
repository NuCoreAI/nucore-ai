from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Sequence


from .base import BaseIntentHandler
from .loader import IntentHandlerRegistry
from .models import IntentHandlerResult, RouteResult
from .router import IntentRouter
from .session_store import SessionStore
from .stream_handler import RouterStreamHandler, StreamHandler
from .adapters import LLMAdapter
from .directory_monitor import DirectoryMonitor
from nucore import NuCoreInterface
from utils import get_logger
logger = get_logger(__name__)

SubscriberCallback = Callable[[Any], None]


def _apply_runtime_overrides(
    runtime_config: dict[str, Any],
    provider: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    model_url: str | None = None,
) -> dict[str, Any]:
    """Layer CLI-supplied overrides on top of a loaded runtime config dict.

    Mutates a shallow copy of ``runtime_config`` (never the original) so the
    caller can safely discard or retry with the original dict.

    When ``provider`` names a key not already in ``supported_llms``, a minimal
    placeholder entry is created so downstream code can still resolve it.  The
    ``default_llm`` (and ``router_llm``) are updated to ``provider`` whenever
    an explicit provider is given so all subsequent routing uses the CLI choice.

    Args:
        runtime_config: Parsed ``runtime_config.json`` dict.
        provider:        Provider alias override (e.g. ``"claude"``).
        api_key:         API key override for the selected provider.
        model:           Model name override.
        model_url:       Base URL override for OpenAI-compatible endpoints.

    Returns:
        A new dict with the overrides applied.

    Raises:
        ValueError: If no provider can be resolved and ``supported_llms`` is empty.
    """
    config = dict(runtime_config)
    supported_llms = dict(config.get("supported_llms", {}))

    selected_key = provider or config.get("default_llm") or next(iter(supported_llms), None)
    if not selected_key:
        raise ValueError("No provider specified and no default_llm in runtime_config")

    if selected_key not in supported_llms:
        if provider:
            # Auto-create a minimal entry so the CLI can override any provider
            # even when it is not pre-declared in runtime_config.json.
            supported_llms[selected_key] = {
                "provider": selected_key,
                "model": None,
                "url": None,
                "params": {},
            }
        else:
            raise ValueError(f"Provider '{selected_key}' not found in runtime_config.supported_llms")

    llm_cfg = dict(supported_llms.get(selected_key, {}))
    if model:
        llm_cfg["model"] = model
    if api_key:
        llm_cfg["api_key"] = api_key
    if model_url:
        llm_cfg["url"] = model_url

    supported_llms[selected_key] = llm_cfg
    config["supported_llms"] = supported_llms
    if provider:
        config["default_llm"] = selected_key
        # Keep router/provider selection aligned with explicit CLI provider overrides.
        config["router_llm"] = selected_key

    return config

def _load_runtime_config(
    path: str,
    stream_handler: StreamHandler,
    provider: str,
    api_key: str,
    model: str,
    model_url: str | None = None,
) -> dict[str, Any]:
    """Load, validate, and return the runtime config dict.

    Reads the JSON file at ``path`` (falling back to a safe empty config when
    the file does not exist), injects the stream-handler callback into every
    LLM entry that does not already declare one, and then applies CLI overrides
    via :func:`_apply_runtime_overrides`.

    Args:
        path:           Absolute or relative path to ``runtime_config.json``.
        stream_handler: Active :class:`~stream_handler.StreamHandler` instance
                        whose ``handle_stream_chunk`` callback is wired into
                        each LLM config entry.  Pass ``None`` to disable
                        streaming for all providers.
        provider:       See :func:`_apply_runtime_overrides`.
        api_key:        See :func:`_apply_runtime_overrides`.
        model:          See :func:`_apply_runtime_overrides`.
        model_url:      See :func:`_apply_runtime_overrides`.

    Returns:
        Fully resolved runtime config dict.

    Raises:
        ValueError: If the JSON file does not contain a top-level object.
    """
    runtime_config_path = (
        Path(path).expanduser().resolve()
        if path
        else Path(__file__).resolve().parent / "runtime_assets" / "runtime_config.json"
    )
    runtime_config = {
            "supported_llms": {},
            "default_llm": None,
            "router_llm": None,
    }
    if not runtime_config_path.exists():
        return runtime_config
    
    with runtime_config_path.open("r", encoding="utf-8") as handle:
        runtime_config = json.load(handle)

    if not isinstance(runtime_config, dict):
        raise ValueError("Runtime config must be a JSON object at the top level")

    # Wire the stream handler callback into every LLM entry that does not
    # already declare its own; set stream=False when no handler is available.
    for _, llm_cfg in runtime_config.get("supported_llms", {}).items():
        if not isinstance(llm_cfg, dict):
            continue
        cfg_stream_handler = llm_cfg.get("stream_handler", None)
        if cfg_stream_handler is None:
            if stream_handler is not None:
                llm_cfg["stream"] = True
                llm_cfg["stream_handler"] = stream_handler.handle_stream_chunk
            else:
                llm_cfg["stream"] = False

    runtime_config = _apply_runtime_overrides(
        runtime_config,
        provider=provider,
        api_key=api_key,
        model=model,
        model_url=model_url,
    )
    return runtime_config


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
    - Merges three-layer LLM config (runtime defaults → per-intent ``llm_override``
      → CLI-supplied overrides) before each handler call.

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
        runtime_provider: str | None = None,
        runtime_api_key: str | None = None,
        runtime_model: str | None = None,
        runtime_model_url: str | None = None
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
            runtime_config_path:      Path to ``runtime_config.json``.
            stream_handler:           Optional stream handler whose callback is
                                      wired into LLM configs for token streaming.
            runtime_provider:         Optional CLI provider override forwarded
                                      to :func:`_load_runtime_config`.
            runtime_api_key:          Optional CLI API key override.
            runtime_model:            Optional CLI model name override.
            runtime_model_url:        Optional CLI base-URL override.

        Raises:
            ValueError: If any of ``llm_client``, ``nucore_interface``, or
                        ``runtime_config_path`` is ``None``.
        """
        if llm_client is None or nucore_interface is None or runtime_config_path is None:
            raise ValueError("llm_client, nucore_interface, and runtime_config_path are required")
        self.intent_handler_directory = Path(intent_handler_directory).expanduser().resolve()
        self.registry = IntentHandlerRegistry(self.intent_handler_directory)
        self.llm_client = llm_client
        self.nucore_interface = nucore_interface
        self.router = IntentRouter(self.registry, llm_client)
        self.runtime_config_path = runtime_config_path
        self.stream_handler = stream_handler
        # Separate stream handler used exclusively by the router so its chunk
        # counter does not interfere with per-intent handler stream state.
        self.router_stream_handler = RouterStreamHandler()
        self._runtime_provider = runtime_provider
        self._runtime_api_key = runtime_api_key
        self._runtime_model = runtime_model
        self._runtime_model_url = runtime_model_url
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
        ``runtime_config.json`` with current overrides, validates the config,
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
            provider=self._runtime_provider or "",
            api_key=self._runtime_api_key or "",
            model=self._runtime_model or "",
            model_url=self._runtime_model_url,
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
        *,
        framework_context: str | None = None,
        session_id: str | None = None,
    ) -> IntentHandlerResult:
        """Feed a tool/agent result back through the ``translate_agent_output`` intent.

        Called after :meth:`handle_query` returns tool results that need to be
        converted into a final user-facing text response by the LLM.

        Args:
            query:             Stringified tool results to process.
            framework_context: Optional extra context string forwarded to the handler.
            session_id:        Session ID for history look-up (currently unused
                               for agent responses — history is not appended).

        Returns:
            :class:`~models.IntentHandlerResult` from ``translate_agent_output``.
        """
        default_max_turns: int = int(self.runtime_config.get("default_max_turns", 20))
        active_llm_key = self.runtime_config.get("default_llm")
        active_llm_cfg = self.runtime_config.get("supported_llms", {}).get(active_llm_key or "", {})
        max_turns: int = int(active_llm_cfg.get("max_turns", default_max_turns))

        history = self.session_store.get(session_id, max_turns=max_turns) if session_id else None

        intent_name = "translate_agent_output"
        handler = self._get_or_create_handler(intent_name)
        step_llm_config = self._resolve_runtime_llm_config(intent_name)
        handler.set_runtime_llm_config(step_llm_config)
        handler.set_current_history(history)

        return await handler.handle(
            query,
            route_result=None,
            framework_context=framework_context,
            dependency_outputs={},
        )

    async def handle_query(
        self,
        query: str,
        *,
        framework_context: str | None = None,
        session_id: str | None = None,
    ) -> IntentHandlerResult:
        """Route a query, execute the dependency chain, and return the final result.

        Full execution pipeline:
        1. Retrieve (or create) the conversation history for ``session_id``.
        2. Route the query to an intent via :meth:`route`.
        3. Resolve the topologically-ordered dependency chain for the selected intent.
        4. For each intent in the chain: resolve its LLM config, inject history,
           create a step-specific :class:`~models.RouteResult`, and call the handler.
        5. Accumulate ``dependency_outputs`` so downstream handlers can read
           results from earlier steps.
        6. After the chain completes, append the final text response to the
           session history (when a session ID is provided).

        Args:
            query:             Raw user input.
            framework_context: Optional extra context string forwarded to every
                               handler in the chain.
            session_id:        Optional session identifier.  When provided,
                               conversation history is loaded before the call
                               and updated after it.

        Returns:
            :class:`~models.IntentHandlerResult` from the last handler in the chain.
        """
        default_max_turns: int = int(self.runtime_config.get("default_max_turns", 20))
        active_llm_key = self.runtime_config.get("default_llm")
        active_llm_cfg = self.runtime_config.get("supported_llms", {}).get(active_llm_key or "", {})
        max_turns: int = int(active_llm_cfg.get("max_turns", default_max_turns))

        history = self.session_store.get(session_id, max_turns=max_turns) if session_id else None

        route_result = await self.route(query, history=history)
        execution_chain = self._resolve_execution_chain(route_result.intent)

        dependency_outputs: dict[str, Any] = {}
        last_result: IntentHandlerResult | None = None

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
                )
            )

            effective_query = step_route_result.resolved_query or query

            result = await handler.handle(
                effective_query,
                route_result=step_route_result,
                framework_context=framework_context,
                dependency_outputs=dependency_outputs,
            )
            if result:
                result.set_route_result(route_result=step_route_result)
                dependency_outputs[intent_name] = result
                last_result = result

        if last_result :
            if session_id is not None:
                response_text = last_result.get_text_output() or ""
                self.session_store.get(session_id).append(query=query, response=response_text)

        return last_result

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
        """Return a topologically-ordered list of intents to execute for ``target_intent``.

        Performs a depth-first traversal of the ``previous_dependencies`` graph
        starting from ``target_intent``.  Each dependency appears before the
        intent that declares it.

        Raises:
            ValueError: If a cycle is detected in the dependency graph.
        """
        ordered: list[str] = []
        visited: set[str] = set()
        active: set[str] = set()  # Intents currently in the DFS call stack (grey nodes).

        def visit(intent_name: str) -> None:
            if intent_name in active:
                raise ValueError(f"Circular dependency in execution chain at '{intent_name}'")
            if intent_name in visited:
                return

            active.add(intent_name)
            definition = self.registry.get(intent_name)
            for dependency in definition.previous_dependencies:
                visit(dependency)
            active.remove(intent_name)

            visited.add(intent_name)
            ordered.append(intent_name)

        visit(target_intent)
        return ordered

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
        - ``supported_llms`` is a dict.
        - ``default_llm`` and ``router_llm`` (when set) name keys in ``supported_llms``.
        - Every intent's ``llm_override`` (when set) names a key in ``supported_llms``.

        Raises:
            ValueError: On any consistency violation.
        """
        supported = self.runtime_config.get("supported_llms", {})
        default_llm = self.runtime_config.get("default_llm")
        router_llm = self.runtime_config.get("router_llm")

        if not isinstance(supported, dict):
            raise ValueError("runtime_config.supported_llms must be a dictionary")
        if default_llm is not None and default_llm not in supported:
            raise ValueError(
                f"runtime_config.default_llm '{default_llm}' is not in supported_llms"
            )
        if router_llm is not None and router_llm not in supported:
            raise ValueError(
                f"runtime_config.router_llm '{router_llm}' is not in supported_llms"
            )

        for definition in self.registry.definitions():  # validate all intents, not just routable ones
            llm_key = definition.config.get("llm_override")
            if llm_key is None:
                continue
            if not isinstance(llm_key, str) or not llm_key.strip():
                raise ValueError(
                    f"Intent '{definition.name}' llm_override must be a non-empty string when provided"
                )
            if llm_key not in supported:
                raise ValueError(
                    f"Intent '{definition.name}' llm_override '{llm_key}' is not in runtime_config.supported_llms"
                )

    def _resolve_runtime_llm_config(self, intent_name: str) -> dict[str, Any]:
        """Build the merged LLM config dict for a specific intent handler call.

        Selection priority:
        1. ``config["llm_override"]`` declared in the intent's ``config.json``.
        2. ``runtime_config["default_llm"]`` global default.
        3. First key in ``supported_llms`` as a last resort.

        The selected entry's ``params`` sub-dict is merged first, then the
        top-level entry keys overwrite it, and ``llm_key`` is injected so the
        dispatch adapter can identify which client to use.

        Raises:
            ValueError: If the resolved key is not in ``supported_llms``.
        """
        supported = self.runtime_config.get("supported_llms", {})
        if not supported:
            return {}

        definition = self.registry.get(intent_name)
        selected_key = definition.config.get("llm_override")
        if selected_key is None:
            selected_key = self.runtime_config.get("default_llm")
            if selected_key is None:
                selected_key = next(iter(supported.keys()))
        elif not isinstance(selected_key, str) or not selected_key.strip():
            raise ValueError(
                f"Intent '{intent_name}' llm_override must be a non-empty string when provided"
            )
        if selected_key not in supported:
            raise ValueError(
                f"Intent '{intent_name}' llm_override '{selected_key}' is not in runtime_config.supported_llms"
            )

        selected = dict(supported.get(selected_key, {}))
        params = selected.pop("params", {})
        merged = dict(params if isinstance(params, dict) else {})
        merged.update(selected)
        merged["llm_key"] = selected_key
        return merged

    def _resolve_router_llm_config(self) -> dict[str, Any]:
        """Build the merged LLM config dict used by the intent router.

        Prefers ``runtime_config["router_llm"]`` over ``default_llm``.  The
        router stream handler callback is injected when streaming is enabled so
        the router's token output does not mix with the handler's stream state.

        Raises:
            ValueError: If the resolved key is not in ``supported_llms``.
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
        params = selected.pop("params", {})
        merged = dict(params if isinstance(params, dict) else {})
        merged.update(selected)
        merged["llm_key"] = selected_key
        # Override the stream handler with the router-specific one so chunk
        # counts for routing calls are tracked separately from handler calls.
        if merged.get("stream"):
            merged["stream_handler"] = self.router_stream_handler.handle_stream_chunk
        return merged

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