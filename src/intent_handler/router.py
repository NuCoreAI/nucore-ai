from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .adapters import LLMAdapter
from .loader import IntentHandlerRegistry
from .models import ConversationHistory, IntentDefinition, RouteResult


class IntentRouter:
    """Routes an incoming user query to the most appropriate intent handler.

    The router constructs a prompt from a Markdown template
    (``runtime_assets/router/prompt.md``) that embeds the list of available
    intents and their routing hints/examples, then calls an LLM and expects a
    JSON response that conforms to the ``tool_router.json`` schema.

    The resulting :class:`~models.RouteResult` contains the selected intent
    name, an optional confidence score, optional reasoning notes, and the
    (possibly rewritten) user query.

    Prompt assembly pipeline:
    1. Load ``router/prompt.md`` and substitute ``<<DISCOVERED_INTENTS>>`` and
       ``<<ROUTING_PATTERNS>>`` placeholders.
    2. Expand any common module placeholders via the registry.
    3. Append the ``# OUTPUT SCHEMA`` contract so the LLM knows the exact JSON
       shape to return.
    4. Prepend recent conversation history when supplied.
    """

    def __init__(self, registry: IntentHandlerRegistry, llm_client: LLMAdapter) -> None:
        """Initialise the router.

        Args:
            registry:   Registry of loaded intent definitions used to build
                        the prompt and validate the selected intent name.
            llm_client: LLM adapter used to call the routing model.
        """
        self.registry = registry
        self.llm_client = llm_client

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def build_router_prompt(self) -> str:
        """Build the complete system prompt for the routing LLM.

        Loads the base template from ``runtime_assets/router/prompt.md``,
        substitutes intent and pattern blocks, expands common module
        placeholders, and appends the JSON output contract.

        Returns:
            The fully assembled router system prompt string.

        Raises:
            FileNotFoundError: If ``router/prompt.md`` is missing.
        """
        router_prompt_path = self.registry.runtime_assets_directory / "router" / "prompt.md"
        if not router_prompt_path.exists():
            raise FileNotFoundError(f"Router prompt template not found: {router_prompt_path}")

        with router_prompt_path.open("r", encoding="utf-8") as f:
            base_prompt = f.read().strip()

        definitions = self.registry.routable_definitions()
        discovered_intents = self._build_discovered_intents(definitions)
        routing_patterns = self._build_routing_patterns(definitions)

        prompt = base_prompt.replace("<<DISCOVERED_INTENTS>>", discovered_intents)
        prompt = prompt.replace("<<ROUTING_PATTERNS>>", routing_patterns)

        # Expand any shared module placeholders (e.g. <<nucore_rules>>).
        expanded_prompt = self.registry.expand_common_module_placeholders(prompt)
        # Append the output schema contract so the LLM knows the required JSON shape.
        return "\n\n".join([expanded_prompt, self._build_router_output_contract()])

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    async def route(
        self,
        query: str,
        llm_config_override: dict[str, Any] | None = None,
        history: ConversationHistory | None = None,
    ) -> RouteResult:
        """Route ``query`` to the best-matching intent and return a :class:`~models.RouteResult`.

        Merges the router's own ``llm_config`` (from ``router/config.json``)
        with any caller-supplied overrides, assembles a message list that
        optionally includes recent conversation history, calls the LLM, and
        validates the returned JSON payload against the ``tool_router`` schema.

        Args:
            query:               The raw user input to route.
            llm_config_override: Optional dict of LLM config values that take
                                 precedence over the router's own config.
            history:             Optional conversation history to prepend to
                                 the user message so the LLM can consider
                                 context when resolving ambiguous queries.

        Returns:
            A :class:`~models.RouteResult` with at minimum ``intent`` and
            ``resolved_query`` populated.

        Raises:
            ValueError: If the LLM returns an invalid response or selects an
                        intent name not present in the registry.
        """
        # Start from the router's own llm_config, then layer caller overrides on top.
        router_llm_config = dict(self.registry.router_config().get("llm_config", {}))
        if llm_config_override:
            router_llm_config.update(llm_config_override)

        # Build a formatted history block (most recent turn first) for the user message.
        history_block = ""
        if history and history.turns:
            lines = ["────────────────────────────────\n# CONVERSATION HISTORY (most recent first):"]
            for turn in reversed(history.turns):
                lines.append(f"User: {turn.query.strip()}")
                lines.append(f"Assistant: {turn.response.strip()}")
                lines.append("")
            history_block = "\n".join(lines).strip()

        def _make_user_content(router_prompt: str) -> str:
            """Combine optional history and the user query into one content string."""
            parts = []
            if history_block:
                parts.append(history_block)
            parts.append(f"────────────────────────────────\n# USER QUERY:\n{query.strip()}")
            return "\n\n".join(parts)

        router_prompt = self.build_router_prompt()
        # Build message list using the provider-appropriate layout.
        if self._supports_system_role(router_llm_config):
            messages = [
                {"role": "system", "content": router_prompt},
                {"role": "user", "content": _make_user_content(router_prompt)},
            ]
        else:
            # Claude and similar providers that do not use a separate system role:
            # inline the system instructions at the start of the user turn.
            messages = [
                {
                    "role": "user",
                    "content": (
                        "SYSTEM INSTRUCTIONS:\n"
                        f"{router_prompt}\n\n"
                        f"{_make_user_content(router_prompt)}"
                    ),
                }
            ]

        raw_response = await self.llm_client.generate(
            messages=messages,
            config=router_llm_config,
            expect_json=True,
        )
        payload = self._coerce_route_payload(raw_response)
        payload = self._normalize_and_validate_route_payload(payload, query)

        intent_name = payload.get("intent")
        if intent_name not in self.registry.names():
            raise ValueError(
                f"Router selected unknown intent '{intent_name}'. Available intents: {self.registry.names()}"
            )

        # Confidence may be numeric or a string representation; normalise to float.
        confidence = payload.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence = None

        return RouteResult(
            intent=intent_name,
            confidence=confidence,
            notes=payload.get("notes"),
            resolved_query=payload.get("user_query") or query,
            raw_response=payload,
        )

    # ------------------------------------------------------------------
    # Prompt block builders
    # ------------------------------------------------------------------

    def _format_intent_block(self, definition: IntentDefinition) -> str:
        """Render a single intent as a YAML-like bullet block for the prompt."""
        lines = [f"- intent: {definition.name}"]
        if definition.description:
            lines.append(f"  description: {definition.description}")
        if definition.previous_dependencies:
            lines.append("  previous_dependencies (ordered):")
            lines.extend(f"    - {intent_name}" for intent_name in definition.previous_dependencies)
        if definition.routing_examples:
            lines.append("  examples:")
            lines.extend(f"    - {example}" for example in definition.routing_examples)
        if definition.router_hints:
            lines.append("  hints:")
            lines.extend(f"    - {hint}" for hint in definition.router_hints)
        return "\n".join(lines)

    def _build_discovered_intents(self, definitions: list[IntentDefinition]) -> str:
        """Format all routable intent definitions into the ``<<DISCOVERED_INTENTS>>`` block."""
        intent_blocks = [self._format_intent_block(definition) for definition in definitions]
        return "\n\n".join(intent_blocks)

    def _build_routing_patterns(self, definitions: list[IntentDefinition]) -> str:
        """Build the ``<<ROUTING_PATTERNS>>`` block from each intent's hints or examples.

        Prefers ``router_hints`` over ``routing_examples``; uses the first two
        examples when hints are absent; falls back to a "No specific patterns
        defined" placeholder.
        """
        patterns = []
        for definition in definitions:
            if definition.router_hints:
                hints_text = " / ".join(definition.router_hints)
                patterns.append(f"- **{definition.name}**: {hints_text}")
            elif definition.routing_examples:
                # Only include the first two examples to keep the prompt concise.
                examples_text = " / ".join(definition.routing_examples[:2])
                patterns.append(f"- **{definition.name}**: {examples_text}")
            else:
                patterns.append(f"- **{definition.name}**: No specific patterns defined")
        return "\n".join(patterns)

    def _build_router_output_contract(self) -> str:
        """Append the ``# OUTPUT SCHEMA`` section to the prompt.

        Inlines the ``tool_router.json`` input schema so the LLM knows the
        exact JSON structure it must return.
        """
        tool_spec = self._load_router_tool_spec()
        return "\n".join(
            [
                "# OUTPUT SCHEMA",
                f"Return JSON only that conforms exactly to `{tool_spec.get('name', 'tool_router')}` input_schema.",
                json.dumps(tool_spec.get("input_schema", {}), indent=2),
                "Do not include markdown fences or extra keys.",
            ]
        )

    def _load_router_tool_spec(self) -> dict[str, Any]:
        """Load and return the ``tool_router.json`` schema dict.

        Raises:
            FileNotFoundError: If ``runtime_assets/router/tool_router.json`` is missing.
        """
        tool_spec_path = self.registry.runtime_assets_directory / "router" / "tool_router.json"
        if not tool_spec_path.exists():
            raise FileNotFoundError(f"Router tool schema not found: {tool_spec_path}")
        with tool_spec_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    # ------------------------------------------------------------------
    # Payload validation
    # ------------------------------------------------------------------

    def _normalize_and_validate_route_payload(self, payload: dict[str, Any], query: str) -> dict[str, Any]:
        """Validate and normalise a parsed route payload against the ``tool_router`` schema.

        Checks that all required fields are present, that string fields are
        strings, and (when ``additionalProperties`` is ``false``) that no
        unexpected keys are present.  ``user_query`` is backfilled from
        ``query`` when the router omits it.

        Args:
            payload: Parsed JSON dict returned by the routing LLM.
            query:   Original user query used as a fallback for ``user_query``.

        Returns:
            The normalised payload dict.

        Raises:
            ValueError: On type mismatches or missing required fields.
        """
        tool_spec = self._load_router_tool_spec()
        input_schema = tool_spec.get("input_schema", {})
        properties = input_schema.get("properties", {})
        required = input_schema.get("required", [])
        # These optional fields are always permitted even when additionalProperties is false.
        allowed_optional_fields = {"confidence", "notes"}

        if not isinstance(payload, dict):
            raise ValueError(f"Router response must be a JSON object matching tool_router: {payload!r}")

        normalized_payload = dict(payload)
        # Backfill user_query so downstream code always has a resolved query string.
        if "user_query" not in normalized_payload or not normalized_payload["user_query"]:
            normalized_payload["user_query"] = query.strip()

        missing = [key for key in required if key not in normalized_payload]
        if missing:
            raise ValueError(f"Router response is missing required fields from tool_router: {missing}")

        if input_schema.get("additionalProperties") is False:
            extra_keys = sorted(set(normalized_payload) - set(properties) - allowed_optional_fields)
            if extra_keys:
                raise ValueError(f"Router response contains unsupported tool_router fields: {extra_keys}")

        for field_name, field_schema in properties.items():
            if field_name not in normalized_payload:
                continue
            field_type = field_schema.get("type")
            if field_type == "string" and not isinstance(normalized_payload[field_name], str):
                raise ValueError(f"Router field '{field_name}' must be a string")

        notes = normalized_payload.get("notes")
        if notes is not None and not isinstance(notes, str):
            raise ValueError("Router field 'notes' must be a string when provided")

        confidence = normalized_payload.get("confidence")
        if confidence is not None and not isinstance(confidence, (int, float, str)):
            raise ValueError("Router field 'confidence' must be numeric or string when provided")

        return normalized_payload

    # ------------------------------------------------------------------
    # Response coercion
    # ------------------------------------------------------------------

    def _coerce_route_payload(self, raw_response: Any) -> dict[str, Any]:
        """Attempt to extract a valid ``{"intent": ...}`` dict from any LLM response shape.

        Handles three cases in order:
        1. ``raw_response`` is already a dict with an ``"intent"`` key — return as-is.
        2. ``raw_response`` is a dict with a text/content field — recurse on the text.
        3. ``raw_response`` is a string — try ``json.loads``, then regex JSON extraction,
           then intent name inference, then a generic fallback intent.

        Raises:
            ValueError: If no valid payload can be extracted.
        """
        if isinstance(raw_response, dict):
            if isinstance(raw_response.get("intent"), str):
                return raw_response

            # Unwrap nested text/content fields produced by some adapter responses.
            extracted_text = self._extract_text_response(raw_response)
            if extracted_text is not None:
                return self._coerce_route_payload(extracted_text)

        if isinstance(raw_response, str):
            text = raw_response.strip()
            try:
                payload = json.loads(text)
                if isinstance(payload, dict):
                    return payload
            except json.JSONDecodeError:
                # Fall back to regex extraction when the string contains
                # surrounding prose or markdown fences.
                match = re.search(r"\{.*\}", text, flags=re.DOTALL)
                if match:
                    payload = json.loads(match.group(0))
                    if isinstance(payload, dict):
                        return payload

            # Last resort: try to find a known intent name mentioned in the text.
            inferred_intent = self._infer_intent_from_text(text)
            if inferred_intent is not None:
                return {"intent": inferred_intent}

            # If a generic fallback intent is registered, use it rather than raising.
            fallback_intent = self._fallback_text_intent()
            if fallback_intent is not None:
                return {
                    "intent": fallback_intent,
                    "notes": f"Router returned non-JSON text: {text}",
                }

        raise ValueError(f"Router response is not valid JSON: {raw_response!r}")

    @staticmethod
    def _extract_text_response(raw_response: dict[str, Any]) -> str | None:
        """Pull a plain-text string out of a provider response dict.

        Checks ``"text"`` first, then ``"content"`` (string or list-of-blocks).
        Returns ``None`` when no non-empty text can be found.
        """
        text = raw_response.get("text")
        if isinstance(text, str) and text.strip():
            return text

        content = raw_response.get("content")
        if isinstance(content, str) and content.strip():
            return content

        # Handle Claude-style content list: [{"type": "text", "text": "..."}]
        if isinstance(content, list):
            text_parts = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    text_parts.append(item["text"])
            joined = "\n".join(part.strip() for part in text_parts if part and part.strip()).strip()
            if joined:
                return joined

        return None

    def _infer_intent_from_text(self, text: str) -> str | None:
        """Try to infer a registered intent name from free-form router text.

        First checks for an exact case-insensitive match, then scans the text
        for any known intent name using word-boundary regex, returning the
        first (leftmost) match.

        Returns ``None`` when no intent name can be found.
        """
        normalized = text.strip()
        if not normalized:
            return None

        names = self.registry.names()
        lowered_map = {name.lower(): name for name in names}

        # Exact match (case-insensitive).
        direct = lowered_map.get(normalized.lower())
        if direct is not None:
            return direct

        # Scan for the leftmost occurrence of any intent name in the text.
        matches: list[tuple[int, str]] = []
        lowered_text = normalized.lower()
        for name in names:
            pattern = rf"(?<!\w){re.escape(name.lower())}(?!\w)"
            match = re.search(pattern, lowered_text)
            if match:
                matches.append((match.start(), name))

        if not matches:
            return None

        matches.sort(key=lambda item: item[0])
        return matches[0][1]

    def _fallback_text_intent(self) -> str | None:
        """Return a generic fallback intent name if one is registered.

        Checks for ``"general_help"`` then ``"general"`` in that order.
        Returns ``None`` when neither is registered.
        """
        for candidate in ("general_help", "general"):
            if candidate in self.registry.names():
                return candidate
        return None

    # ------------------------------------------------------------------
    # Provider helpers
    # ------------------------------------------------------------------

    def _supports_system_role(self, router_llm_config: dict[str, Any]) -> bool:
        """Return ``True`` if the routing provider accepts a ``system`` role message.

        Checks ``router_llm_config["supports_system_role"]`` first (explicit
        override).  Falls back to a heuristic: Claude / Anthropic providers do
        not use a dedicated system role, all others do.
        """
        explicit = router_llm_config.get("supports_system_role")
        if explicit is not None:
            return bool(explicit)

        # Build a combined hint string from the adapter's provider_name and the config.
        provider_hint = " ".join(
            [
                str(getattr(self.llm_client, "provider_name", "") or ""),
                str(router_llm_config.get("provider", "") or ""),
                str(router_llm_config.get("model", "") or ""),
            ]
        ).lower()

        # Claude/Anthropic embed system instructions inside the first user message.
        if "claude" in provider_hint or "anthropic" in provider_hint:
            return False

        return True