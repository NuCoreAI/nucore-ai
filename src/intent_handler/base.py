from __future__ import annotations

import importlib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .models import ConversationHistory, IntentDefinition, IntentHandlerResult, RouteResult
from .adapters import LLMAdapter
from .session_store import SessionStore
from nucore import NuCoreInterface
from utils.logger import _write_debug_prompt
from rag import RAGData, DedupeDevices 



class BaseIntentHandler(ABC):
    """Abstract base class for all intent handlers.

    Each concrete handler (e.g. ``CommandControlStatusIntentHandler``) must
    implement :meth:`handle`.  All shared plumbing — prompt rendering, message
    assembly, LLM invocation, tool spec loading, and config resolution — lives
    here so subclasses stay focused on their domain logic.

    Lifecycle (managed by :class:`~intent_handler.runtime.IntentRuntime`):
    1. Instantiated once per intent and cached for the session lifetime.
    2. :meth:`set_runtime_llm_config` is called before each invocation so the
       handler uses the provider/model selected for this request.
    3. :meth:`set_current_history` injects the active conversation turn list.
    4. :meth:`handle` is awaited; it should call :meth:`build_messages` then
       :meth:`call_llm` and return an :class:`~intent_handler.models.IntentHandlerResult`.
    """

    def __init__(
        self,
        definition: IntentDefinition,
        llm_client: LLMAdapter,
        nucore_interface: NuCoreInterface | None = None,
    ) -> None:
        """Initialise the handler.

        Args:
            definition:       Parsed intent definition including prompt text,
                              config dict, tool file paths, and directory.
            llm_client:       Adapter used to call the LLM for this handler.
            nucore_interface: Optional NuCore backend interface, injected by the
                              runtime so handlers can query/control devices.
        """
        self.definition = definition
        self.llm_client = llm_client
        self.nucore_interface = nucore_interface
        self._runtime_llm_config: dict[str, Any] = {}
        # Lazy-loaded list of ToolSpec objects declared in config["tool_files"].
        self._tool_specs_cache = None
        # Per-provider cache of exported (provider-native) tool dicts to avoid
        # re-serialising the same specs on every call.
        self._exported_tools_cache: dict[str, list[dict[str, Any]] | None] = {}
        self._current_history: ConversationHistory | None = None

    # ------------------------------------------------------------------
    # Read-only properties forwarded from the intent definition
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        """Unique intent name as declared in the handler directory."""
        return self.definition.name

    @property
    def prompt_text(self) -> str:
        """Raw prompt template string loaded from the intent's prompt file."""
        return self.definition.prompt_content

    @property
    def config(self) -> dict[str, Any]:
        """Intent-level config dict (from ``config.json`` in the handler dir)."""
        return self.definition.config

    @property
    def directory(self) -> Path:
        """Filesystem path to this handler's directory."""
        return self.definition.directory

    # ------------------------------------------------------------------
    # Session / runtime state setters
    # ------------------------------------------------------------------

    def set_current_history(self, history: ConversationHistory | None) -> None:
        """Inject the active conversation history before each invocation.

        Called by the runtime so that :meth:`build_messages` can include prior
        turns without requiring subclasses to manage history explicitly.
        """
        self._current_history = history

    # ------------------------------------------------------------------
    # Message assembly
    # ------------------------------------------------------------------

    async def build_messages(
        self,
        query: str,
        *,
        framework_context: str | None = None,
        route_result: RouteResult | None = None,
        extra_user_sections: dict[str, str] | None = None,
        history: ConversationHistory | None = None,
    ) -> list[dict[str, str]]:
        """Assemble the final message list to send to the LLM.

        The method handles two provider layouts:

        * **System-role providers** (OpenAI, Gemini, Grok, …): the rendered
          prompt is placed in a ``{"role": "system"}`` message, followed by
          history turns, then the current user turn.

        * **Non-system-role providers** (Claude via raw messages API): the
          rendered prompt is prepended to the first user message so the model
          still receives it as contextual instruction.

        Args:
            query:              The user's current query string.
            framework_context:  Optional context injected by the framework
                                (e.g. time, location) prepended to the user turn.
            route_result:       Route result from the router, forwarded to prompt
                                rendering for placeholder substitution.
            extra_user_sections: Additional named sections appended to the user
                                 turn before the query (key → content mapping).
            history:            Explicit history override; defaults to
                                :attr:`_current_history` when ``None``.

        Returns:
            Ordered list of ``{"role", "content"}`` dicts ready for the LLM.
        """
        if history is None:
            history = self._current_history

        resolved_prompt_text = await self.render_prompt_text(
            query,
            framework_context=framework_context,
            route_result=route_result,
        )

        # Build current user turn: optional framework context, any extra
        # sections, history (with consistent labels), and the user query —
        # each separated by a visual rule.
        user_parts = []
        if framework_context:
            user_parts.append(f"---\n# FRAMEWORK CONTEXT:\n{framework_context.strip()}")
        if extra_user_sections:
            for section_name, section_value in extra_user_sections.items():
                if section_value:
                    user_parts.append(f"---\n# {section_name.upper()}:\n{section_value.strip()}")
        formatted_history = SessionStore._format_history_content(history)
        if formatted_history:
            user_parts.append(formatted_history)
        user_parts.append(f"\n\n---\n# USER QUERY:\n{query.strip()}")
        current_user_content = "\n\n".join(user_parts).strip()

        if self._supports_system_role():
            # Standard layout: system prompt → current user turn (which already includes history block).
            messages = [
                {"role": "system", "content": resolved_prompt_text},
                {"role": "user", "content": current_user_content},
            ]
        else:
            # Non-system-role layout: inline system instructions with the user content.
            messages = [
                {
                    "role": "user",
                    "content": (
                        "---\n# SYSTEM INSTRUCTIONS:\n"
                        f"{resolved_prompt_text}\n\n"
                        f"{current_user_content}"
                    ),
                }
            ]

        return messages


    # ------------------------------------------------------------------
    # Prompt rendering
    # ------------------------------------------------------------------

    async def get_prompt_runtime_replacements(
        self,
        query: str,
        *,
        framework_context: str | None = None,
        route_result: RouteResult | None = None,
    ) -> dict[str, str]:
        """Return a mapping of prompt placeholder keys to their runtime values.

        Subclasses override this to inject dynamic content (e.g. device lists,
        RAG results) into the prompt template.  Keys may be bare strings or
        already wrapped in ``<<…>>`` — both forms are accepted.

        The default implementation returns an empty dict (no substitutions).
        """
        return {}

    async def get_tool_result_context(
        self,
        registry: Any,
        query: str | None = None,
        framework_context: str | None = None,
        route_result: RouteResult | None = None,
    ) -> Any: 
        """
         If the subclass has provided a specific prompt for tool results by overriding get_tool_result_prompt, this method will renders it by replacing the nucore and runtime templates and return the rendered context. If get_tool_result_prompt returns None, this method return nothing. 

        """
        context = await self.get_tool_result_prompt()
        if context is None:
            return ""
        context = registry.expand_common_module_placeholders(context) 
        context = context.strip()
        context = await self.render_prompt_text(
            query=query,
            prompt=context,
            framework_context=framework_context,
            route_result=route_result,
        )

        return context

    
    async def get_tool_result_prompt(self)->str:
        """
            If subclass wants to provide specific prompt for tool results that is different from the main prompt, they can override this method. Otherwise, it will simply use the main prompt. 
            If used, the subclass can include nucore templates such as <<nucore_definitions>> or <<nucore_common_rules>> as well as specific runtime templates that will be replaced
            using get_prompt_runtime_replacements. Example:
            <<nucore_definitions>>
            <<nucore_common_rules>>

            ---
            # DEVICE STRUCTURE
            <<runtime_device_structure>> 
        """
        return None


    async def render_prompt_text(
        self,
        query: str,
        *,
        prompt: str | None = None,
        framework_context: str | None = None,
        route_result: RouteResult | None = None,
    ) -> str:
        """Render the prompt template by substituting all runtime placeholders.

        Calls :meth:`get_prompt_runtime_replacements` to collect substitution
        values, then replaces each ``<<key>>`` occurrence in the raw prompt text.
        Missing or ``None`` values are replaced with an empty string.
        If a specific prompt is provided via the ``prompt`` parameter, it will be used instead of the default prompt text.

        Returns the fully rendered, stripped prompt string.
        """
        rendered = prompt or self.prompt_text
        replacements = await self.get_prompt_runtime_replacements(
            query,
            framework_context=framework_context,
            route_result=route_result,
        )

        for raw_key, value in replacements.items():
            placeholder = self._normalize_prompt_placeholder(raw_key)
            rendered = rendered.replace(placeholder, "" if value is None else str(value))

        return rendered.strip()

    @staticmethod
    def _normalize_prompt_placeholder(key: str) -> str:
        """Ensure ``key`` is wrapped in ``<<…>>`` for template substitution.

        Accepts keys that are already in ``<<key>>`` form and bare keys alike.
        """
        key_text = str(key or "").strip()
        if key_text.startswith("<<") and key_text.endswith(">>"):
            return key_text
        return f"<<{key_text}>>"

    # ------------------------------------------------------------------
    # LLM config / provider helpers
    # ------------------------------------------------------------------

    def _supports_system_role(self) -> bool:
        """Return True if the active provider accepts a dedicated system role message.

        The ``supports_system_role`` config key overrides automatic detection.
        The adapters in this runtime normalize system-role messages for all
        supported providers, so the default is True unless explicitly disabled.
        """
        llm_config = self.get_effective_llm_config()

        # Explicit config override takes precedence over heuristic detection.
        explicit = llm_config.get("supports_system_role")
        if explicit is not None:
            return bool(explicit)

        return True

    def set_runtime_llm_config(self, llm_config: dict[str, Any] | None) -> None:
        """Replace the runtime LLM config for the next invocation.

        Called by the runtime before each :meth:`handle` call so that the
        correct provider/model/key/stream settings are in effect.
        """
        self._runtime_llm_config = dict(llm_config or {})

    def get_effective_llm_config(self, call_config: dict[str, Any] | None = None) -> dict[str, Any]:
        """Merge LLM config layers into a single flat dict.

        Priority order (highest → lowest):
        1. ``call_config`` — per-call overrides supplied by the subclass.
        2. ``definition.llm_config`` — intent-level defaults from ``config.json``.
        3. ``_runtime_llm_config`` — provider/model injected by the runtime.
        """
        merged_config: dict[str, Any] = {}
        merged_config.update(self._runtime_llm_config or {})
        merged_config.update(self.definition.llm_config or {})
        if call_config:
            merged_config.update(call_config)
        return merged_config

    def get_effective_provider(self, call_config: dict[str, Any] | None = None) -> str:
        """Resolve the active provider name from the merged LLM config.

        Falls back to the adapter's ``provider_name`` attribute, and ultimately
        to ``"claude"`` if neither config nor adapter provides a value.
        """
        llm_config = self.get_effective_llm_config(call_config)
        provider = llm_config.get("provider") or llm_config.get("llm")
        if provider:
            return str(provider)
        return str(getattr(self.llm_client, "provider_name", "claude") or "claude")

    # ------------------------------------------------------------------
    # Tool helpers
    # ------------------------------------------------------------------

    def get_declared_tool_paths(self) -> list[Path]:
        """Return absolute paths for all tool JSON files declared in config.

        Tool file paths in ``config["tool_files"]`` are relative to the
        handler's directory and are resolved here.
        """
        return [self.directory / path for path in self.config.get("tool_files", [])]

    def get_tool_specs(self):
        """Return the list of :class:`~adapters.ToolSpec` objects for this handler.

        Results are cached after the first load so repeated calls within a
        session do not re-read files from disk.
        """
        if self._tool_specs_cache is None:
            tool_paths = self.get_declared_tool_paths()
            if tool_paths:
                self._tool_specs_cache = LLMAdapter.tools_spec_from_files(tool_paths)
            else:
                self._tool_specs_cache = []
        return self._tool_specs_cache

    def get_tool_names(self) -> list[str]:
        """Return the names of all tools declared for this handler."""
        return [spec.name for spec in self.get_tool_specs()]

    def build_provider_tools(self, call_config: dict[str, Any] | None = None) -> list[dict[str, Any]] | None:
        """Export tool specs in the active provider's native format.

        Results are cached per provider so the same specs are not serialised
        multiple times when the same provider is used across turns.

        Returns ``None`` when no tools are declared (signals the LLM to skip
        tool-use entirely).
        """
        provider = self.get_effective_provider(call_config)
        if provider not in self._exported_tools_cache:
            tool_specs = self.get_tool_specs()
            if not tool_specs:
                self._exported_tools_cache[provider] = None
            else:
                self._exported_tools_cache[provider] = self.llm_client.export_tools(tool_specs)
        return self._exported_tools_cache[provider]

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    async def call_llm(
        self,
        *,
        messages: list[dict[str, str]],
        config: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        expect_json: bool = False,
    ) -> IntentHandlerResult:
        """Invoke the LLM and wrap the raw response in an :class:`IntentHandlerResult`.

        Args:
            messages:    Assembled message list from :meth:`build_messages`.
            config:      Optional per-call config overrides (merged on top of
                         the effective config).
            tools:       Explicit provider-native tool list; when ``None`` the
                         handler's own declared tools are used automatically.
            expect_json: Passed through to the adapter to enable JSON mode where
                         the provider supports it.

        Returns:
            An :class:`IntentHandlerResult` whose ``output`` holds the raw
            adapter response.  ``route_result`` is intentionally left ``None``
            here — callers set it after the fact.
        """
        merged_config = self.get_effective_llm_config(config)
        # Attach the stream handler from the definition so streaming works
        # without subclasses needing to wire it manually.
        if self.definition.stream_handler_class is not None:
            merged_config["stream_handler"] = self.definition.stream_handler_class.handle_stream_chunk
        resolved_tools = tools if tools is not None else self.build_provider_tools(merged_config)
        await _write_debug_prompt(self.definition.name, messages)
        response = await self.llm_client.generate(
            messages=messages,
            config=merged_config or None,
            tools=resolved_tools,
            expect_json=expect_json,
        )
        return IntentHandlerResult(
            intent=self.name,
            output=response,
            route_result=None,  # set by the caller after the chain step completes
            stream_handler=self.definition.stream_handler_class if self.definition.stream_handler_class else None,
        )

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    async def handle(
        self,
        query: str,
        *,
        route_result: RouteResult | None = None,
        framework_context: str | None = None,
        raw_response: IntentHandlerResult | None = None,
        tool_calls: list[ToolCall] | None = None,
    ) -> IntentHandlerResult:
        """Execute the intent and return the result.

        Subclasses must implement this method.  The typical pattern is:

        .. code-block:: python

            # Runtime builds messages/calls the LLM first, then passes the
            # raw response and extracted tool calls here for post-processing.
            response = raw_response
            # post-process response (e.g. execute tools, transform output)
            response.set_route_result(route_result=route_result)
            return response

        Args:
            query:              The (possibly resolved) user query string.
            route_result:       Route metadata from the router.
            framework_context:  Optional runtime context string.
            raw_response:       LLM response returned by ``call_llm``.
            tool_calls:         Tool calls extracted from ``raw_response``.

        Returns:
            An :class:`IntentHandlerResult` carrying the final output.
        """
        raise NotImplementedError
    
    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_rags_from_candidates(self, candidate_devices:list[dict[str, Any]]) -> str:
        """Filter the full RAG store to the devices nominated by the LLM tool call.

        Iterates the candidate list in ``candidate_devices``, discards entries below
        the configured score threshold, then extracts and de-duplicates the
        matching RAG documents.

        Args:
            candidate_devices: A list of dicts with ``device_id`` and ``score`` keys.

        Returns:
            De-duplicated device document string ready for downstream prompt
            injection, or an empty string when no candidates pass the threshold.
        """
        full_rags = self.nucore_interface.rags
        if not candidate_devices or not full_rags:
            return RAGData()

        # Threshold is configurable per-intent; fall back to 0.80.
        score_threshold = self.config.get("threshold", 0.80)

        # Collect device IDs that meet the relevance threshold.
        matched_candidate_ids: set[str] = set()
        for d in candidate_devices:
            if float(d.get('score', 0)) >= score_threshold:
                try:
                    matched_candidate_ids.add(d['device_id'])
                except Exception:
                    pass

        # Build a filtered RAGData containing only the matched devices.
        filtered_rags = RAGData(documents=[], ids=[])
        for idx, id_ in enumerate(full_rags["ids"]):
            if id_ in matched_candidate_ids:
                filtered_rags.add_document(
                    full_rags["documents"][idx],
                    full_rags["embeddings"][idx],
                    id_,
                    full_rags["metadatas"][idx],
                )

        rag_docs = filtered_rags["documents"]
        if not rag_docs:
            return ""

        # Concatenate documents then de-duplicate overlapping content.
        device_docs = ""
        for rag_doc in rag_docs:
            device_docs += "\n" + rag_doc

        deduper = DedupeDevices()
        return deduper.dedupe(device_docs)