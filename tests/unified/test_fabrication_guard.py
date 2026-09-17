"""``detect_completion_claim`` -- pure-function unit tests, no I/O.

Matches are paraphrases of the completion claims actually observed live
(see AgenticLoop's fabrication-guard wiring for the full context); non-matches
cover the shapes that must stay clean -- clarifying questions, plain
informational/status replies, "I don't know" replies, and forward-looking
descriptions of what a routine's own logic will do (not a claim that this
reply just did anything).
"""

from __future__ import annotations

from unified.fabrication_guard import detect_completion_claim

MATCHING_CLAIMS = [
    "Done! I've run the 'else' branch of the routine. This will turn off the light.",
    "I've run the 'then' branch of the routine. This will: 1. Turn on the light",
    "Great! The routine is all set with the Dog Bark sound. Everything is configured as you requested!",
    "The push notification is being sent through the UD Mobile node.",
    "Perfect! I've updated the routine to use the correct sound.",
    "The GuestBathroom is now off.",
    "Your request has been completed successfully.",
    "✅ Turns on the GuestBathroom light at sundown",
]

NON_MATCHING_REPLIES = [
    # Clarifying question -- no claim of anything having happened.
    "I need you to specify which notification you'd like to send. Please choose:",
    # Plain read-only status/informational reply.
    "The GuestBathroom is currently on at 100% brightness.",
    # "I don't have that" reply.
    "I don't have the exact record of that from earlier, let me check again now.",
    # Forward-looking description of a routine's own logic, not a claim
    # that this reply performed an action (the known "wrong tool called"
    # gap is a separate, explicitly out-of-scope case -- see loop.py).
    "This is from when we just ran the 'then' branch of the routine.",
    "The 'then' branch will turn off the light six hours after it turns on.",
    "Here are the Yom Kippur dates for the next 10 years:",
    "Would you like me to send a specific notification now?",
    # Bare, unpunctuated "done" mid-sentence -- not a completion claim
    # about *this* reply (also matches the existing test_loop.py fixture
    # text, which must stay unaffected by this guard).
    "once that's done, I'll let you know",
    "",
]


def test_detects_evidenced_completion_claims():
    for text in MATCHING_CLAIMS:
        assert detect_completion_claim(text) is not None, f"expected a match for: {text!r}"


def test_does_not_flag_legitimate_replies():
    for text in NON_MATCHING_REPLIES:
        assert detect_completion_claim(text) is None, f"unexpected match for: {text!r}"


def test_returns_the_matched_pattern_name():
    assert detect_completion_claim("Done! All set.") in {"done", "all_set"}
