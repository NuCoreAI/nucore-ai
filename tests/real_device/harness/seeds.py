from __future__ import annotations

from pathlib import Path

import yaml

from .query_gen import GeneratedCase

SEEDS_FILE = Path(__file__).resolve().parents[1] / "seeds" / "regression.yaml"


def load_seeds(path: Path | None = None) -> list[GeneratedCase]:
    """Load previously-flagged fuzzy queries so they're replayed on every run.

    This is the regression net for the fuzzing harness: once a corner case is
    found once, it stays in the suite (and gets re-verified) rather than only
    ever being discovered by chance again.
    """
    resolved = Path(path) if path else SEEDS_FILE
    if not resolved.exists():
        return []

    with resolved.open("r", encoding="utf-8") as handle:
        raw_items = yaml.safe_load(handle) or []

    return [
        GeneratedCase(
            id=str(item["id"]),
            query=str(item["query"]),
            intent_family=str(item["intent_family"]),
            corner_case_type=str(item.get("corner_case_type", "")),
            rationale=str(item.get("rationale", "")),
            target_device_ids=list(item.get("target_device_ids") or []),
            target_routine_id=item.get("target_routine_id"),
            source="seed",
        )
        for item in raw_items
    ]


def add_seeds(new_cases: list[GeneratedCase], path: Path | None = None) -> None:
    """Append newly-flagged cases to the seed file, skipping ones already present (by query text)."""
    resolved = Path(path) if path else SEEDS_FILE
    resolved.parent.mkdir(parents=True, exist_ok=True)
    existing = load_seeds(resolved)
    existing_queries = {c.query for c in existing}

    to_add = [c for c in new_cases if c.query not in existing_queries]
    if not to_add:
        return

    combined = existing + to_add
    payload = [
        {
            "id": c.id,
            "query": c.query,
            "intent_family": c.intent_family,
            "corner_case_type": c.corner_case_type,
            "rationale": c.rationale,
            "target_device_ids": c.target_device_ids,
            "target_routine_id": c.target_routine_id,
        }
        for c in combined
    ]
    with resolved.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False, allow_unicode=True)
