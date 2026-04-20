from __future__ import annotations

import importlib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Protocol

from .nucore_interface import NuCoreInterface 
from .models import IntentDefinition, IntentHandlerResult, RouteResult
from .adapters import LLMAdapter, ToolSpec, ToolCall
prompt_debug_output=True

class BaseIntentHandler(ABC):
    def __init__(
        self,
        definition: IntentDefinition,
        llm_client: LLMAdapter,
        nucore_interface: NuCoreInterface | None = None,
    ) -> None:
        self.definition = definition
        self.llm_client = llm_client
        self.nucore_interface = nucore_interface
        self._runtime_llm_config: dict[str, Any] = {}
        self._tool_specs_cache = None
        self._exported_tools_cache: dict[str, list[dict[str, Any]] | None] = {}

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def prompt_text(self) -> str:
        return self.definition.prompt_content

    @property
    def config(self) -> dict[str, Any]:
        return self.definition.config

    @property
    def directory(self) -> Path:
        return self.definition.directory

    def build_messages(
        self,
        query: str,
        *,
        dependency_outputs:IntentHandlerResult | str | dict[str, Any] | None = None, 
        framework_context: str | None = None,
        route_result: RouteResult | None = None,
        extra_user_sections: dict[str, str] | None = None,
    ) -> list[dict[str, str]]:
        resolved_prompt_text = self.render_prompt_text(
            query,
            dependency_outputs=dependency_outputs,
            framework_context=framework_context,
            route_result=route_result,
        )
        user_parts = []
        if route_result:
            user_parts.append(f"────────────────────────────────\n# ROUTER RESULT:\n*intent={route_result.intent}\n*confidence={route_result.confidence}\n*notes={route_result.notes or ''}")

        if framework_context:
            user_parts.append(f"────────────────────────────────\n# FRAMEWORK CONTEXT:\n{framework_context.strip()}")

        if extra_user_sections:
            for section_name, section_value in extra_user_sections.items():
                if section_value:
                    user_parts.append(f"────────────────────────────────\n# {section_name.upper()}:\n{section_value.strip()}")
        
        user_parts.append(f"────────────────────────────────\n# USER QUERY:\n{query.strip()}")


        if self._supports_system_role():
            if prompt_debug_output:
                with open("/tmp/nucore.prompt.md", "w") as f:
                    f.write(f"{resolved_prompt_text}\n\n")
                    for idx, part in enumerate(user_parts):
                        f.write(part+"\n\n")
            return [
                {"role": "system", "content": resolved_prompt_text},
                {"role": "user", "content": "\n\n".join(user_parts).strip()},
            ]

        # Claude-style clients often reject "system" as a chat message role.
        user_content = "\n\n".join(user_parts).strip()
        if prompt_debug_output:
            with open("/tmp/nucore.prompt.md", "w") as f:
                f.write(f"SYSTEM INSTRUCTIONS:\n{resolved_prompt_text}\n\n")
                f.write(user_content)

        return [
            {
                "role": "user",
                "content": (
                    "SYSTEM INSTRUCTIONS:\n"
                    f"{resolved_prompt_text}\n\n"
                    f"{user_content}"
                ),
            }
        ]

    def get_prompt_runtime_replacements(
        self,
        query: str,
        *,
        dependency_outputs:IntentHandlerResult | None = None,
        framework_context: str | None = None,
        route_result: RouteResult | None = None,
    ) -> dict[str, str]:
        return {}

    def render_prompt_text(
        self,
        query: str,
        *,
        dependency_outputs:IntentHandlerResult | str | dict[str, Any] | None = None, 
        framework_context: str | None = None,
        route_result: RouteResult | None = None,
    ) -> str:
        rendered = self.prompt_text
        replacements = self.get_prompt_runtime_replacements(
            query,
            dependency_outputs=dependency_outputs,
            framework_context=framework_context,
            route_result=route_result,
        )

        for raw_key, value in replacements.items():
            placeholder = self._normalize_prompt_placeholder(raw_key)
            rendered = rendered.replace(placeholder, "" if value is None else str(value))

        return rendered.strip()

    @staticmethod
    def _normalize_prompt_placeholder(key: str) -> str:
        key_text = str(key or "").strip()
        if key_text.startswith("<<") and key_text.endswith(">>"):
            return key_text
        return f"<<{key_text}>>"

    def _supports_system_role(self) -> bool:
        llm_config = self.get_effective_llm_config()

        explicit = llm_config.get("supports_system_role")
        if explicit is not None:
            return bool(explicit)

        provider_hint = " ".join(
            [
                str(getattr(self.llm_client, "provider_name", "") or ""),
                str(llm_config.get("provider", "") or ""),
                str(llm_config.get("model", "") or ""),
            ]
        ).lower()

        if "claude" in provider_hint or "anthropic" in provider_hint:
            return False

        return True

    def set_runtime_llm_config(self, llm_config: dict[str, Any] | None) -> None:
        self._runtime_llm_config = dict(llm_config or {})

    def get_effective_llm_config(self, call_config: dict[str, Any] | None = None) -> dict[str, Any]:
        merged_config: dict[str, Any] = {}
        merged_config.update(self._runtime_llm_config or {})
        merged_config.update(self.definition.llm_config or {})
        if call_config:
            merged_config.update(call_config)
        return merged_config

    def get_effective_provider(self, call_config: dict[str, Any] | None = None) -> str:
        llm_config = self.get_effective_llm_config(call_config)
        provider = llm_config.get("provider") or llm_config.get("llm")
        if provider:
            return str(provider)
        return str(getattr(self.llm_client, "provider_name", "claude") or "claude")

    def get_declared_tool_paths(self) -> list[Path]:
        return [self.directory / path for path in self.config.get("tool_files", [])]

    def get_tool_specs(self):
        if self._tool_specs_cache is None:
            tool_paths = self.get_declared_tool_paths()
            if tool_paths:
                self._tool_specs_cache = LLMAdapter.tools_spec_from_files(tool_paths)
            else:
                self._tool_specs_cache = []
        return self._tool_specs_cache

    def get_tool_names(self) -> list[str]:
        return [spec.name for spec in self.get_tool_specs()]

    def build_provider_tools(self, call_config: dict[str, Any] | None = None) -> list[dict[str, Any]] | None:
        provider = self.get_effective_provider(call_config)
        if provider not in self._exported_tools_cache:
            tool_specs = self.get_tool_specs()
            if not tool_specs:
                self._exported_tools_cache[provider] = None
            else:
                self._exported_tools_cache[provider] = self.llm_client.export_tools(tool_specs)
        return self._exported_tools_cache[provider]

    async def call_llm(
        self,
        *,
        messages: list[dict[str, str]],
        config: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        expect_json: bool = False,
    ) -> IntentHandlerResult | str | dict[str, Any]:
        merged_config = self.get_effective_llm_config(config)
        resolved_tools = tools if tools is not None else self.build_provider_tools(merged_config)
        response = await self.llm_client.generate(
            messages=messages,
            config=merged_config or None,
            tools=resolved_tools,
            expect_json=expect_json,
        )
        return IntentHandlerResult(
            intent=self.name,
            output=response,
            route_result=None, # these should be set by the caller if relevant, not here,
            metadata=None # these should be set by the caller if relevant, not here
        )

    @abstractmethod
    async def handle(
        self,
        query: str,
        *,
        route_result: RouteResult | None = None,
        framework_context: str | None = None,
        dependency_outputs: IntentHandlerResult | str | dict[str, Any] | None = None,
    ) -> IntentHandlerResult:
        raise NotImplementedError
    