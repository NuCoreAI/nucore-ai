from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"
ARCHIVE_DIR = RESULTS_DIR / "archive"

_INTENT_HANDLER_DIR = "src/intent_handler_directory"


@dataclass
class CaseResult:
    case_id: str
    query: str
    intent_family: str
    corner_case_type: str = ""
    rationale: str = ""
    source: str = "generated"  # "generated" | "seed"
    routed_intent: str | None = None
    flagged: bool = False
    severity: str = "ok"  # "ok" | "review" | "error"
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    before_state: dict[str, Any] = field(default_factory=dict)
    after_state: dict[str, Any] = field(default_factory=dict)
    restore_notes: dict[str, Any] = field(default_factory=dict)
    restore_error: str | None = None
    duration_s: float = 0.0


def write_run(results: list[CaseResult]) -> tuple[Path, Path | None]:
    """Write latest-run.json (always) and latest-failures.md (only if anything is flagged).

    Also archives a timestamped copy of both under results/archive/ for history.
    Returns (run_json_path, failures_md_path_or_None).
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    run_payload = {
        "timestamp": timestamp,
        "total": len(results),
        "ok": sum(1 for r in results if r.severity == "ok"),
        "review": sum(1 for r in results if r.severity == "review"),
        "error": sum(1 for r in results if r.severity == "error"),
        "cases": [_case_to_dict(r) for r in results],
    }

    run_json_path = RESULTS_DIR / "latest-run.json"
    run_json_path.write_text(json.dumps(run_payload, indent=2, default=str), encoding="utf-8")
    (ARCHIVE_DIR / f"{timestamp}-run.json").write_text(json.dumps(run_payload, indent=2, default=str), encoding="utf-8")

    flagged = [r for r in results if r.flagged]
    if not flagged:
        stale_failures = RESULTS_DIR / "latest-failures.md"
        if stale_failures.exists():
            stale_failures.unlink()
        return run_json_path, None

    failures_md_path = RESULTS_DIR / "latest-failures.md"
    markdown = _render_failures_markdown(flagged, timestamp=timestamp, total=len(results))
    failures_md_path.write_text(markdown, encoding="utf-8")
    (ARCHIVE_DIR / f"{timestamp}-failures.md").write_text(markdown, encoding="utf-8")

    return run_json_path, failures_md_path


def _case_to_dict(result: CaseResult) -> dict[str, Any]:
    return {
        "id": result.case_id,
        "query": result.query,
        "intent_family": result.intent_family,
        "corner_case_type": result.corner_case_type,
        "rationale": result.rationale,
        "source": result.source,
        "routed_intent": result.routed_intent,
        "flagged": result.flagged,
        "severity": result.severity,
        "reason": result.reason,
        "details": result.details,
        "before_state": result.before_state,
        "after_state": result.after_state,
        "restore_notes": result.restore_notes,
        "restore_error": result.restore_error,
        "duration_s": result.duration_s,
    }


def _render_failures_markdown(flagged: list[CaseResult], *, timestamp: str, total: int) -> str:
    errors = [r for r in flagged if r.severity == "error"]
    reviews = [r for r in flagged if r.severity == "review"]

    lines: list[str] = []
    lines.append(f"# Real-device fuzz run: {len(flagged)} of {total} case(s) flagged ({timestamp})")
    lines.append("")
    lines.append(
        f"{len(errors)} clear error(s) (exception / tool failure / silent no-op) and {len(reviews)} "
        "case(s) worth a second look (no tool call, response doesn't read as a clarifying question). "
        "Full run data: `results/latest-run.json`. Flagged queries have been appended to "
        "`seeds/regression.yaml` for replay on future runs."
    )
    lines.append("")

    for result in errors + reviews:
        lines.append(f"## [{result.severity.upper()}] `{result.case_id}` ({result.corner_case_type or 'uncategorized'})")
        lines.append("")
        lines.append(f"- **Query:** {result.query}")
        lines.append(f"- **Intent family:** `{result.intent_family}`  **Routed intent:** `{result.routed_intent or 'n/a'}`")
        lines.append(f"- **Source:** {result.source}")
        if result.rationale:
            lines.append(f"- **Why this query was chosen:** {result.rationale}")
        lines.append(f"- **Handler to inspect:** `{_INTENT_HANDLER_DIR}/{result.intent_family}/handler.py` (and its `prompt.md`)")
        lines.append(f"- **Reason flagged:** {result.reason}")
        if result.restore_error:
            lines.append(
                f"- **⚠ Restore failed:** {result.restore_error} — the real device/routine may be left in a "
                "modified state, check manually."
            )
        if result.restore_notes:
            uncertain = {k: v for k, v in result.restore_notes.items() if isinstance(v, dict) and v.get("restore_uncertain")}
            if uncertain:
                lines.append(f"- **⚠ Restore uncertain for:** {', '.join(uncertain.keys())} — check these devices manually.")
        if result.before_state:
            lines.append("")
            lines.append("**Device/routine state before:**")
            lines.append("```json")
            lines.append(json.dumps(result.before_state, indent=2, default=str))
            lines.append("```")
        if result.after_state:
            lines.append("")
            lines.append("**Device/routine state after:**")
            lines.append("```json")
            lines.append(json.dumps(result.after_state, indent=2, default=str))
            lines.append("```")
        if result.details:
            lines.append("")
            lines.append("**Details (raw tool call/result, response text):**")
            lines.append("```json")
            lines.append(json.dumps(result.details, indent=2, default=str))
            lines.append("```")
        lines.append("")

    return "\n".join(lines)
