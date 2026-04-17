from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dataclasses import dataclass, field
from typing import Any

@dataclass(frozen=True)
class IntentDefinition:
    name: str
    directory: Path
    config_path: Path
    prompt_content: str
    handler_path: Path
    description: str
    handler_class: str | None = None
    previous_dependencies: list[str] = field(default_factory=list)
    routing_examples: list[str] = field(default_factory=list)
    router_hints: list[str] = field(default_factory=list)
    llm_config: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RouteResult:
    intent: str
    confidence: float | None = None
    notes: str | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IntentHandlerResult:
    intent: str
    output: Any
    route_result: RouteResult | None = None
    metadata: dict[str, Any] = field(default_factory=dict)