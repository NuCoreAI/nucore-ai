"""Per-session token accounting behind the ``/session-stats`` command.

Every model request's usage is normalized (Claude, OpenAI chat/Responses and
Gemini each report it differently) and accumulated in a plain dict that lives
on ``ConversationHistory.usage``, so it is saved with the session and does not
depend on the prompt log, which is a debug option. All functions here work on
that dict and never raise: a stat must not break a conversation.

The report also splits the *next* request into system sections, tools and
history. Those sizes are estimates (``estimate_tokens``), scaled to the last
real request's input total when that is still comparable, so the parts add up
to a measured number.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

_RECENT_LIMIT = 20
_EVENT_LIMIT = 20

# (model-name prefix, (input $/Mtok, output $/Mtok)). Cache reads bill at
# 0.1x input, writes at 1.25x (5m) / 2x (1h). A model that matches nothing is
# simply not priced -- the report then omits dollars rather than guessing.
_PRICES: tuple[tuple[str, tuple[float, float]], ...] = (
    ("claude-sonnet-5", (2.0, 10.0)),
    ("claude-haiku-4-5", (1.0, 5.0)),
)

# (model-name prefix, context window in tokens). Unknown -> None; a profile's
# ``context_window`` overrides this.
_WINDOWS: tuple[tuple[str, int], ...] = (
    ("claude-sonnet-5", 1_000_000),
    ("claude-haiku-4-5", 200_000),
    ("gemini-", 1_000_000),
    ("gpt-4o", 128_000),
)

_TOOL_BLOCK_TYPES = {"tool_use", "tool_result", "server_tool_use", "web_search_tool_result"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def context_window(model: str | None, override: Any = None) -> int | None:
    if isinstance(override, int) and not isinstance(override, bool) and override > 0:
        return override
    name = (model or "").lower()
    for prefix, window in _WINDOWS:
        if name.startswith(prefix):
            return window
    return None


def normalize_usage(raw: Any) -> dict[str, int]:
    """One provider usage dict -> ``input_total`` (everything the model read,
    cached or not), ``cache_read``, ``cache_write_5m``, ``cache_write_1h`` and
    ``output``. Unrecognized shapes give zeros."""
    zero = {"input_total": 0, "cache_read": 0, "cache_write_5m": 0, "cache_write_1h": 0, "output": 0}
    if not isinstance(raw, dict):
        return zero
    try:
        if "promptTokenCount" in raw or "candidatesTokenCount" in raw:
            return {
                **zero,
                "input_total": _int(raw.get("promptTokenCount")),
                "cache_read": _int(raw.get("cachedContentTokenCount")),
                "output": _int(raw.get("candidatesTokenCount")) + _int(raw.get("thoughtsTokenCount")),
            }
        if "prompt_tokens" in raw or "completion_tokens" in raw:
            details = raw.get("prompt_tokens_details") or {}
            return {
                **zero,
                "input_total": _int(raw.get("prompt_tokens")),
                "cache_read": _int((details or {}).get("cached_tokens")),
                "output": _int(raw.get("completion_tokens")),
            }
        if "cache_read_input_tokens" in raw or "cache_creation_input_tokens" in raw:
            read = _int(raw.get("cache_read_input_tokens"))
            created = _int(raw.get("cache_creation_input_tokens"))
            breakdown = raw.get("cache_creation") or {}
            w5 = _int(breakdown.get("ephemeral_5m_input_tokens"))
            w1 = _int(breakdown.get("ephemeral_1h_input_tokens"))
            if not (w5 or w1):
                w1 = created  # no breakdown reported; the adapter always asks for 1h
            return {
                "input_total": _int(raw.get("input_tokens")) + read + created,
                "cache_read": read,
                "cache_write_5m": w5,
                "cache_write_1h": w1,
                "output": _int(raw.get("output_tokens")),
            }
        details = raw.get("input_tokens_details") or {}
        return {
            **zero,
            "input_total": _int(raw.get("input_tokens")),
            "cache_read": _int((details or {}).get("cached_tokens")),
            "output": _int(raw.get("output_tokens")),
        }
    except Exception:
        return zero


def cost_usd(model: str | None, rec: dict[str, int]) -> float | None:
    name = (model or "").lower()
    for prefix, (price_in, price_out) in _PRICES:
        if name.startswith(prefix):
            fresh = max(0, rec["input_total"] - rec["cache_read"] - rec["cache_write_5m"] - rec["cache_write_1h"])
            total = (
                fresh * price_in
                + rec["cache_read"] * price_in * 0.1
                + rec["cache_write_5m"] * price_in * 1.25
                + rec["cache_write_1h"] * price_in * 2.0
                + rec["output"] * price_out
            )
            return total / 1e6
    return None


def record_request(usage: dict[str, Any], kind: str, raw_usage: Any, model: str | None = None) -> None:
    """Add one model request's usage to *usage* (``ConversationHistory.usage``)."""
    try:
        rec = normalize_usage(raw_usage)
        usd = cost_usd(model, rec)
        usage["requests"] = _int(usage.get("requests")) + 1
        for key, value in rec.items():
            usage[key] = _int(usage.get(key)) + value
        if usd is None:
            usage["unpriced_requests"] = _int(usage.get("unpriced_requests")) + 1
        else:
            usage["usd"] = float(usage.get("usd") or 0.0) + usd
        recent = usage.setdefault("recent", [])
        recent.append({"ts": _now(), "kind": kind, **rec, "usd": usd})
        del recent[:-_RECENT_LIMIT]
    except Exception:
        pass


def record_compaction(usage: dict[str, Any], kind: str, before: int, after: int) -> None:
    try:
        events = usage.setdefault("compactions", [])
        events.append({"ts": _now(), "kind": kind, "before": _int(before), "after": _int(after)})
        del events[:-_EVENT_LIMIT]
    except Exception:
        pass


def record_mid_turn_over_budget(usage: dict[str, Any], round_number: int, tokens: int, budget: int) -> None:
    try:
        usage["mid_turn_events"] = _int(usage.get("mid_turn_events")) + 1
        usage["last_mid_turn_event"] = {"ts": _now(), "round": round_number, "tokens": tokens, "budget": budget}
    except Exception:
        pass


def _tokens(text: str) -> int:
    from .history_compaction import estimate_tokens  # lazy: history_compaction imports this module

    return estimate_tokens(text)


def _dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def _is_tool_traffic(message: dict[str, Any]) -> bool:
    if message.get("role") == "tool" or message.get("tool_calls"):
        return True
    content = message.get("content")
    return isinstance(content, list) and any(
        isinstance(block, dict) and block.get("type") in _TOOL_BLOCK_TYPES for block in content
    )


def _section_label(index: int, text: str) -> str:
    markers = [m for m in ("DEVICE DATABASE", "ROUTINES DATABASE", "TIME & LOCATION", "USER PREFERENCES") if m in text]
    base = f"system section {index + 1}"
    return f"{base} ({', '.join(markers)})" if markers else base


def estimate_composition(
    sections: list[str],
    tools: list[dict[str, Any]],
    turns: list[Any],
    summary_label: str,
) -> list[tuple[str, int]]:
    """``(label, estimated_tokens)`` for each part of the next request."""
    parts: list[tuple[str, int]] = []
    for i, section in enumerate(s for s in sections if s):
        parts.append((_section_label(i, section), _tokens(section)))
    parts.append((f"tools ({len(tools)})", _tokens(_dumps(tools))))
    summary = chat = tool_traffic = 0
    for turn in turns:
        if turn.query == summary_label:
            summary += _tokens(turn.response)
            continue
        if not turn.messages:
            chat += _tokens(turn.query) + _tokens(turn.response)
            continue
        for message in turn.messages:
            size = _tokens(_dumps(message))
            if _is_tool_traffic(message):
                tool_traffic += size
            else:
                chat += size
    parts.append(("history: compacted summary", summary))
    parts.append(("history: conversation text", chat))
    parts.append(("history: tool calls and results", tool_traffic))
    return parts


def _n(value: int) -> str:
    return f"{value:,}"


def _pct(part: int, whole: int) -> str:
    return f"{part * 100 / whole:.0f}%" if whole else "-"


def format_report(
    usage: dict[str, Any],
    composition: list[tuple[str, int]],
    *,
    history_tokens: int,
    history_budget: int,
    window: int | None,
    max_tokens: int | None,
    model: str | None,
) -> str:
    """The ``/session-stats`` reply (Markdown)."""
    recent = usage.get("recent") or []
    last_round = next((r for r in reversed(recent) if r.get("kind") == "round"), None)
    compactions = usage.get("compactions") or []

    estimated = sum(tokens for _, tokens in composition)
    scale = 1.0
    calibrated = False
    # Comparable only while nothing has changed the history since that request.
    if last_round and estimated and not (compactions and compactions[-1]["ts"] >= last_round["ts"]):
        ratio = last_round["input_total"] / estimated
        if 0.5 <= ratio <= 2.0:
            scale, calibrated = ratio, True
    total = round(estimated * scale)

    lines = [f"**Session stats** ({model or 'unknown model'})", ""]
    context_line = f"**Context for the next request:** ~{_n(total)} tokens"
    if window:
        context_line += f" of {_n(window)} ({_pct(total, window)})"
    lines.append(context_line + (" -- calibrated to the last request" if calibrated else " -- estimated"))
    lines.append(
        f"**History:** ~{_n(history_tokens)} of its {_n(history_budget)}-token budget ({_pct(history_tokens, history_budget)})"
    )
    if max_tokens:
        lines.append(f"**Reply reserve (`max_tokens`):** {_n(max_tokens)}")
    lines += ["", "| Part | Tokens | Share |", "|---|---:|---:|"]
    for label, tokens in composition:
        scaled = round(tokens * scale)
        lines.append(f"| {label} | {_n(scaled)} | {_pct(scaled, total)} |")

    if last_round:
        read, written = last_round["cache_read"], last_round["cache_write_5m"] + last_round["cache_write_1h"]
        uncached = max(0, last_round["input_total"] - read - written)
        lines += [
            "",
            f"**Last request:** {_n(last_round['input_total'])} in ({_n(read)} cached read, {_n(written)} cache write, "
            f"{_n(uncached)} uncached; {_pct(read, last_round['input_total'])} hit), {_n(last_round['output'])} out",
        ]

    requests = _int(usage.get("requests"))
    if requests:
        read, total_in = _int(usage.get("cache_read")), _int(usage.get("input_total"))
        totals = (
            f"**Session totals:** {_n(requests)} requests, {_n(total_in)} input ({_pct(read, total_in)} read from cache), "
            f"{_n(_int(usage.get('cache_write_1h')))} written 1h / {_n(_int(usage.get('cache_write_5m')))} written 5m, "
            f"{_n(_int(usage.get('output')))} output"
        )
        if usage.get("usd") is not None and usage.get("usd"):
            suffix = " (some requests unpriced)" if usage.get("unpriced_requests") else ""
            totals += f", ~${usage['usd']:.2f}{suffix}"
        lines += ["", totals]
    else:
        lines += ["", "**Session totals:** no requests recorded for this session yet."]

    if compactions:
        last = compactions[-1]
        lines.append(
            f"**Compactions:** {len(compactions)} recorded; latest ({last['kind']}) ~{_n(last['before'])} -> ~{_n(last['after'])} tokens"
        )
    events = _int(usage.get("mid_turn_events"))
    if events:
        last_event = usage.get("last_mid_turn_event") or {}
        lines.append(
            f"**Mid-turn over-budget events:** {events} (latest at round {last_event.get('round')}, "
            f"~{_n(_int(last_event.get('tokens')))} tokens)"
        )

    if recent:
        lines += ["", "| When (UTC) | Request | In | Cached read | Written | Out |", "|---|---|---:|---:|---:|---:|"]
        for r in recent[-5:]:
            lines.append(
                f"| {r['ts'][11:19]} | {r['kind']} | {_n(r['input_total'])} | {_n(r['cache_read'])} | "
                f"{_n(r['cache_write_5m'] + r['cache_write_1h'])} | {_n(r['output'])} |"
            )
    return "\n".join(lines)
