
import json
from pathlib import Path
from abc import ABC, abstractmethod
from typing import Any
from dataclasses import dataclass


def stringify_tool_result(result: Any) -> str:
    """Render an arbitrary tool-execution result as a string for a
    ``tool_result``/``tool`` round-trip message.

    Shared by every adapter's ``build_tool_round_trip_messages`` so results
    are stringified identically regardless of provider.
    """
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception:
        return str(result)


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

    def build_tool_round_trip_messages(
        self,
        *,
        raw_response: Any,
        tool_calls: list[ToolCall],
        tool_results: list[Any],
        config: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Return the provider-native message(s) to append after executing
        *tool_calls*, so the *next* :meth:`generate` call sees a real
        assistant-tool-call turn plus a real tool-result turn -- enabling a
        genuine multi-turn agentic tool-calling loop -- instead of a
        synthesized text summary with tools disabled.

        A concrete (not abstract) method with a ``NotImplementedError``
        default: most adapters don't need to support this yet, and making it
        abstract would break every existing adapter that doesn't implement
        it. Only adapters actually used by :class:`unified.loop.AgenticLoop`
        need to override this.

        Args:
            raw_response: The dict :meth:`generate` returned for the turn
                that produced *tool_calls*.
            tool_calls:   The canonical :class:`ToolCall` list extracted from
                *raw_response* (via :meth:`parse_tool_calls`).
            tool_results: The result of executing each tool call, in the same
                order as *tool_calls*.
            config:       The same per-call config dict passed to the
                :meth:`generate` call that produced *raw_response* -- needed
                by :class:`~provider_dispatch_adapter.ProviderDispatchLLMAdapter`
                to resolve the *same* concrete provider that actually
                produced *raw_response* (its shape is provider-specific and
                otherwise carries no "which provider made this" tag of its
                own), so this must be threaded through unchanged rather than
                re-resolved from a default.

        Returns:
            A list of message dicts to append to the conversation before the
            next :meth:`generate` call.
        """
        raise NotImplementedError(
            f"{self.provider_name} adapter does not support the agentic tool round trip yet"
        )

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
            data:   Dict conforming to the Claude tool authoring format. An
                    optional top-level ``"strict": false`` overrides the
                    ``strict`` argument for this one tool -- needed for a
                    tool whose schema has a genuinely free-form object
                    parameter (``"additionalProperties": true`` by design,
                    dynamic keys the caller can't enumerate in advance),
                    which OpenAI's strict mode cannot represent at all
                    (strict requires every property enumerated with
                    ``additionalProperties: false``).
            strict: Passed through to the resulting :class:`ToolSpec` unless
                    overridden by the tool file's own ``"strict"`` key.

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
            strict=bool(data.get("strict", strict)),
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