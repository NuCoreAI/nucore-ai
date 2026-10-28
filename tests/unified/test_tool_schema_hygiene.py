"""Tool-schema authoring hygiene checks that apply regardless of which LLM
adapter ends up reading a tool's JSON Schema -- unlike the provider-specific
normalization tests in tests/unified/adapters/, these catch mistakes in the
hand-authored schema itself.

Regression coverage for a live bug (commit 2edffc2, "Fix generate_plugin_
scaffold's aiTools schema missing properties"): a `required` list naming a
field with no matching `properties` entry at all. OpenAI and Claude silently
tolerate this (neither validates that required names are actually declared),
but Gemini's schema validator rejects the whole request over it
("required[0]: property is not defined"). Nothing in any adapter's own code
guards against reintroducing this -- it's a plain authoring-correctness
property of the schema, checked here once, for every tool file, rather than
relying on it being caught by whichever provider happens to be strict about
it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_TOOLS_DIR = Path(__file__).parents[2] / "src" / "unified" / "tools"
_PLUGIN_AUTHORING_TOOLS_DIR = Path(__file__).parents[2] / "src" / "unified" / "plugin_authoring" / "tools"


def _find_required_without_matching_property(node: Any, path: list[str]) -> list[tuple[list[str], str]]:
    """Walk a schema node recursively (into `properties` values, `items`
    -- dict or list -- and the three combiner keywords), collecting every
    `required` entry that has no matching key in that same node's
    `properties`."""
    if not isinstance(node, dict):
        return []
    findings: list[tuple[list[str], str]] = []
    properties = node.get("properties")
    required = node.get("required")
    if isinstance(required, list) and isinstance(properties, dict):
        for name in required:
            if name not in properties:
                findings.append((path, name))
    if isinstance(properties, dict):
        for key, value in properties.items():
            findings.extend(_find_required_without_matching_property(value, path + [key]))
    items = node.get("items")
    if isinstance(items, dict):
        findings.extend(_find_required_without_matching_property(items, path + ["items"]))
    elif isinstance(items, list):
        for index, item in enumerate(items):
            findings.extend(_find_required_without_matching_property(item, path + [f"items[{index}]"]))
    for keyword in ("anyOf", "oneOf", "allOf"):
        variants = node.get(keyword)
        if isinstance(variants, list):
            for index, variant in enumerate(variants):
                findings.extend(_find_required_without_matching_property(variant, path + [f"{keyword}[{index}]"]))
    return findings


def test_no_tool_file_requires_a_property_it_never_declares():
    tool_files = sorted(_TOOLS_DIR.glob("tool_*.json")) + sorted(_PLUGIN_AUTHORING_TOOLS_DIR.glob("tool_*.json"))
    for tool_file in tool_files:
        data = json.loads(tool_file.read_text())
        findings = _find_required_without_matching_property(data.get("input_schema", {}), [])
        assert findings == [], f"{tool_file.name}: {findings}"


def test_the_walker_actually_catches_the_defect_it_guards_against():
    # Proves the walker isn't vacuously passing -- a required name with no
    # matching property, at a nested path, is actually detected.
    schema = {
        "type": "object",
        "properties": {
            "items_list": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name", "ghost"],
                    "properties": {"name": {"type": "string"}},
                },
            }
        },
    }

    findings = _find_required_without_matching_property(schema, [])

    assert findings == [(["items_list", "items"], "ghost")]
