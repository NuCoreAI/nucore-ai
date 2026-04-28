from __future__ import annotations

import importlib.util
import inspect
import json
from pathlib import Path
from typing import Iterable

from .base import BaseIntentHandler
from .models import IntentDefinition
from .adapters import LLMAdapter
from .stream_handler import StreamHandler

class IntentHandlerRegistry:
    def __init__(self, root_directory: str | Path) -> None:
        self.root_directory = Path(root_directory).expanduser().resolve()
        self.runtime_assets_directory = Path(__file__).resolve().parent / "runtime_assets"
        self.common_modules_directory = Path(__file__).resolve().parent / "runtime_assets" / "common_modules"
        self.router_config_path = Path(__file__).resolve().parent / "runtime_assets" / "router" / "config.json"
        self._definitions: dict[str, IntentDefinition] = {}
        self._modules_cache: dict[str, object] = {}
        self._handler_class_cache: dict[tuple[Path, str | None], tuple[int, type[BaseIntentHandler]]] = {}
        self._stream_handler_class_cache: dict[tuple[Path, str | None], tuple[int, type[StreamHandler]]] = {}
        #self.refresh() # Moved refresh call to runtime to ensure directory monitor is set up first.

    def refresh(self) -> None:
        if not self.root_directory.exists():
            raise FileNotFoundError(f"Intent handler directory not found: {self.root_directory}")
        if not self.root_directory.is_dir():
            raise NotADirectoryError(f"Intent handler path is not a directory: {self.root_directory}")

        #invalidate the cache
        self._handler_class_cache = {}
        self._modules_cache = {}
        if self.common_modules_directory.exists() and self.common_modules_directory.is_dir():
            for module_file in sorted(self.common_modules_directory.glob("*.md")):
                module_name = module_file.stem
                with module_file.open("r", encoding="utf-8") as module_handle:
                    self._modules_cache[module_name] = module_handle.read()
        
        definitions: dict[str, IntentDefinition] = {}
        for child in sorted(self.root_directory.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            # Only treat directories with config.json as runnable intents.
            if not (child / "config.json").exists():
                continue
            definition = self._load_definition(child)
            definitions[definition.name] = definition

        if not definitions:
            raise ValueError(f"No intent handlers found in {self.root_directory}")

        self._validate_dependencies(definitions)
        self._definitions = definitions

    def names(self) -> list[str]:
        return list(self._definitions.keys())

    def definitions(self) -> list[IntentDefinition]:
        return list(self._definitions.values())

    def routable_definitions(self) -> list[IntentDefinition]:
        return [d for d in self._definitions.values() if d.config.get("routable", True)]

    def get(self, intent_name: str) -> IntentDefinition:
        try:
            return self._definitions[intent_name]
        except KeyError as exc:
            raise KeyError(f"Unknown intent handler '{intent_name}'") from exc

    def router_config(self) -> dict:
        if not self.router_config_path.exists():
            return {}
        with self.router_config_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def expand_common_module_placeholders(self, content: str) -> str:
        expanded_content = content
        for module_name, module_content in self._modules_cache.items():
            for placeholder in self._common_module_placeholders(module_name):
                if placeholder in expanded_content:
                    expanded_content = expanded_content.replace(placeholder, str(module_content))
        return expanded_content

    @staticmethod
    def _common_module_placeholders(module_name: str) -> tuple[str, ...]:
        placeholders = [
            f"<<{module_name}>>",
            f"<<nucore_{module_name}>>",
            f"<<nucore_{module_name}_rules>>",
        ]
        if module_name.endswith("s") and len(module_name) > 1:
            singular_name = module_name[:-1]
            placeholders.append(f"<<nucore_{singular_name}>>")
            placeholders.append(f"<<nucore_{singular_name}_rules>>")
        return tuple(dict.fromkeys(placeholders))

    def instantiate(
        self,
        intent_name: str,
        *,
        llm_client: LLMAdapter,
        nucore_interface=None,
    ) -> BaseIntentHandler:
        definition = self.get(intent_name)
        handler_class = self._load_handler_class(definition)
        return handler_class(definition=definition, llm_client=llm_client, nucore_interface=nucore_interface)

    def _load_definition(self, intent_directory: Path) -> IntentDefinition:
        config_path = intent_directory / "config.json"
        prompt_path = intent_directory / "prompt.md"

        with config_path.open("r", encoding="utf-8") as handle:
            config = json.load(handle)

        # Auto-discover tool files in this intent directory and merge them into config.
        discovered_tools = [file.name for file in intent_directory.glob("tool_*.json") if file.is_file()]
        discovered_tools = sorted(discovered_tools, key=lambda p: p)

        configured_tool_files = config.get("tool_files", [])
        if configured_tool_files is None or not isinstance(configured_tool_files, list):
            configured_tool_files = []

        merged_tool_files: list[str] = []
        seen_tool_files: set[str] = set()

        for tool_file in configured_tool_files:
            if not isinstance(tool_file, str):
                continue
            if tool_file in seen_tool_files:
                continue
            seen_tool_files.add(tool_file)
            merged_tool_files.append(tool_file)

        for tool_file in discovered_tools:
            if not isinstance(tool_file, str):
                continue
            if tool_file in seen_tool_files:
                continue
            seen_tool_files.add(tool_file)
            merged_tool_files.append(tool_file)

        config["tool_files"] = merged_tool_files

        handler_file = config.get("handler") #, "handler.py")
        if not isinstance(handler_file, str) or not handler_file.endswith(".py"):
            raise ValueError(
                f"Intent '{intent_directory.name}' has invalid handler '{handler_file}'. Expected a .py file name."
            )
        handler_path = intent_directory / handler_file
        paths =[config_path, prompt_path, handler_path]

        stream_handler_file = config.get("stream_handler", None)
        stream_handler_path = None
        if stream_handler_file:
            if not isinstance(stream_handler_file, str) or not handler_file.endswith(".py"):
                raise ValueError(
                    f"Intent '{intent_directory.name}' has invalid stream_handler '{stream_handler_file}'. Expected a .py file name."
                )
            stream_handler_path = intent_directory / stream_handler_file
            paths.append(stream_handler_path)

        missing_paths = [
            str(path.name)
            for path in paths 
            if not path.exists()
        ]
        if missing_paths:
            raise FileNotFoundError(
                f"Intent handler '{intent_directory.name}' is missing required files: {', '.join(missing_paths)}"
            )

        configured_intent = config.get("intent", intent_directory.name)
        if configured_intent != intent_directory.name:
            raise ValueError(
                f"Intent directory '{intent_directory.name}' does not match config intent '{configured_intent}'"
            )
        
        prompt_content = self.expand_common_module_placeholders(
            prompt_path.read_text(encoding="utf-8")
        )


        stream_handler_class = self._load_stream_handler_class(configured_intent, stream_handler_path, None)
        if stream_handler_class is not None: 
            stream_handler_class = stream_handler_class()

        return IntentDefinition(
            name=configured_intent,
            directory=intent_directory,
            config_path=config_path,
            prompt_content=prompt_content,
            handler_path=handler_path,
            stream_handler_path=stream_handler_path,
            description=config.get("description", ""),
#            handler_class=config.get("handler_class") if isinstance(config.get("handler_class"), str) and not str(config.get("handler_class")).endswith(".py") else None,
            handler_class=None,
            stream_handler_class = stream_handler_class, 
            previous_dependencies=list(config.get("previous_dependencies", [])),
            routing_examples=list(config.get("routing_examples", [])),
            router_hints=list(config.get("router_hints", [])),
            llm_config=dict(config.get("llm_config", {})),
            config=config,
        )

    def _validate_dependencies(self, definitions: dict[str, IntentDefinition]) -> None:
        for intent_name, definition in definitions.items():
            for dependency in definition.previous_dependencies:
                if dependency not in definitions:
                    raise ValueError(
                        f"Intent '{intent_name}' has unknown dependency '{dependency}'"
                    )
                if dependency == intent_name:
                    raise ValueError(
                        f"Intent '{intent_name}' cannot depend on itself"
                    )

        visit_state: dict[str, int] = {name: 0 for name in definitions}

        def dfs(name: str) -> None:
            state = visit_state[name]
            if state == 1:
                raise ValueError(f"Circular dependency detected at intent '{name}'")
            if state == 2:
                return

            visit_state[name] = 1
            definition = definitions[name]
            for dependency in definition.previous_dependencies:
                dfs(dependency)
            visit_state[name] = 2

        for name in definitions:
            dfs(name)

    #def _load_stream_handler_class(self, definition: IntentDefinition) -> type[StreamHandler]:
    def _load_stream_handler_class(self, intent_name, stream_handler_path: Path, stream_handler_class:str) -> type[StreamHandler]: 
        if stream_handler_path is None:
            return None 
        cache_key = (stream_handler_path, stream_handler_class)
        try:
            mtime_ns = stream_handler_path.stat().st_mtime_ns
        except FileNotFoundError:
            mtime_ns = -1

        cached_entry = self._stream_handler_class_cache.get(cache_key)
        if cached_entry and cached_entry[0] == mtime_ns:
            return cached_entry[1]

        module_name = f"intent_handler_dynamic_{intent_name}"
        spec = importlib.util.spec_from_file_location(module_name, stream_handler_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Unable to load stream handler module for intent '{intent_name}'")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        class_name = stream_handler_class
        if class_name:
            stream_handler_class = getattr(module, class_name, None)
            if stream_handler_class is None:
                raise ValueError(
                    f"Handler class '{class_name}' not found in {definition.stream_handler_path}"
                )
            if not inspect.isclass(stream_handler_class) or not issubclass(stream_handler_class, BaseIntentHandler):
                raise TypeError(
                    f"Handler class '{class_name}' in {stream_handler_path} must subclass BaseIntentHandler"
                )
            self.stream_handler_class_cache[cache_key] = (mtime_ns, stream_handler_class)
            return stream_handler_class

        candidates = list(self._iter_stream_handler_classes(module))
        if len(candidates) != 1:
            raise ValueError(
                f"Expected exactly one StreamHandler subclass in {stream_handler_path}, found {len(candidates)}"
            )
        selected_class = candidates[0]
        self._stream_handler_class_cache[cache_key] = (mtime_ns, selected_class)
        return selected_class

    def _load_handler_class(self, definition: IntentDefinition) -> type[BaseIntentHandler]:
        cache_key = (definition.handler_path, definition.handler_class)
        try:
            mtime_ns = definition.handler_path.stat().st_mtime_ns
        except FileNotFoundError:
            mtime_ns = -1

        cached_entry = self._handler_class_cache.get(cache_key)
        if cached_entry and cached_entry[0] == mtime_ns:
            return cached_entry[1]

        module_name = f"intent_handler_dynamic_{definition.name}"
        spec = importlib.util.spec_from_file_location(module_name, definition.handler_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Unable to load handler module for intent '{definition.name}'")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        class_name = definition.handler_class
        if class_name:
            handler_class = getattr(module, class_name, None)
            if handler_class is None:
                raise ValueError(
                    f"Handler class '{class_name}' not found in {definition.handler_path}"
                )
            if not inspect.isclass(handler_class) or not issubclass(handler_class, BaseIntentHandler):
                raise TypeError(
                    f"Handler class '{class_name}' in {definition.handler_path} must subclass BaseIntentHandler"
                )
            self._handler_class_cache[cache_key] = (mtime_ns, handler_class)
            return handler_class

        candidates = list(self._iter_handler_classes(module))
        if len(candidates) != 1:
            raise ValueError(
                f"Expected exactly one BaseIntentHandler subclass in {definition.handler_path}, found {len(candidates)}"
            )
        selected_class = candidates[0]
        self._handler_class_cache[cache_key] = (mtime_ns, selected_class)
        return selected_class

    @staticmethod
    def _iter_handler_classes(module) -> Iterable[type[BaseIntentHandler]]:
        for _, member in inspect.getmembers(module, inspect.isclass):
            if member is BaseIntentHandler:
                continue
            if issubclass(member, BaseIntentHandler):
                yield member

    @staticmethod
    def _iter_stream_handler_classes(module) -> Iterable[type[StreamHandler]]:
        for _, member in inspect.getmembers(module, inspect.isclass):
            if member is StreamHandler:
                continue
            if issubclass(member, StreamHandler):
                yield member