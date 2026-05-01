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
    """Discovers, loads, and caches intent handler definitions from a directory tree.

    Directory layout expected under ``root_directory``::

        <root_directory>/
          <intent_name>/
            config.json        — required: intent metadata and LLM settings
            prompt.md          — required: prompt template (may contain <<placeholders>>)
            handler.py         — required: BaseIntentHandler subclass
            stream_handler.py  — optional: StreamHandler subclass
            tool_*.json        — optional: auto-discovered tool spec files

    Common prompt modules are loaded from
    ``runtime_assets/common_modules/*.md`` and substituted into prompt
    templates on load via :meth:`expand_common_module_placeholders`.

    Handler Python modules are loaded with ``importlib.util`` at runtime so
    they can be hot-reloaded without restarting the process.  Both handler and
    stream-handler classes are keyed by ``(path, class_name)`` and the file's
    ``mtime_ns`` so stale cached classes are automatically evicted when a file
    changes on disk.
    """

    def __init__(self, root_directory: str | Path) -> None:
        """Initialise the registry.

        Args:
            root_directory: Absolute or relative path to the directory that
                            contains one sub-directory per intent handler.
                            The path is resolved to an absolute form immediately.
        """
        self.root_directory = Path(root_directory).expanduser().resolve()
        self.runtime_assets_directory = Path(__file__).resolve().parent / "runtime_assets"
        self.common_modules_directory = Path(__file__).resolve().parent / "runtime_assets" / "common_modules"
        self.router_config_path = Path(__file__).resolve().parent / "runtime_assets" / "router" / "config.json"
        self._definitions: dict[str, IntentDefinition] = {}
        self._modules_cache: dict[str, object] = {}
        # Cache key: (file_path, explicit_class_name | None) → (mtime_ns, class)
        self._handler_class_cache: dict[tuple[Path, str | None], tuple[int, type[BaseIntentHandler]]] = {}
        self._stream_handler_class_cache: dict[tuple[Path, str | None], tuple[int, type[StreamHandler]]] = {}
        # NOTE: refresh() is intentionally NOT called here.  The runtime calls
        # it after the directory monitor is configured to avoid a race between
        # the initial scan and the first monitor poll.

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Re-scan the root directory and reload all intent definitions.

        Clears the handler class and common-module caches, re-reads every
        ``config.json`` / ``prompt.md`` pair found in sub-directories, expands
        common module placeholders, and validates the resulting dependency
        graph for cycles and unknown references.

        Raises:
            FileNotFoundError: If ``root_directory`` does not exist.
            NotADirectoryError: If ``root_directory`` is not a directory.
            ValueError: If no runnable intents are found, or if dependency
                        validation fails.
        """
        if not self.root_directory.exists():
            raise FileNotFoundError(f"Intent handler directory not found: {self.root_directory}")
        if not self.root_directory.is_dir():
            raise NotADirectoryError(f"Intent handler path is not a directory: {self.root_directory}")

        # Invalidate module-level caches so hot-reloaded files take effect.
        self._handler_class_cache = {}
        self._modules_cache = {}

        # Load common markdown modules used as prompt placeholders.
        if self.common_modules_directory.exists() and self.common_modules_directory.is_dir():
            for module_file in sorted(self.common_modules_directory.glob("*.md")):
                module_name = module_file.stem
                with module_file.open("r", encoding="utf-8") as module_handle:
                    self._modules_cache[module_name] = module_handle.read()

        definitions: dict[str, IntentDefinition] = {}
        for child in sorted(self.root_directory.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            # Only treat sub-directories that contain a config.json as runnable intents.
            if not (child / "config.json").exists():
                continue
            definition = self._load_definition(child)
            definitions[definition.name] = definition

        if not definitions:
            raise ValueError(f"No intent handlers found in {self.root_directory}")

        self._validate_dependencies(definitions)
        self._definitions = definitions

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def names(self) -> list[str]:
        """Return names of all loaded intent handlers."""
        return list(self._definitions.keys())

    def definitions(self) -> list[IntentDefinition]:
        """Return all loaded :class:`~models.IntentDefinition` objects."""
        return list(self._definitions.values())

    def routable_definitions(self) -> list[IntentDefinition]:
        """Return only definitions that the router is allowed to select.

        An intent is considered routable when ``config["routable"]`` is absent
        or explicitly set to ``True``.  Set it to ``False`` to create
        dependency-only intents that can never be chosen directly by the router.
        """
        return [d for d in self._definitions.values() if d.config.get("routable", True)]

    def get(self, intent_name: str) -> IntentDefinition:
        """Look up a single definition by name.

        Raises:
            KeyError: If no intent with ``intent_name`` has been loaded.
        """
        try:
            return self._definitions[intent_name]
        except KeyError as exc:
            raise KeyError(f"Unknown intent handler '{intent_name}'") from exc

    def router_config(self) -> dict:
        """Load and return the router config dict from ``runtime_assets/router/config.json``.

        Returns an empty dict when the file does not exist.
        """
        if not self.router_config_path.exists():
            return {}
        with self.router_config_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    # ------------------------------------------------------------------
    # Common module placeholder expansion
    # ------------------------------------------------------------------

    def expand_common_module_placeholders(self, content: str) -> str:
        """Replace all ``<<module_name>>`` style placeholders with module content.

        Iterates over every common module loaded from
        ``runtime_assets/common_modules/`` and substitutes all known placeholder
        variants (see :meth:`_common_module_placeholders`) found in ``content``.

        Args:
            content: Raw prompt template string to expand.

        Returns:
            The expanded string with all recognised placeholders replaced.
        """
        expanded_content = content
        for module_name, module_content in self._modules_cache.items():
            for placeholder in self._common_module_placeholders(module_name):
                if placeholder in expanded_content:
                    expanded_content = expanded_content.replace(placeholder, str(module_content))
        return expanded_content

    @staticmethod
    def _common_module_placeholders(module_name: str) -> tuple[str, ...]:
        """Generate all placeholder variants accepted for a given module name.

        For a module named ``rules`` the following placeholders are recognised:
        ``<<rules>>``, ``<<nucore_rules>>``, ``<<nucore_rules_rules>>``,
        ``<<nucore_rule>>``, ``<<nucore_rule_rules>>``.

        Duplicate entries are removed while preserving insertion order.
        """
        placeholders = [
            f"<<{module_name}>>",
            f"<<nucore_{module_name}>>",
            f"<<nucore_{module_name}_rules>>",
        ]
        # Add singular variants for module names that end in "s".
        if module_name.endswith("s") and len(module_name) > 1:
            singular_name = module_name[:-1]
            placeholders.append(f"<<nucore_{singular_name}>>")
            placeholders.append(f"<<nucore_{singular_name}_rules>>")
        return tuple(dict.fromkeys(placeholders))

    # ------------------------------------------------------------------
    # Instantiation
    # ------------------------------------------------------------------

    def instantiate(
        self,
        intent_name: str,
        *,
        llm_client: LLMAdapter,
        nucore_interface=None,
    ) -> BaseIntentHandler:
        """Instantiate the handler class for ``intent_name`` and return the instance.

        Args:
            intent_name:      Name of a previously loaded intent.
            llm_client:       LLM adapter to inject into the handler.
            nucore_interface: Optional NuCore backend interface to inject.

        Returns:
            A freshly constructed :class:`~base.BaseIntentHandler` subclass instance.
        """
        definition = self.get(intent_name)
        handler_class = self._load_handler_class(definition)
        return handler_class(definition=definition, llm_client=llm_client, nucore_interface=nucore_interface)

    # ------------------------------------------------------------------
    # Definition loading
    # ------------------------------------------------------------------

    def _load_definition(self, intent_directory: Path) -> IntentDefinition:
        """Parse ``config.json`` and ``prompt.md`` for a single intent directory.

        Also auto-discovers ``tool_*.json`` files in the directory and merges
        them with any explicitly listed ``config["tool_files"]``, deduplicating
        while preserving declaration order (explicit files first).

        Raises:
            ValueError: If the handler file is missing or mis-named, or if the
                        ``intent`` field in config.json does not match the
                        directory name.
            FileNotFoundError: If any required file (config, prompt, handler)
                               is missing.
        """
        config_path = intent_directory / "config.json"
        prompt_path = intent_directory / "prompt.md"

        with config_path.open("r", encoding="utf-8") as handle:
            config = json.load(handle)

        # Auto-discover tool spec files named tool_*.json and merge with any
        # explicitly configured tool_files, preserving explicit order first.
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

        handler_file = config.get("handler")
        if not isinstance(handler_file, str) or not handler_file.endswith(".py"):
            raise ValueError(
                f"Intent '{intent_directory.name}' has invalid handler '{handler_file}'. Expected a .py file name."
            )
        handler_path = intent_directory / handler_file
        paths = [config_path, prompt_path, handler_path]

        # Optionally load a custom stream handler module declared in config.
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

        # Enforce that config["intent"] matches the directory name so intent
        # identity is unambiguous across directory moves and renames.
        configured_intent = config.get("intent", intent_directory.name)
        if configured_intent != intent_directory.name:
            raise ValueError(
                f"Intent directory '{intent_directory.name}' does not match config intent '{configured_intent}'"
            )

        # Expand common module placeholders in the prompt text at load time so
        # the rendered prompt is ready to use without extra processing later.
        prompt_content = self.expand_common_module_placeholders(
            prompt_path.read_text(encoding="utf-8")
        )

        # Instantiate the stream handler class (if any) once at definition load
        # time so handler instances don't have to manage that themselves.
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
            handler_class=None,
            stream_handler_class=stream_handler_class,
            previous_dependencies=list(config.get("previous_dependencies", [])),
            routing_examples=list(config.get("routing_examples", [])),
            router_hints=list(config.get("router_hints", [])),
            llm_config=dict(config.get("llm_config", {})),
            config=config,
        )

    # ------------------------------------------------------------------
    # Dependency validation
    # ------------------------------------------------------------------

    def _validate_dependencies(self, definitions: dict[str, IntentDefinition]) -> None:
        """Check that all ``previous_dependencies`` references are valid and acyclic.

        Performs two passes:
        1. Reference check — every dependency name must exist in ``definitions``
           and cannot be the intent itself.
        2. Cycle detection — DFS over the dependency graph to find any strongly
           connected components (cycles raise ``ValueError``).

        Raises:
            ValueError: On an unknown dependency, self-dependency, or cycle.
        """
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

        # DFS state: 0 = unvisited, 1 = in current path (grey), 2 = done (black).
        visit_state: dict[str, int] = {name: 0 for name in definitions}

        def dfs(name: str) -> None:
            state = visit_state[name]
            if state == 1:
                raise ValueError(f"Circular dependency detected at intent '{name}'")
            if state == 2:
                return

            visit_state[name] = 1  # Mark as in-progress (grey).
            definition = definitions[name]
            for dependency in definition.previous_dependencies:
                dfs(dependency)
            visit_state[name] = 2  # Mark as complete (black).

        for name in definitions:
            dfs(name)

    # ------------------------------------------------------------------
    # Dynamic class loading
    # ------------------------------------------------------------------

    def _load_stream_handler_class(self, intent_name, stream_handler_path: Path, stream_handler_class: str) -> type[StreamHandler]:
        """Dynamically load a :class:`~stream_handler.StreamHandler` subclass from a file.

        The class is cached by ``(path, class_name)`` and invalidated when the
        file's ``mtime_ns`` changes so hot-reloads work without a process restart.

        Args:
            intent_name:          Name of the owning intent (used as module name).
            stream_handler_path:  Path to the ``.py`` file to load.  Returns
                                  ``None`` immediately when this is ``None``.
            stream_handler_class: Explicit class name to look up.  When falsy,
                                  the module must contain exactly one
                                  ``StreamHandler`` subclass.

        Raises:
            ImportError: If the module spec cannot be created.
            ValueError: If an explicit class name is not found, or if auto-
                        discovery finds != 1 candidate class.
            TypeError:  If the found class does not subclass
                        :class:`~base.BaseIntentHandler`.
        """
        if stream_handler_path is None:
            return None
        cache_key = (stream_handler_path, stream_handler_class)
        try:
            mtime_ns = stream_handler_path.stat().st_mtime_ns
        except FileNotFoundError:
            mtime_ns = -1

        # Return cached class if the file has not changed since last load.
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
            # Explicit class name: look it up directly and validate type.
            stream_handler_class = getattr(module, class_name, None)
            if stream_handler_class is None:
                raise ValueError(
                    f"Handler class '{class_name}' not found in {stream_handler_path}"
                )
            if not inspect.isclass(stream_handler_class) or not issubclass(stream_handler_class, BaseIntentHandler):
                raise TypeError(
                    f"Handler class '{class_name}' in {stream_handler_path} must subclass BaseIntentHandler"
                )
            self.stream_handler_class_cache[cache_key] = (mtime_ns, stream_handler_class)
            return stream_handler_class

        # Auto-discovery: module must expose exactly one StreamHandler subclass.
        candidates = list(self._iter_stream_handler_classes(module))
        if len(candidates) != 1:
            raise ValueError(
                f"Expected exactly one StreamHandler subclass in {stream_handler_path}, found {len(candidates)}"
            )
        selected_class = candidates[0]
        self._stream_handler_class_cache[cache_key] = (mtime_ns, selected_class)
        return selected_class

    def _load_handler_class(self, definition: IntentDefinition) -> type[BaseIntentHandler]:
        """Dynamically load a :class:`~base.BaseIntentHandler` subclass from a file.

        Uses the same ``(path, class_name) → (mtime_ns, class)`` caching
        strategy as :meth:`_load_stream_handler_class`.

        Args:
            definition: The :class:`~models.IntentDefinition` whose
                        ``handler_path`` and optional ``handler_class`` name
                        are used for loading.

        Raises:
            ImportError: If the module spec cannot be created.
            ValueError: If an explicit class name is not found, or if
                        auto-discovery finds != 1 candidate class.
            TypeError:  If the found class does not subclass
                        :class:`~base.BaseIntentHandler`.
        """
        cache_key = (definition.handler_path, definition.handler_class)
        try:
            mtime_ns = definition.handler_path.stat().st_mtime_ns
        except FileNotFoundError:
            mtime_ns = -1

        # Return cached class if the file has not changed since last load.
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
            # Explicit class name: look it up directly and validate type.
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

        # Auto-discovery: module must expose exactly one BaseIntentHandler subclass.
        candidates = list(self._iter_handler_classes(module))
        if len(candidates) != 1:
            raise ValueError(
                f"Expected exactly one BaseIntentHandler subclass in {definition.handler_path}, found {len(candidates)}"
            )
        selected_class = candidates[0]
        self._handler_class_cache[cache_key] = (mtime_ns, selected_class)
        return selected_class

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _iter_handler_classes(module) -> Iterable[type[BaseIntentHandler]]:
        """Yield all concrete :class:`~base.BaseIntentHandler` subclasses in ``module``."""
        for _, member in inspect.getmembers(module, inspect.isclass):
            if member is BaseIntentHandler:
                continue
            if issubclass(member, BaseIntentHandler):
                yield member

    @staticmethod
    def _iter_stream_handler_classes(module) -> Iterable[type[StreamHandler]]:
        """Yield all concrete :class:`~stream_handler.StreamHandler` subclasses in ``module``."""
        for _, member in inspect.getmembers(module, inspect.isclass):
            if member is StreamHandler:
                continue
            if issubclass(member, StreamHandler):
                yield member