from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from .inventory import LiveInventory

# Corner-case taxonomy per intent family, given to the generator LLM as
# guidance so it produces genuinely adversarial phrasings grounded in the
# live inventory rather than generic/easy queries.
CORNER_CASE_GUIDANCE: dict[str, list[str]] = {
    "command_control_status": [
        "ambiguous_device_reference -- a name/phrase that matches multiple real devices with no qualifier",
        "vague_quantity -- 'a little', 'a lot', 'way up/down' instead of a number",
        "pronoun_no_antecedent -- 'turn it off' with no prior context establishing what 'it' is",
        "nonexistent_device -- refers to a plausible-sounding device that is NOT in the inventory",
        "room_level_multiple_matches -- a room/area command matching several real devices at once",
        "negation_or_compound -- 'don't turn off X, turn down Y instead' or 'turn off the lights and lock the door'",
        "colloquial_property_query -- 'how hot is it in the bedroom' instead of naming the exact property",
    ],
    "group_scene_ops": [
        "ambiguous_scene_reference -- a scene/group name that matches multiple real scenes or is easily confused with a device name",
        "nonexistent_scene -- refers to a plausible scene that is NOT in the inventory",
        "membership_edge_case -- add a device to a scene/group it's already a member of, or remove one that isn't a member",
        "scene_vs_group_confusion -- phrasing that could mean either a device group or a lighting scene",
    ],
    "node_ops": [
        "rename_conflict -- rename a device to a name that's already used by another real device",
        "invalid_characters_in_name -- a new name containing characters the backend rejects",
        "move_to_nonexistent_parent -- move a device into a folder/group that doesn't exist",
        "redundant_operation -- disable an already-disabled device, or enable an already-enabled one",
        "fuzzy_node_reference -- a partial or misspelled version of a real device/folder name",
    ],
    "routine_automation": [
        "vague_time_reference -- 'later tonight', 'in a bit', 'when I get home' with no explicit trigger defined elsewhere",
        "conflicting_conditions -- an if/then that contradicts itself (e.g. turn something on AND off under the same condition)",
        "multi_action_single_query -- several distinct actions bundled into one routine request",
        "fuzzy_reference_to_existing_routine -- referring to a real existing routine by a partial/approximate name to edit it",
        "missing_required_detail -- asks for automation without specifying a concrete trigger or action target",
    ],
    "routine_status_ops": [
        "fuzzy_routine_reference -- a partial or approximate name of a real existing routine",
        "operate_on_nonexistent_routine -- refers to a plausible routine name that does NOT exist",
        "redundant_operation -- enable an already-enabled routine, or disable an already-disabled one",
    ],
}

_SYSTEM_PROMPT = """You are a QA fuzzing assistant for a natural-language smart-home control system. \
Given a live device/routine inventory and a corner-case taxonomy, generate adversarial test queries a real \
user might plausibly type, engineered to expose bugs: wrong device selected, silent no-ops, missing \
clarification, malformed routines, crashes. Ground every query in REAL entities from the inventory provided \
-- use their exact names, and their exact "id" field when you can attribute the query to specific target(s). \
Respond with ONLY a JSON array, no prose, no markdown fences."""


@dataclass
class GeneratedCase:
    id: str
    query: str
    intent_family: str
    corner_case_type: str = ""
    rationale: str = ""
    target_device_ids: list[str] = field(default_factory=list)
    target_routine_id: str | None = None
    source: str = "generated"  # "generated" | "seed"


def _build_user_prompt(inventory: LiveInventory, intents: list[str], count_per_intent: int) -> str:
    sections = [
        "# LIVE DEVICE/GROUP/FOLDER INVENTORY",
        inventory.device_summary_text or "(no devices reported)",
        "",
        "# LIVE ROUTINES (condensed)",
        inventory.routines_summary_text or "[]",
        "",
        "# TASK",
        f"Generate exactly {count_per_intent} corner-case test queries for EACH of the following intent "
        f"families: {', '.join(intents)}.",
        "",
        "For each intent family, draw from (and vary across) this taxonomy of corner-case types -- use every "
        "type at least once if count_per_intent allows:",
    ]
    for intent in intents:
        sections.append(f"\n## {intent}")
        for line in CORNER_CASE_GUIDANCE.get(intent, []):
            sections.append(f"- {line}")

    sections.append(
        "\n# OUTPUT FORMAT\n"
        "A single JSON array. Each element:\n"
        "{\n"
        '  "query": "<the natural language query text>",\n'
        '  "intent_family": "<one of the requested intent families>",\n'
        '  "corner_case_type": "<short slug from the taxonomy above>",\n'
        '  "rationale": "<one sentence: why this might trip up the system>",\n'
        '  "target_device_ids": ["<exact id field(s) from the inventory this query targets, if any>"],\n'
        '  "target_routine_id": "<exact id field from the routines list, if this targets an existing routine, else null>"\n'
        "}\n"
        "Leave target_device_ids empty and target_routine_id null for queries that intentionally reference "
        "something NOT in the inventory (nonexistent_device / nonexistent_scene / operate_on_nonexistent_routine)."
    )
    return "\n".join(sections)


def _extract_json_array(text: str) -> list[dict[str, Any]]:
    """Parse a JSON array out of the model's raw text output, tolerating stray markdown fences."""
    cleaned = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\[.*\])\s*```", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1)
    else:
        start, end = cleaned.find("["), cleaned.rfind("]")
        if start != -1 and end != -1 and end > start:
            cleaned = cleaned[start : end + 1]
    parsed = json.loads(cleaned)
    if not isinstance(parsed, list):
        raise ValueError("Expected a JSON array of generated test cases")
    return parsed


async def generate_fuzzy_queries(
    llm_client: Any,
    llm_config: dict[str, Any],
    inventory: LiveInventory,
    intents: list[str],
    count_per_intent: int,
) -> list[GeneratedCase]:
    """Ask the LLM to generate adversarial test queries grounded in the live inventory."""
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_prompt(inventory, intents, count_per_intent)},
    ]
    response = await llm_client.generate(messages=messages, config=llm_config, tools=None, expect_json=True)
    text = (response or {}).get("text") if isinstance(response, dict) else None
    if not text:
        raise ValueError(f"Query generator returned no text output (raw response: {response!r})")

    raw_items = _extract_json_array(text)

    cases: list[GeneratedCase] = []
    for item in raw_items:
        if not isinstance(item, dict) or not item.get("query") or not item.get("intent_family"):
            continue
        target_device_ids = [
            str(d) for d in (item.get("target_device_ids") or []) if str(d) in inventory.known_device_ids
        ]
        target_routine_id = item.get("target_routine_id")
        if target_routine_id and str(target_routine_id) not in inventory.known_routine_ids:
            target_routine_id = None
        cases.append(
            GeneratedCase(
                id=f"fuzz-{uuid.uuid4().hex[:10]}",
                query=str(item["query"]),
                intent_family=str(item["intent_family"]),
                corner_case_type=str(item.get("corner_case_type", "")),
                rationale=str(item.get("rationale", "")),
                target_device_ids=target_device_ids,
                target_routine_id=str(target_routine_id) if target_routine_id else None,
            )
        )
    return cases
