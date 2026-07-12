from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from intent_handler import IntentHandlerResult

# Loose signal that a no-tool-call text response is a clarifying question
# rather than a (possibly wrong) direct answer or a silent failure. This is
# intentionally coarse -- heuristic-only verification means borderline cases
# get flagged with severity="review" rather than confidently judged either way.
_CLARIFYING_HINTS = ("?", "which ", "did you mean", "could you clarify", "can you clarify", "do you mean", "multiple")


@dataclass
class VerifyOutcome:
    """Result of heuristic verification: is this case worth a human/Claude looking at?

    There is no hand-written expected outcome for a fuzzily-generated query,
    so this never asserts "correct" -- only "nothing suspicious" (severity
    "ok"), "worth a second look" ("review"), or "clearly broken" ("error").
    """

    flagged: bool
    severity: str  # "ok" | "review" | "error"
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)


def _looks_like_clarifying_question(text: str) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in _CLARIFYING_HINTS)


def verify_heuristic(result: IntentHandlerResult | None, *, exception: Exception | None = None) -> VerifyOutcome:
    """Flag a fuzzy-query result for review using only generic, expectation-free signals.

    Flags (severity="error"): an exception was raised, no result came back,
    a tool call executed but its result looks like a failure (reusing the
    runtime's own ``execution_metadata["tool_calls_failed"]``), or no tool
    call ran and there was no text response at all (silent no-op).

    Flags (severity="review", non-authoritative): no tool call ran and the
    text response doesn't read as a clarifying question -- could be a
    legitimate direct answer, or could be a silent wrong-guess; a human or
    Claude needs to look at the actual text to tell which.

    Not flagged: a tool call executed with no failure marker, or the system
    asked a clarifying question -- both are normal, healthy outcomes for a
    corner-case query.
    """
    if exception is not None:
        return VerifyOutcome(
            flagged=True,
            severity="error",
            reason=f"Exception raised while running the query: {type(exception).__name__}: {exception}",
        )

    if result is None:
        return VerifyOutcome(flagged=True, severity="error", reason="Runtime returned no result for this query.")

    metadata = result.execution_metadata or {}
    text_output = result.get_text_output()

    if metadata.get("had_tool_calls"):
        if metadata.get("tool_calls_failed"):
            return VerifyOutcome(
                flagged=True,
                severity="error",
                reason="Tool call executed but its result indicates failure.",
                details={"tool_results": result.get_tool_results()},
            )
        return VerifyOutcome(
            flagged=False,
            severity="ok",
            reason="Tool call executed with no failure marker.",
            details={"tool_results": result.get_tool_results(), "text_output": text_output},
        )

    if not text_output:
        return VerifyOutcome(
            flagged=True,
            severity="error",
            reason="Silent no-op: no tool call executed and no text response.",
        )

    if _looks_like_clarifying_question(text_output):
        return VerifyOutcome(
            flagged=False,
            severity="ok",
            reason="Asked a clarifying question instead of guessing.",
            details={"text_output": text_output},
        )

    return VerifyOutcome(
        flagged=True,
        severity="review",
        reason="No tool call executed and the response doesn't read as a clarifying question -- "
        "could be a legitimate direct answer or a silent wrong guess; needs a human/Claude read.",
        details={"text_output": text_output},
    )
