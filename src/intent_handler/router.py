from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .base import LLMAdapter
from .loader import IntentHandlerRegistry
from .models import IntentDefinition, RouteResult


class IntentRouter:
    def __init__(self, registry: IntentHandlerRegistry, llm_client: LLMAdapter) -> None:
        self.registry = registry
        self.llm_client = llm_client

    def build_router_prompt(self) -> str:
        router_prompt_path = self.registry.runtime_assets_directory / "router" / "prompt.md"
        if not router_prompt_path.exists():
            raise FileNotFoundError(f"Router prompt template not found: {router_prompt_path}")

        with router_prompt_path.open("r", encoding="utf-8") as f:
            base_prompt = f.read().strip()

        definitions = self.registry.definitions()
        discovered_intents = self._build_discovered_intents(definitions)
        routing_patterns = self._build_routing_patterns(definitions)

        prompt = base_prompt.replace("<<DISCOVERED_INTENTS>>", discovered_intents)
        prompt = prompt.replace("<<ROUTING_PATTERNS>>", routing_patterns)

        expanded_prompt = self.registry.expand_common_module_placeholders(prompt)
        return "\n\n".join([expanded_prompt, self._build_router_output_contract()])

    async def route(self, query: str, llm_config_override: dict[str, Any] | None = None) -> RouteResult:
        router_llm_config = dict(self.registry.router_config().get("llm_config", {}))
        if llm_config_override:
            router_llm_config.update(llm_config_override)

        if self._supports_system_role(router_llm_config):
            messages = [
                {"role": "system", "content": self.build_router_prompt()},
                {"role": "user", "content": query.strip()},
            ]
        else:
            messages = [
                {
                    "role": "user",
                    "content": (
                        "SYSTEM INSTRUCTIONS:\n"
                        f"{self.build_router_prompt()}\n\n"
                        f"USER QUERY:\n{query.strip()}"
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

        confidence = payload.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence = None

        return RouteResult(
            intent=intent_name,
            confidence=confidence,
            notes=payload.get("notes"),
            raw_response=payload,
        )

    def _format_intent_block(self, definition: IntentDefinition) -> str:
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
        """Format discovered intents for the prompt."""
        intent_blocks = [self._format_intent_block(definition) for definition in definitions]
        return "\n\n".join(intent_blocks)
    
    def _build_routing_patterns(self, definitions: list[IntentDefinition]) -> str:
        """Build routing patterns from intent hints and examples."""
        patterns = []
        for definition in definitions:
            if definition.router_hints:
                hints_text = " / ".join(definition.router_hints)
                patterns.append(f"- **{definition.name}**: {hints_text}")
            elif definition.routing_examples:
                examples_text = " / ".join(definition.routing_examples[:2])  # First 2 examples
                patterns.append(f"- **{definition.name}**: {examples_text}")
            else:
                patterns.append(f"- **{definition.name}**: No specific patterns defined")
        return "\n".join(patterns)

    def _build_router_output_contract(self) -> str:
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
        tool_spec_path = self.registry.runtime_assets_directory / "router" / "tool_router.json"
        if not tool_spec_path.exists():
            raise FileNotFoundError(f"Router tool schema not found: {tool_spec_path}")
        with tool_spec_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _normalize_and_validate_route_payload(self, payload: dict[str, Any], query: str) -> dict[str, Any]:
        tool_spec = self._load_router_tool_spec()
        input_schema = tool_spec.get("input_schema", {})
        properties = input_schema.get("properties", {})
        required = input_schema.get("required", [])

        if not isinstance(payload, dict):
            raise ValueError(f"Router response must be a JSON object matching tool_router: {payload!r}")

        missing = [key for key in required if key not in payload]
        if missing:
            raise ValueError(f"Router response is missing required fields from tool_router: {missing}")

        if input_schema.get("additionalProperties") is False:
            extra_keys = sorted(set(payload) - set(properties))
            if extra_keys:
                raise ValueError(f"Router response contains unsupported tool_router fields: {extra_keys}")

        for field_name, field_schema in properties.items():
            if field_name not in payload:
                continue
            field_type = field_schema.get("type")
            if field_type == "string" and not isinstance(payload[field_name], str):
                raise ValueError(f"Router field '{field_name}' must be a string")

        normalized_payload = dict(payload)
        normalized_payload["user_query"] = query.strip()
        return normalized_payload

    @staticmethod
    def _coerce_route_payload(raw_response: Any) -> dict[str, Any]:
        if isinstance(raw_response, dict):
            return raw_response

        if isinstance(raw_response, str):
            text = raw_response.strip()
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                match = re.search(r"\{.*\}", text, flags=re.DOTALL)
                if match:
                    return json.loads(match.group(0))

        raise ValueError(f"Router response is not valid JSON: {raw_response!r}")

    def _supports_system_role(self, router_llm_config: dict[str, Any]) -> bool:

        explicit = router_llm_config.get("supports_system_role")
        if explicit is not None:
            return bool(explicit)

        provider_hint = " ".join(
            [
                str(getattr(self.llm_client, "provider_name", "") or ""),
                str(router_llm_config.get("provider", "") or ""),
                str(router_llm_config.get("model", "") or ""),
            ]
        ).lower()

        if "claude" in provider_hint or "anthropic" in provider_hint:
            return False

        return True