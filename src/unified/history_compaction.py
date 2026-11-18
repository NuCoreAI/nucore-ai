"""Token-based compaction for :class:`~models.ConversationHistory`.

This is the *only* limit on how much history is replayed -- there is no
turn-count cap. Dropping the oldest turn on every append would shift the
provider's cached prompt prefix every turn; compaction instead runs only
when the estimated token size exceeds the budget, and then compacts down to
about half the budget (hysteresis), so the prefix stays stable until the
history has regrown by about half the budget.

``maybe_compact_history`` keeps the most recent turns that fit in that
half-budget (always at least the latest turn) and collapses everything older
into one summary pseudo-turn via a single LLM call, rather than silently
dropping it.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from .adapters import LLMAdapter
from .models import ConversationHistory, ConversationTurn
from utils import get_logger

logger = get_logger(__name__)

_DEFAULT_TOKEN_BUDGET = 20000
_COMPACTED_QUERY_LABEL = "[earlier conversation, summarized]"

# Lowest token-budget decile (5 == 50%) that ever triggers a `notify` call --
# see maybe_compact_history's own docstring.
_MIN_BUDGET_NOTICE_DECILE = 5

_COMPACTED_NOTICE = (
    "*Session history was over budget, so I compacted the older turns. You can also do "
    "this any time with /compact-session.*"
)


def _budget_notice(percent: int) -> str:
    return f"*Session history is at {percent}% of its budget -- you can compact it any time with /compact-session.*"

_SUMMARY_PROMPT_HEADER = (
    "Summarize the conversation turns below concisely, in plain prose (no headers/bullets "
    "needed unless they help). Preserve concrete facts a later turn might still depend on -- "
    "device/routine names and ids, decisions made, values reported by tools -- and omit "
    "meta-commentary about the summarization itself. Do not fabricate anything not present "
    "below. Keep the summary under {max_words} words.\n\n"
)


def estimate_tokens(text: str) -> int:
    """Rough, conservative token-count estimate (~3 chars/token; real
    JSON/code-heavy transcripts measure closer to 2-3, prose closer to 4) --
    no tokenizer dependency in this codebase to call instead."""
    return len(text or "") // 3


def _turn_tokens(turn: ConversationTurn) -> int:
    """Estimate one turn's token footprint for the compaction budget check.

    A turn with a non-empty rich ``.messages`` transcript (every tool_use/
    tool_result round trip this turn produced -- see models.py's
    ConversationTurn.messages) is sized from that transcript's full
    JSON-serialized bytes, not just ``.query``/``.response`` -- those two
    short text fields would badly undercount a turn that made several tool
    calls (e.g. a multi-KB file read echoed back verbatim in a tool_result
    block), letting compaction fire far later than the actual prompt size
    sent to the model on every subsequent turn would justify.

    Falls back to ``.query``/``.response`` alone -- today's exact estimate
    -- for a turn with no rich transcript: pre-feature turns, the
    ``_COMPACTED_QUERY_LABEL`` summary pseudo-turn, and every turn whose
    messages a caller never captured. Never raises -- a serialization
    failure degrades to the text-only estimate rather than propagating.
    """
    if turn.messages:
        try:
            return estimate_tokens(json.dumps(turn.messages, ensure_ascii=False, default=str))
        except Exception:
            pass
    return estimate_tokens(turn.query) + estimate_tokens(turn.response)


def _history_tokens(turns: list[ConversationTurn]) -> int:
    return sum(_turn_tokens(t) for t in turns)


def _render_turns_for_summary(turns: list[ConversationTurn]) -> str:
    parts = []
    for turn in turns:
        parts.append(f"User: {turn.query}\nAssistant: {turn.response}")
    return "\n\n".join(parts)


def _is_summary(turn: ConversationTurn) -> bool:
    return turn.query == _COMPACTED_QUERY_LABEL


def _summary_max_tokens(token_budget: int, configured: Any) -> int:
    """Cap on the summary call's own output so a summary can never eat the
    budget it exists to protect -- a profile's configured ``max_tokens``
    (e.g. 64000 for plugin_authoring) is sized for generation, not this."""
    cap = max(512, token_budget // 4)
    return min(int(configured), cap) if configured else cap


def _split_for_compaction(turns: list[ConversationTurn], keep_budget: int) -> int:
    """Index such that ``turns[split:]`` is the longest recent suffix whose
    estimated size fits ``keep_budget`` -- always at least the latest turn,
    however large. ``turns[:split]`` is what gets summarized."""
    kept = 0
    split = len(turns)
    for idx in range(len(turns) - 1, -1, -1):
        size = _turn_tokens(turns[idx])
        if split < len(turns) and kept + size > keep_budget:
            break
        kept += size
        split = idx
    return split


async def _summarize_turns(
    turns: list[ConversationTurn], *, llm_client: LLMAdapter, llm_config: dict[str, Any], max_tokens: int
) -> str:
    header = _SUMMARY_PROMPT_HEADER.format(max_words=max(100, int(max_tokens * 0.6)))
    prompt = header + _render_turns_for_summary(turns)
    # Never stream this internal call into whatever live handler the caller's
    # config carries -- that handler is wired to the user-facing reply, not
    # this behind-the-scenes summarization call.
    summary_config = dict(llm_config)
    summary_config.pop("stream_handler", None)
    summary_config["stream"] = False
    summary_config["max_tokens"] = max_tokens
    response = await llm_client.generate(messages=[{"role": "user", "content": prompt}], config=summary_config, tools=None)
    response = response or {}
    return (response.get("text") or response.get("content") or "").strip()


async def maybe_compact_history(
    history: ConversationHistory,
    *,
    llm_client: LLMAdapter,
    llm_config: dict[str, Any],
    token_budget: int = _DEFAULT_TOKEN_BUDGET,
    notify: Callable[[str], Awaitable[None]] | None = None,
    force: bool = False,
) -> bool:
    """Collapse the older turns of ``history.turns`` into one summary
    pseudo-turn when the history's estimated token size exceeds
    ``token_budget``. Mutates ``history.turns`` in place.

    The most recent turns that fit in half the budget (always at least the
    latest turn) are kept verbatim; everything older is summarized. Targeting
    half the budget is deliberate hysteresis: the history must regrow by
    about half the budget before the next compaction, so the provider's
    cached prompt prefix stays stable in between instead of being rewritten
    (plus an extra summarization call) every turn.

    No-ops when there is nothing older than the kept suffix to summarize --
    including when the latest turn alone exceeds the budget (a warning is
    logged; nothing short of dropping the live turn could help) -- or, without
    *force*, when the history is already under budget. A lone summary
    pseudo-turn is never re-summarized. Never raises -- a failed
    summarization call falls back to plain hard truncation of the same older
    turns rather than blocking the user's actual turn.

    *notify*, when given, is awaited with one italic Markdown status line
    whenever estimated usage crosses a new 10%-of-budget decile at or above
    50% (tracked on ``history.last_budget_notice_decile`` so the same
    decile isn't repeated every turn), or once compaction actually runs
    (superseding the generic decile wording with "compaction just
    happened" instead, for the same turn that crosses 100%). Reset to 0
    after compaction, so a later climb re-notifies from 50% again.
    Best-effort: an exception here is logged and swallowed, same contract
    as every other hook in this codebase (see AgenticLoop's
    on_raw_response).

    *force*, when True, skips the token-budget gate -- the manual
    ``/compact-session`` command's path (see
    ``UnifiedRuntime.compact_session``), letting a customer compact early
    even while under budget (keeping about the newest half of the history
    by size). *notify* isn't meaningful there -- that caller composes its
    own reply -- and should be left ``None``.

    Returns:
        True iff compaction (summarized or fallback-truncated) happened.
    """
    turns = history.turns
    if not turns:
        return False

    before_tokens = _history_tokens(turns)
    over_budget = before_tokens > token_budget
    if not force and not over_budget:
        if notify is not None and token_budget > 0:
            decile = int(before_tokens * 10 // token_budget)
            # Capped at 9 (<100%) -- decile 10+ only ever gets the
            # "compaction just happened" wording below, never this generic
            # percentage one, even in the razor-thin edge case where
            # before_tokens lands exactly on token_budget (decile == 10 but
            # not, by this function's own `> token_budget` rule, "over
            # budget" yet).
            if _MIN_BUDGET_NOTICE_DECILE <= decile <= 9 and decile > history.last_budget_notice_decile:
                history.last_budget_notice_decile = decile
                try:
                    await notify(_budget_notice(decile * 10))
                except Exception as exc:
                    logger.error("unified: history-budget notify hook failed: %s", exc)
        return False

    keep_budget = token_budget // 2
    if force:
        keep_budget = min(keep_budget, before_tokens // 2)
    split = _split_for_compaction(turns, keep_budget)
    to_summarize = turns[:split]
    keep = turns[split:]

    if not to_summarize or all(_is_summary(t) for t in to_summarize):
        if over_budget:
            logger.warning(
                f"History is over budget (~{before_tokens} > ~{token_budget} estimated tokens) but "
                f"nothing older than the latest turn can be compacted -- the latest turn alone is "
                f"~{_history_tokens(keep)} estimated tokens. Consider raising history_token_budget."
            )
        return False

    try:
        summary_text = await _summarize_turns(
            to_summarize,
            llm_client=llm_client,
            llm_config=llm_config,
            max_tokens=_summary_max_tokens(token_budget, llm_config.get("max_tokens")),
        )
        if not summary_text:
            raise ValueError("empty summary")
        history.turns = [ConversationTurn(query=_COMPACTED_QUERY_LABEL, response=summary_text)] + keep
        logger.info(
            f"Compacted {len(to_summarize)} conversation turns into a summary "
            f"(~{before_tokens} -> ~{_history_tokens(history.turns)} estimated tokens)"
        )
    except Exception as exc:
        history.turns = keep
        logger.warning(
            f"History summarization failed ({exc!r}) -- fell back to dropping the oldest "
            f"{len(to_summarize)} turns without a summary "
            f"(~{before_tokens} -> ~{_history_tokens(history.turns)} estimated tokens)"
        )

    after_tokens = _history_tokens(history.turns)
    if after_tokens > token_budget:
        logger.warning(
            f"History is still over budget after compaction (~{after_tokens} > ~{token_budget} "
            f"estimated tokens): the most recent turn(s) alone exceed it. Consider raising "
            f"history_token_budget so compaction doesn't fire on every turn."
        )

    history.last_budget_notice_decile = 0
    if notify is not None:
        try:
            await notify(_COMPACTED_NOTICE)
        except Exception as exc:
            logger.error("unified: history-budget notify hook failed: %s", exc)

    return True
