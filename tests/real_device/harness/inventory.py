from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .snapshot import _force_refresh_nodes, _force_refresh_routines


@dataclass
class LiveInventory:
    """Live device/routine inventory pulled straight from the connected hub.

    Feeds the fuzzy query generator (harness/query_gen.py) real device and
    routine names/ids so generated queries reference things that actually
    exist -- and lets the harness validate that a generator-chosen
    target_device_id is real before trying to snapshot it.
    """

    device_summary_text: str  # the same compact device/group/folder JSON the LLM sees via summary_rags
    routines_summary_text: str  # condensed_routines as JSON
    known_device_ids: set[str]
    known_routine_ids: set[str]


async def get_live_inventory(nucore_interface: Any) -> LiveInventory:
    """Refresh and pull the hub's current device/routine inventory.

    Uses the same in-memory summaries the runtime itself builds for LLM
    prompts (``summary_rags``, ``condensed_routines``) rather than
    re-querying/re-formatting the device structure -- so what the fuzzy
    query generator sees matches what the intent handlers themselves see.
    """
    await _force_refresh_nodes(nucore_interface)
    await _force_refresh_routines(nucore_interface)

    device_summary_text = nucore_interface.summary_rags.docs_to_string() if nucore_interface.summary_rags else ""
    routines_summary_text = json.dumps(nucore_interface.condensed_routines or [], indent=2)

    known_device_ids = set(nucore_interface.nodes) | set(nucore_interface.groups) | set(nucore_interface.folders)
    known_routine_ids = {str(r["id"]) for r in (nucore_interface.condensed_routines or []) if r.get("id")}

    return LiveInventory(
        device_summary_text=device_summary_text,
        routines_summary_text=routines_summary_text,
        known_device_ids=known_device_ids,
        known_routine_ids=known_routine_ids,
    )


def extract_ids_from_summary(device_summary_text: str) -> set[str]:
    """Best-effort extraction of every ``"id": "..."`` value out of the summary JSON text.

    Used as a fallback when the generator prompt's device_summary_text isn't
    valid standalone JSON (it may be wrapped in a fenced code block or
    concatenated with other RAG documents) -- a regex scan is more robust
    than requiring a clean ``json.loads`` round-trip here.
    """
    return set(re.findall(r'"id"\s*:\s*"([^"]+)"', device_summary_text or ""))
