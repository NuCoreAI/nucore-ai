"""``inet`` (OpenADR utility demand-response signal) conditions -- 3
sub-shapes, all self-contained fixed enums, no external id lookup needed.

Grammar:
    utility_price(op=">", value=<n>)          # op is >,>=,<,<=,==,!=
    utility_status(op="is"|"isnot", value="inactive"|"active"|"pendingVeryNear"|"pendingNear"|"pendingFar"|"pendingVeryFar")
    utility_mode(op="is"|"isnot", value="none"|"normal"|"moderate"|"high"|"special")
"""

from __future__ import annotations

import ast
from typing import Any

from ..core import COMPARE_STR_TOKENS, TriggerCompileError, call_args, literal, register_condition_call

_EQ_STR_TO_TOKEN = {"is": "IS", "isnot": "ISNOT"}
_STATUS_VALUES = {"inactive", "active", "pendingVeryNear", "pendingNear", "pendingFar", "pendingVeryFar"}
_MODE_VALUES = {"none", "normal", "moderate", "high", "special"}


def compile_utility_price(expr: ast.Call) -> dict[str, Any]:
    _, kwargs = call_args(expr)
    if "op" not in kwargs or "value" not in kwargs:
        raise TriggerCompileError("utility_price(...) requires op= and value=.")
    op = literal(kwargs["op"])
    if op not in COMPARE_STR_TOKENS:
        raise TriggerCompileError("utility_price(...)'s op= must be one of: >, >=, <, <=, ==, !=")
    value = literal(kwargs["value"])
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TriggerCompileError("utility_price(...)'s value= must be a number.")
    return {"type": "inet", "id": "oadr", "control": "price", "op": COMPARE_STR_TOKENS[op], "action": value}


def compile_utility_status(expr: ast.Call) -> dict[str, Any]:
    _, kwargs = call_args(expr)
    if "op" not in kwargs or "value" not in kwargs:
        raise TriggerCompileError("utility_status(...) requires op= and value=.")
    op = literal(kwargs["op"])
    if op not in _EQ_STR_TO_TOKEN:
        raise TriggerCompileError("utility_status(...)'s op= must be 'is' or 'isnot'.")
    value = literal(kwargs["value"])
    if value not in _STATUS_VALUES:
        raise TriggerCompileError(f"utility_status(...)'s value= must be one of: {', '.join(sorted(_STATUS_VALUES))}")
    return {"type": "inet", "id": "oadr", "control": "status", "op": _EQ_STR_TO_TOKEN[op], "action": value}


def compile_utility_mode(expr: ast.Call) -> dict[str, Any]:
    _, kwargs = call_args(expr)
    if "op" not in kwargs or "value" not in kwargs:
        raise TriggerCompileError("utility_mode(...) requires op= and value=.")
    op = literal(kwargs["op"])
    if op not in _EQ_STR_TO_TOKEN:
        raise TriggerCompileError("utility_mode(...)'s op= must be 'is' or 'isnot'.")
    value = literal(kwargs["value"])
    if value not in _MODE_VALUES:
        raise TriggerCompileError(f"utility_mode(...)'s value= must be one of: {', '.join(sorted(_MODE_VALUES))}")
    return {"type": "inet", "id": "oadr", "control": "mode", "op": _EQ_STR_TO_TOKEN[op], "action": value}


register_condition_call("utility_price", compile_utility_price)
register_condition_call("utility_status", compile_utility_status)
register_condition_call("utility_mode", compile_utility_mode)
