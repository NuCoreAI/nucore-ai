
import json
from pathlib import Path
from abc import ABC, abstractmethod
from typing import Any
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Data Transfer Objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolSpec:
    """Canonical description of a tool that can be handed to any LLM provider.

    Attributes:
        name:        Unique tool name used as the function identifier by the LLM.
        description: Human-readable description shown to the model.
        json_schema: JSON Schema dict describing the tool's input parameters
                     (Claude ``input_schema`` format is the source of truth).
        strict:      When True, providers that support strict schema enforcement
                     (e.g. OpenAI function-calling) will apply it.
    """
    name: str
    description: str
    json_schema: dict[str, Any]
    strict: bool = True

@dataclass(frozen=True)
class ToolCall:
    """Represents a single tool invocation returned by the LLM.

    Attributes:
        call_id:  Provider-assigned identifier for this specific call instance.
                  Required by some providers (e.g. OpenAI) when submitting the
                  tool result back.
        name:     Name of the tool that was called, matching a ``ToolSpec.name``.
        args:     Parsed argument dictionary as returned by the model.
        provider: Name of the provider that generated this call (e.g. ``"claude"``).
        raw:      Unmodified provider response object, useful for debugging.
    """
    call_id: str
    name: str
    args: dict[str, Any]
    provider: str
    raw: Any = None

@dataclass(frozen=True)
class ProviderCapabilities:
    """Declares what optional features a specific LLM provider supports.

    These flags are used by the dispatch layer to adapt request construction
    and response parsing without hard-coding provider-specific branches.

    Attributes:
        supports_parallel_calls:        Provider may return multiple tool calls
                                        in a single response turn.
        supports_streaming_tool_args:   Provider can stream partial tool
                                        argument JSON before the call is complete.
        requires_tool_result_id:        Provider requires that tool results
                                        reference the original ``call_id``
                                        (e.g. OpenAI).
        supports_strict_json_schema:    Provider enforces the ``strict`` flag on
                                        tool input schemas.
        tool_choice_mode:               Default tool-choice strategy to send
                                        (``"auto"``, ``"any"``, ``"none"``).
        max_tools_per_request:          Maximum number of tool definitions that
                                        can be included in a single request.
    """
    supports_parallel_calls: bool = False
    supports_streaming_tool_args: bool = False
    requires_tool_result_id: bool = True
    supports_strict_json_schema: bool = True
    tool_choice_mode: str = "auto"
    max_tools_per_request: int = 128

    from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Abstract LLM Adapter
# ---------------------------------------------------------------------------

class LLMAdapter(ABC):
    """Abstract base class for all LLM provider adapters.

    Each concrete adapter (e.g. Anthropic, OpenAI, Gemini) implements this
    interface so that the intent runtime can call any provider uniformly.

    Class attribute:
        provider_name: Short identifier for the provider, set by each subclass.
    """

    provider_name: str = "unknown"

    @abstractmethod
    async def generate(
        self,
        *,
        messages: list[dict[str, str]],
        config: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        expect_json: bool = False,
    ) -> Any:
        """Send a chat request to the provider and return the raw response.

        Args:
            messages:    Conversation history in the standard ``{"role", "content"}``
                         format.
            config:      Optional provider-specific parameters (model, temperature,
                         max_tokens, stream handler, etc.).
            tools:       Pre-exported tool definitions in the provider's native
                         format, as returned by :meth:`export_tools`.
            expect_json: Hint to the provider that the assistant reply should be
                         valid JSON (enables JSON mode where supported).

        Returns:
            The raw provider response object; callers use :meth:`parse_tool_calls`
            and companion helpers to extract structured data from it.
        """
        raise NotImplementedError

    @abstractmethod
    def export_tools(self, specs: list[ToolSpec]) -> Any:
        """Convert a list of provider-neutral :class:`ToolSpec` objects into the
        native tool definition format expected by this provider's API.

        Args:
            specs: Provider-neutral tool specifications.

        Returns:
            A value suitable for the ``tools`` parameter of :meth:`generate`.
        """
        ...

    @abstractmethod
    def parse_tool_calls(self, response: Any) -> list[ToolCall]:
        """Extract any tool calls from a provider response.

        Args:
            response: Raw provider response as returned by :meth:`generate`.

        Returns:
            A (possibly empty) list of :class:`ToolCall` instances.
        """
        raise NotImplementedError

    @abstractmethod
    def to_canonical_tools(self, tool_calls: list[ToolCall]) -> list[dict[str, Any]]:
        """Serialize a list of :class:`ToolCall` objects into the provider's
        expected tool-result message format so they can be appended to the
        conversation history.

        Args:
            tool_calls: Tool calls previously returned by :meth:`parse_tool_calls`.

        Returns:
            A list of message dicts ready to be passed back to :meth:`generate`.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Tool spec helpers
    # ------------------------------------------------------------------

    @classmethod
    def tools_spec_from_file(cls, path: str | Path, *, strict: bool = True) -> ToolSpec:
        """Load a single :class:`ToolSpec` from a JSON file.

        The file must use the Claude tool authoring format with a top-level
        ``input_schema`` key.

        Args:
            path:   Path to the JSON tool definition file.
            strict: Passed through to the resulting :class:`ToolSpec`.

        Raises:
            FileNotFoundError: If ``path`` does not exist.
        """
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Tool spec file not found: {file_path}")
        with file_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return cls.tools_spec_from_dict(data, strict=strict)

    @classmethod
    def tools_spec_from_dict(cls, data: dict[str, Any], *, strict: bool = True) -> ToolSpec:
        """Build a :class:`ToolSpec` from an already-parsed dict.

        Args:
            data:   Dict conforming to the Claude tool authoring format.
            strict: Passed through to the resulting :class:`ToolSpec`.

        Raises:
            TypeError:  If ``data`` is not a dict.
            ValueError: If required fields (``name``, ``input_schema``) are absent.
        """
        if not isinstance(data, dict):
            raise TypeError(f"Expected a dict, got {type(data).__name__}")

        name = data.get("name", "")
        description = data.get("description", "")
        json_schema = data.get("input_schema")

        if not name:
            raise ValueError(f"Tool spec is missing a 'name' field: {data}")
        if not isinstance(json_schema, dict):
            raise ValueError(
                f"Tool spec '{name}' is missing an 'input_schema' object. "
                "All tool files must use the Claude authoring format."
            )

        return ToolSpec(
            name=name,
            description=description,
            json_schema=json_schema,
            strict=strict,
        )

    @classmethod
    def tools_spec_from_files(cls, paths: list[str | Path], *, strict: bool = True) -> list[ToolSpec]:
        """Convenience wrapper that loads multiple tool spec files at once.

        Args:
            paths:  List of paths to JSON tool definition files.
            strict: Passed through to each resulting :class:`ToolSpec`.

        Returns:
            Ordered list of :class:`ToolSpec` instances, one per file.
        """
        return [cls.tools_spec_from_file(p, strict=strict) for p in paths]