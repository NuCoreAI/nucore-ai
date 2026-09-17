"""Detects a reply that claims a tool-mediated action happened when no tool
was actually called this turn -- the code-level backstop for the CRITICAL
RULE in system_prompt.md ("never claim an action happened unless you did it
this turn").

Deliberately tool-agnostic: it never looks at *which* tool should have been
called, only whether *any* tool was called at all this turn, paired with a
generic phrase heuristic for "this reads like a completion claim." That's
what lets it cover every tool (current and future) with zero per-tool
maintenance -- the same reason the prompt-side CRITICAL RULE was collapsed
from five topic-specific restatements into one general one (see
system_prompt.md's own history).

Known, accepted scope limits (see AgenticLoop.run for how these get used):
- Catches "fabricated from nothing" (no tool call at all). Does NOT catch a
  *wrong* tool being called (e.g. a status re-check standing in for a repeat
  action request) while still claiming the repeat was satisfied -- that needs
  matching claim-type to expected-tool, which reintroduces the per-tool
  coupling this module exists to avoid. A known v1 gap, not silently ignored.
- Regex heuristics over a semantic classifier: cheap, synchronous, adds no
  latency/cost, and can't itself hallucinate a false judgment. Trades some
  precision/recall for that. Since detection only gates logging by default
  (see the caller's ``fabrication_guard_mode``), a false positive here costs
  nothing until "block" mode is actually enabled.
"""

from __future__ import annotations

import re

# Each pattern name should read naturally in a log line ("fabrication_flag"
# detail's "pattern" field) -- keep them short and self-explanatory.
_COMPLETION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # "Done!"/"Done." only -- a bare "done" mid-sentence ("once that's done,
    # I'll...") is not a completion claim about *this* reply.
    ("done", re.compile(r"\bDone[!.]")),
    # Curated but tool-agnostic verb list: ordinary English past-tense verbs
    # for "did a thing", not NuCore-specific tool names -- covers regular
    # ("updated", "created") and the common irregular forms this system's
    # own replies actually use ("run", "set", "sent").
    (
        "ive_verbed",
        re.compile(
            r"\bI'?ve\s+(run|set|sent|turned|created|updated|removed|added|changed|fixed|"
            r"deleted|renamed|enabled|disabled|paired|excluded|included|installed|configured|"
            r"saved|started|stopped|restarted|moved|purchased|bought)\b",
            re.IGNORECASE,
        ),
    ),
    ("is_now", re.compile(r"\bis now\b", re.IGNORECASE)),
    ("has_been", re.compile(r"\bhas been\b", re.IGNORECASE)),
    ("successfully", re.compile(r"\bsuccessfully\b", re.IGNORECASE)),
    ("all_set", re.compile(r"\ball set\b", re.IGNORECASE)),
    (
        "is_being_verbed",
        re.compile(
            r"\bis being (run|sent|turned|updated|removed|added|changed|installed|configured|"
            r"saved|deleted|paired|excluded)\b",
            re.IGNORECASE,
        ),
    ),
    ("checkmark", re.compile(r"✅")),  # "✅"
    ("everything_is", re.compile(r"\beverything is (configured|set|done)\b", re.IGNORECASE)),
)


def detect_completion_claim(text: str) -> str | None:
    """Returns the name of the first matched completion-claim pattern, or
    ``None`` if *text* doesn't read like one. Callers are expected to only
    invoke this when no tool was actually called this turn -- see this
    module's docstring for why that precondition (not the pattern list
    alone) is what keeps this tool-agnostic."""
    if not text:
        return None
    for name, pattern in _COMPLETION_PATTERNS:
        if pattern.search(text):
            return name
    return None
