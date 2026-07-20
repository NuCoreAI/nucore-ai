"""``notify`` action -- send a notification. Scaffolding only: no domain
concept of "notification recipients" exists anywhere in this codebase
(confirmed via grep), and no discovery tool ships this iteration -- same
treatment as var/x10.

Grammar:
    notify(recipient=<recipient_id>)
    notify(recipient=<recipient_id>, content=<content_id>)
"""

from __future__ import annotations

import ast
from typing import Any

from ..core import TriggerCompileError, call_args, literal, register_action_call


def compile_notify(expr: ast.Call) -> dict[str, Any]:
    _, kwargs = call_args(expr)
    if "recipient" not in kwargs:
        raise TriggerCompileError("notify(...) requires recipient=.")
    recipient = literal(kwargs["recipient"])
    if isinstance(recipient, bool) or not isinstance(recipient, int):
        raise TriggerCompileError("notify(...)'s recipient= must be an integer id.")

    out: dict[str, Any] = {"type": "notify", "recipient": recipient}
    if "content" in kwargs:
        content = literal(kwargs["content"])
        if isinstance(content, bool) or not isinstance(content, int):
            raise TriggerCompileError("notify(...)'s content= must be an integer id.")
        out["content"] = content

    return out


register_action_call("notify", compile_notify)
