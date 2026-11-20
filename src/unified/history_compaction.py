"""Token-based compaction for :class:`~models.ConversationHistory`.

This is the *only* limit on how much history is replayed -- there is no
turn-count cap. Dropping the oldest turn on every append would shift the
provider's cached prompt prefix every turn; compaction instead runs only
when the estimated token size exceeds the budget, and then compacts down to
about half the budget (hysteresis), so the prefix stays stable until the
history has regrown by about half the budget.

When the caller supplies a *fork_summarizer* (the Claude path),
``maybe_compact_history`` asks it to summarize the whole live conversation --
a fork of the real request, so the summarizer reads the already-cached
history and sees every tool result -- and replaces the entire history with
that one summary ("simple compaction").

Otherwise it keeps the most recent turns that fit in that half-budget (always
at least the latest turn) and collapses everything older into one summary
pseudo-turn via a single standalone LLM call, rather than silently dropping
it. That is also the fallback when a fork fails.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from .adapters import LLMAdapter
from .models import ConversationHistory, ConversationTurn
from .usage_stats import record_compaction
from utils import get_logger

logger = get_logger(__name__)

_DEFAULT_TOKEN_BUDGET = 20000
_COMPACTED_QUERY_LABEL = "[earlier conversation, summarized]"

# Lowest token-budget decile (5 == 50%) that ever triggers a `notify` call --
# see maybe_compact_history's own docstring.
_MIN_BUDGET_NOTICE_DECILE = 5

# Trailing blank line: the UI appends the reply stream to the notice's bubble,
# so without it the first reply token fuses onto the closing asterisk.
_COMPACTED_NOTICE = (
    "*Session history was over budget, so I compacted the older turns. You can also do "
    "this any time with /compact-session.*\n\n"
)


def _budget_notice(percent: int) -> str:
    return f"*Session history is at {percent}% of its budget -- you can compact it any time with /compact-session.*\n\n"

_SUMMARY_PROMPT_HEADER = (
    "Summarize the conversation turns below concisely, in plain prose (no headers/bullets "
    "needed unless they help). Preserve concrete facts a later turn might still depend on -- "
    "device/routine names and ids, decisions made, values reported by tools -- and omit "
    "meta-commentary about the summarization itself. Each turn also lists the tools the "
    "assistant called, with short excerpts of what they returned: treat those as things the "
    "assistant actually observed, and keep the concrete ones (names, folders, ids, paths, "
    "values) in the summary as established facts, so later turns don't doubt or retract them. "
    "Do not fabricate anything not present below. Keep the summary under {max_words} words.\n\n"
)

# The final message of a forked summarization request: the live conversation
# so far, then this. The summary alone replaces the history, so it has to carry
# everything a later turn will depend on.
_FORK_INSTRUCTION = (
    "The conversation will continue from a summary alone -- everything above is about to be "
    "replaced by it. Write that summary now, in plain prose, and do not call any tools. Cover: "
    "(1) what the user has asked for and why; (2) what has been done so far and what has not; "
    "(3) the concrete facts you observed from tool results -- names, folders, files, ids, paths, "
    "values, errors -- stated as established facts so they are not doubted or retracted later; "
    "(4) decisions and preferences the user expressed; (5) the current state and the exact next "
    "step, including any question you just asked the user and what you were about to do. Do not "
    "invent anything that is not present above. Keep it under {max_words} words."
)

# What the summarizer sees of a turn's tool calls: enough to keep concrete
# findings (a folder name, an id, a value) alive, nowhere near the full output.
_TOOL_ARGS_CHARS = 150
_TOOL_RESULT_CHARS = 500
_TOOL_ACTIVITY_CHARS_PER_TURN = 2500


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


def _one_line(value: Any, limit: int, *, note_length: bool = False) -> str:
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        except Exception:
            value = str(value)
    value = " ".join(value.split())
    if len(value) <= limit:
        return value
    return value[:limit] + (f"... [{len(value):,} chars in all]" if note_length else "...")


def _result_text(content: Any) -> Any:
    """A tool result's text whether it is a string or a list of content blocks."""
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return content


def _tool_activity(turn: ConversationTurn) -> list[str]:
    """One ``name(args) -> result excerpt`` line per tool call in a turn's rich
    transcript, in call order. Recognizes Claude's ``tool_use``/``tool_result``
    blocks, OpenAI chat's ``tool_calls``/``role: "tool"`` messages and OpenAI
    Responses' ``function_call``/``function_call_output`` items; a native web
    search shows only its query (its result block is opaque). A turn with no
    rich transcript has none."""
    calls: list[tuple[str, str, Any]] = []  # (call id, name, args)
    results: dict[str, Any] = {}
    for message in turn.messages or []:
        if message.get("type") == "function_call":
            calls.append((str(message.get("call_id", "")), message.get("name", "?"), message.get("arguments")))
        elif message.get("type") == "function_call_output":
            results[str(message.get("call_id", ""))] = message.get("output")
        elif message.get("role") == "tool":
            results[str(message.get("tool_call_id", ""))] = message.get("content")
        content = message.get("content")
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            calls.append((str(call.get("id", "")), function.get("name", "?"), function.get("arguments")))
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    calls.append((str(block.get("id", "")), block.get("name", "?"), block.get("input")))
                elif block.get("type") == "server_tool_use":
                    calls.append(("", block.get("name", "?"), block.get("input")))
                elif block.get("type") == "tool_result":
                    results[str(block.get("tool_use_id", ""))] = _result_text(block.get("content"))
    lines: list[str] = []
    used = 0
    for index, (call_id, name, args) in enumerate(calls):
        line = f"- {name}({_one_line(args if args is not None else '', _TOOL_ARGS_CHARS)})"
        if call_id and call_id in results:
            line += f" -> {_one_line(results[call_id], _TOOL_RESULT_CHARS, note_length=True)}"
        if used + len(line) > _TOOL_ACTIVITY_CHARS_PER_TURN:
            lines.append(f"- ({len(calls) - index} more tool call(s) omitted)")
            break
        lines.append(line)
        used += len(line)
    return lines


def _render_turns_for_summary(turns: list[ConversationTurn]) -> str:
    parts = []
    for turn in turns:
        part = f"User: {turn.query}\nAssistant: {turn.response}"
        activity = _tool_activity(turn)
        if activity:
            part += "\nTools the assistant called this turn:\n" + "\n".join(activity)
        parts.append(part)
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
    fork_summarizer: Callable[[str, int], Awaitable[str]] | None = None,
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

    *fork_summarizer*, when given and the history has at least 2 turns, is
    awaited first with ``(instruction, max_tokens)`` and must return a
    summary of the whole live conversation (see ``AgenticLoop.summarize``).
    On success the entire history becomes that one summary turn. If it raises
    or returns nothing, a warning is logged and the standalone summarizer
    path below runs as if no fork had been offered.

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

    if fork_summarizer is not None and len(turns) >= 2:
        max_tokens = _summary_max_tokens(token_budget, llm_config.get("max_tokens"))
        try:
            fork_text = await fork_summarizer(
                _FORK_INSTRUCTION.format(max_words=max(100, int(max_tokens * 0.6))), max_tokens
            )
            fork_text = (fork_text or "").strip()
            if not fork_text:
                raise ValueError("empty summary")
        except Exception as exc:
            logger.warning(f"Forked summarization failed ({exc!r}) -- falling back to the standalone summarizer")
        else:
            turn_count = len(turns)
            history.turns = [ConversationTurn(query=_COMPACTED_QUERY_LABEL, response=fork_text)]
            logger.info(
                f"Compacted {turn_count} conversation turns via fork into a summary "
                f"(~{before_tokens} -> ~{_history_tokens(history.turns)} estimated tokens)"
            )
            record_compaction(history.usage, "fork", before_tokens, _history_tokens(history.turns))
            history.last_budget_notice_decile = 0
            if notify is not None:
                try:
                    await notify(_COMPACTED_NOTICE)
                except Exception as exc:
                    logger.error("unified: history-budget notify hook failed: %s", exc)
            return True

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

    compaction_kind = "summary"
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
        compaction_kind = "truncated"
        history.turns = keep
        logger.warning(
            f"History summarization failed ({exc!r}) -- fell back to dropping the oldest "
            f"{len(to_summarize)} turns without a summary "
            f"(~{before_tokens} -> ~{_history_tokens(history.turns)} estimated tokens)"
        )

    after_tokens = _history_tokens(history.turns)
    record_compaction(history.usage, compaction_kind, before_tokens, after_tokens)
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
