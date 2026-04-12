from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nucore import NuCoreBackendAPI

from .base import BaseIntentHandler
from .base import LLMAdapter
from .loader import IntentHandlerRegistry
from .models import IntentHandlerResult, RouteResult
from .router import IntentRouter
from .nucore_interface import NuCoreInterface


class IntentRuntime:
    def __init__(
        self,
        intent_handler_directory: str | Path,
        *,
        llm_client: LLMAdapter,
        backend_api: NuCoreBackendAPI | None = None,
        runtime_config_path: str | Path | None = None,
    ) -> None:
        self.intent_handler_directory = Path(intent_handler_directory).expanduser().resolve()
        self.registry = IntentHandlerRegistry(self.intent_handler_directory)
        self.llm_client = llm_client
        self.nucore_interface = NuCoreInterface(nucore_api=backend_api)
        self.router = IntentRouter(self.registry, llm_client)
        self.runtime_config_path = (
            Path(runtime_config_path).expanduser().resolve()
            if runtime_config_path
            else self.registry.runtime_assets_directory / "runtime_config.json"
        )
        self.runtime_config = self._load_runtime_config()
        self._handler_instances: dict[str, BaseIntentHandler] = {}
        self._handler_signatures: dict[str, tuple[int, int, int]] = {}
        self._validate_runtime_config()

    def refresh(self) -> None:
        self.registry.refresh()
        self.runtime_config = self._load_runtime_config()
        self._validate_runtime_config()
        self._reconcile_handler_cache()

    async def route(self, query: str) -> RouteResult:
        self.refresh()
        router_llm_config = self._resolve_router_llm_config()
        return await self.router.route(query, llm_config_override=router_llm_config)

    async def handle_query(
        self,
        query: str,
        *,
        framework_context: str | None = None,
    ) -> IntentHandlerResult:
        route_result = await self.route(query)
        execution_chain = self._resolve_execution_chain(route_result.intent)

        dependency_outputs: dict[str, Any] = {}
        last_result: IntentHandlerResult | None = None

        for intent_name in execution_chain:
            handler = self._get_or_create_handler(intent_name)
            step_llm_config = self._resolve_runtime_llm_config(intent_name)
            handler.set_runtime_llm_config(step_llm_config)

            step_context = self._build_framework_context(
                framework_context=framework_context,
                dependency_outputs=dependency_outputs,
            )

            step_route_result = (
                route_result
                if intent_name == route_result.intent
                else RouteResult(
                    intent=intent_name,
                    confidence=route_result.confidence,
                    notes=f"Dependency step for '{route_result.intent}'",
                    raw_response=route_result.raw_response,
                )
            )

            outcome = await handler.handle(
                query,
                route_result=step_route_result,
                framework_context=step_context,
            )

            if isinstance(outcome, IntentHandlerResult):
                result = outcome
            else:
                result = handler.as_result(outcome, route_result=step_route_result)

            dependency_outputs[intent_name] = {
                "intent": result.intent,
                "output": self._safe_json_data(result.output),
                "metadata": self._safe_json_data(result.metadata),
                "llm": self._safe_json_data(step_llm_config),
            }
            last_result = result

        if last_result is None:
            raise ValueError("Execution chain produced no result")

        return last_result

    def available_intents(self) -> list[str]:
        self.refresh()
        return self.registry.names()

    def router_prompt(self) -> str:
        self.refresh()
        return self.router.build_router_prompt()

    def _resolve_execution_chain(self, target_intent: str) -> list[str]:
        ordered: list[str] = []
        visited: set[str] = set()
        active: set[str] = set()

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

    def _build_framework_context(
        self,
        *,
        framework_context: str | None,
        dependency_outputs: dict[str, Any],
    ) -> str | None:
        if not dependency_outputs:
            return framework_context

        dependencies_json = json.dumps(dependency_outputs, indent=2)
        dependency_block = f"DEPENDENCY OUTPUTS (ordered pipeline history):\n{dependencies_json}"

        if framework_context and framework_context.strip():
            return f"{framework_context.strip()}\n\n{dependency_block}"
        return dependency_block

    def _safe_json_data(self, value: Any) -> Any:
        try:
            json.dumps(value)
            return value
        except TypeError:
            return str(value)

    def _load_runtime_config(self) -> dict[str, Any]:
        if not self.runtime_config_path.exists():
            return {
                "supported_llms": {},
                "default_llm": None,
                "router_llm": None,
            }

        with self.runtime_config_path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)

        return {
            "supported_llms": dict(loaded.get("supported_llms", {})),
            "default_llm": loaded.get("default_llm"),
            "router_llm": loaded.get("router_llm"),
        }

    def _validate_runtime_config(self) -> None:
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
        return merged

    def _intent_signature(self, intent_name: str) -> tuple[int, int, int]:
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
        current_signature = self._intent_signature(intent_name)
        cached_handler = self._handler_instances.get(intent_name)
        cached_signature = self._handler_signatures.get(intent_name)

        if cached_handler is not None and cached_signature == current_signature:
            return cached_handler

        handler = self.registry.instantiate(
            intent_name,
            llm_client=self.llm_client,
            nucore_interface=self.nucore_interface,
        )
        self._handler_instances[intent_name] = handler
        self._handler_signatures[intent_name] = current_signature
        return handler

    def _reconcile_handler_cache(self) -> None:
        valid_names = set(self.registry.names())
        stale_names = [name for name in self._handler_instances if name not in valid_names]
        for name in stale_names:
            self._handler_instances.pop(name, None)
            self._handler_signatures.pop(name, None)