"""AST-based compiler for the ``routine_automation_python`` DSL.

The LLM authors routine logic as a small, restricted Python-like snippet
(see ``prompt.md`` for the documented builtin vocabulary). This module turns
that snippet into the exact same flat routine dict
(``{"name","id","parent","enabled","comment","if","then","else"}``) that the
original JSON-based ``routine_automation`` intent produces, so it can be
handed to ``nucore_interface.create_automation_routine`` /
``update_routine`` unchanged.

Safety note: this module only ever calls ``ast.parse`` on the untrusted
source. It never calls ``compile``, ``exec``, or ``eval``. The syntax tree is
walked by hand and only the handful of node shapes documented in
``prompt.md`` are given meaning — anything else raises
:class:`RoutineCompileError` with a message specific enough to drive a
repair turn.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any


class RoutineCompileError(Exception):
    """Raised when routine source cannot be translated to the NuCore routine schema."""


@dataclass(frozen=True)
class _Duration:
    """Internal representation of a ``duration(hour=, minute=, second=)`` call.

    This is never exposed to the LLM as JSON -- it exists purely so the model
    never has to do unit arithmetic (multiplying minutes by 60, decomposing
    "90 minutes" into hours+minutes, etc). The compiler is the only thing
    that ever converts it: to a single signed total-seconds integer for
    wait/sunrise/sunset offsets, or to a normalised non-negative
    hours/minutes/seconds triple for repeat's "every" period.
    """

    hour: float = 0
    minute: float = 0
    second: float = 0

    def total_seconds(self) -> int:
        return int(round(self.hour * 3600 + self.minute * 60 + self.second))


_COMPARE_OPS: dict[type, str] = {
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Eq: "==",
    ast.NotEq: "!=",
}

_SCHEDULE_FUNCS = {"at", "weekly_at", "weekly_between", "weekly_for", "between"}


def compile_routine_source(
    *,
    name: str,
    routine_id: int | None,
    comment: str | None,
    source: str,
) -> dict[str, Any]:
    """Compile one routine's DSL ``source`` into the NuCore routine dict.

    Raises:
        RoutineCompileError: On any syntax/shape the DSL does not recognise.
            The message is written to be fed back to the model verbatim.
    """
    if not isinstance(source, str) or not source.strip():
        raise RoutineCompileError("Routine 'code' is empty.")

    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError as exc:
        raise RoutineCompileError(f"Python syntax error: {exc}") from exc

    body = tree.body
    if not body:
        raise RoutineCompileError("Routine 'code' has no statements.")

    if len(body) == 1 and isinstance(body[0], ast.If):
        if_node = body[0]
        if len(if_node.orelse) == 1 and isinstance(if_node.orelse[0], ast.If):
            raise RoutineCompileError("`elif` is not supported. Use a single `if`/`else` only.")

        condition = _compile_condition(if_node.test)
        then_actions = _compile_actions(if_node.body, allow_repeat=True)
        else_actions = _compile_actions(if_node.orelse, allow_repeat=True)
    else:
        # No top-level `if` at all: an unconditional routine consisting only
        # of action statements. NuCore treats an empty `if` list as "always
        # true" (see iox_wrapper.create_automation_routine), so these actions
        # simply have no trigger condition of their own -- e.g. a
        # manually-run macro with only actions, no condition or trigger.
        if any(isinstance(stmt, ast.If) for stmt in body):
            raise RoutineCompileError(
                "Routine source must be either exactly one top-level "
                "`if <condition>: ... else: ...` statement, or, for a routine with no "
                "condition/trigger, only action statements and no `if` at all."
            )
        condition = []
        then_actions = _compile_actions(body, allow_repeat=True)
        else_actions = []

    return {
        "name": name,
        "id": routine_id,
        "parent": 0,
        "enabled": True,
        "comment": comment or "",
        "if": condition,
        "then": then_actions,
        "else": else_actions,
    }


# ---------------------------------------------------------------------------
# Condition compilation (`if` array: COS / COC / SCHEDULE + logic operators)
# ---------------------------------------------------------------------------

def _compile_condition(expr: ast.expr) -> list[dict[str, Any]]:
    if isinstance(expr, ast.BoolOp):
        op_token = "and" if isinstance(expr.op, ast.And) else "or"
        tokens: list[dict[str, Any]] = []
        for i, operand in enumerate(expr.values):
            operand_tokens = _compile_condition(operand)
            if isinstance(operand, ast.BoolOp):
                # Preserve grouping explicitly rather than relying on
                # inferred precedence — always safe, never ambiguous.
                tokens.append({"logic": "("})
                tokens.extend(operand_tokens)
                tokens.append({"logic": ")"})
            else:
                tokens.extend(operand_tokens)
            if i < len(expr.values) - 1:
                tokens.append({"logic": op_token})
        return tokens

    return [_compile_subexpression(expr)]


def _compile_subexpression(expr: ast.expr) -> dict[str, Any]:
    if isinstance(expr, ast.Compare):
        return _compile_cos(expr)
    if isinstance(expr, ast.Call):
        func = expr.func
        if isinstance(func, ast.Attribute) and func.attr == "was_controlled":
            return _compile_coc(expr)
        if isinstance(func, ast.Name) and func.id in _SCHEDULE_FUNCS:
            return _compile_schedule(func.id, expr)
    raise RoutineCompileError(
        "Unrecognised condition. Each condition must be one of: a "
        "`device(...).status(...)` comparison, a `device(...).was_controlled(...)` "
        f"call, or a schedule call ({', '.join(sorted(_SCHEDULE_FUNCS))})."
    )


def _compile_cos(expr: ast.Compare) -> dict[str, Any]:
    if len(expr.ops) != 1 or len(expr.comparators) != 1:
        raise RoutineCompileError(
            "Chained comparisons (e.g. `a < b < c`) are not supported; write one comparison per condition."
        )
    op_type = type(expr.ops[0])
    if op_type not in _COMPARE_OPS:
        raise RoutineCompileError(f"Unsupported comparison operator: {op_type.__name__}")

    device_id, method, _args, kwargs = _match_device_method(expr.left)
    if method != "status":
        raise RoutineCompileError("The left side of a comparison must be `device(...).status(...)`.")

    args = _args
    prop_id = kwargs.get("property") if "property" in kwargs else (args[0] if args else None)
    if prop_id is None:
        raise RoutineCompileError("`device(...).status(property_id, uom=..., precision=...)` requires a property id.")

    uom = kwargs.get("uom")
    precision = kwargs.get("precision")
    if uom is None or precision is None:
        raise RoutineCompileError("`status(...)` requires `uom` and `precision` keyword arguments.")

    value = _literal(expr.comparators[0])
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise RoutineCompileError("Comparison value must be a number.")

    return {
        "device": device_id,
        "status": prop_id,
        "comp": _COMPARE_OPS[op_type],
        "value": value,
        "uom": uom,
        "precision": precision,
    }


def _compile_coc(expr: ast.Call) -> dict[str, Any]:
    device_id = _match_device_ref(expr.func.value)
    if device_id is None:
        raise RoutineCompileError("`was_controlled(...)` must be called on a `device(...)` reference.")

    args, kwargs = _call_args(expr)
    control = kwargs.get("command") if "command" in kwargs else (args[0] if args else None)
    if control is None:
        raise RoutineCompileError("`was_controlled(command=..., ...)` requires a command id.")
    eq = kwargs.get("eq", "is")
    if eq not in ("is", "isnot"):
        raise RoutineCompileError("`eq` must be 'is' or 'isnot'.")

    parameters = kwargs.get("params", [])
    if not isinstance(parameters, list):
        raise RoutineCompileError("`params=` must be a list of `param(...)` calls.")
    return {"device": device_id, "eq": eq, "control": control, "parameters": parameters}


def _compile_schedule(func_name: str, expr: ast.Call) -> dict[str, Any]:
    _args, kwargs = _call_args(expr)

    if func_name == "at":
        block = _at_block(kwargs.get("time"), kwargs.get("sunrise"), kwargs.get("sunset"))
        if kwargs.get("date") is not None:
            block["date"] = kwargs["date"]
        return {"at": block}

    if func_name == "weekly_at":
        days = _require_days(kwargs)
        block = _at_block(kwargs.get("time"), kwargs.get("sunrise"), kwargs.get("sunset"))
        return {"weekly": {"days": days, "at": block}}

    if func_name == "weekly_between":
        days = _require_days(kwargs)
        from_block = _at_block(kwargs.get("from_time"), kwargs.get("from_sunrise"), kwargs.get("from_sunset"))
        to_block = _at_block(kwargs.get("to_time"), kwargs.get("to_sunrise"), kwargs.get("to_sunset"))
        to_block["day"] = kwargs.get("to_day", 0)
        return {"weekly": {"days": days, "from": from_block, "to": to_block}}

    if func_name == "weekly_for":
        days = _require_days(kwargs)
        from_block = _at_block(kwargs.get("from_time"), kwargs.get("from_sunrise"), kwargs.get("from_sunset"))
        period = kwargs.get("duration") if "duration" in kwargs else (_args[0] if _args else None)
        period_seconds = _seconds_from_duration(
            period,
            "weekly_for(...) requires a duration(...) call for the period, e.g. "
            "weekly_for(days=..., from_sunrise=..., duration=duration(hour=2)).",
        )
        if period_seconds < 0:
            raise RoutineCompileError("weekly_for(...) duration must not be negative.")
        hours, minutes, seconds = _decompose_seconds(period_seconds)
        for_block = {"hours": hours, "minutes": minutes, "seconds": seconds}
        return {"weekly": {"days": days, "from": from_block, "for": for_block}}

    if func_name == "between":
        from_block = _at_block(kwargs.get("from_time"), kwargs.get("from_sunrise"), kwargs.get("from_sunset"))
        if kwargs.get("from_date") is not None:
            from_block["date"] = kwargs["from_date"]
        to_block = _at_block(kwargs.get("to_time"), kwargs.get("to_sunrise"), kwargs.get("to_sunset"))
        if kwargs.get("to_date") is not None:
            to_block["date"] = kwargs["to_date"]
        return {"from": from_block, "to": to_block}

    raise RoutineCompileError(f"Unknown schedule function '{func_name}'.")


def _at_block(time_val: Any, sunrise_val: Any, sunset_val: Any) -> dict[str, Any]:
    provided = [(k, v) for k, v in (("time", time_val), ("sunrise", sunrise_val), ("sunset", sunset_val)) if v is not None]
    if len(provided) != 1:
        raise RoutineCompileError(
            "Exactly one of time=, sunrise=, or sunset= must be given for a schedule time reference."
        )
    key, value = provided[0]
    if key == "time":
        if not isinstance(value, str):
            raise RoutineCompileError("time= must be a \"HH:MM:SS\" string.")
        return {"time": value}
    seconds = _seconds_from_duration(
        value,
        f"{key}= requires a duration(...) call, e.g. {key}=duration(minute=-10) for 10 minutes before "
        f"or {key}=duration(minute=10) for 10 minutes after -- never a raw number.",
    )
    return {key: seconds}


def _require_days(kwargs: dict[str, Any]) -> str:
    days = kwargs.get("days")
    if not days or not isinstance(days, str):
        raise RoutineCompileError("Weekly schedules require `days=\"mon,tue,...\"`.")
    return days


# ---------------------------------------------------------------------------
# Action compilation (`then` / `else` arrays)
# ---------------------------------------------------------------------------

def _compile_actions(stmts: list[ast.stmt], *, allow_repeat: bool) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for stmt in stmts:
        if isinstance(stmt, ast.Pass):
            # `pass` is a no-op, same as it is in real Python -- most commonly
            # written for an intentionally empty `else:` block. Silently
            # contributing zero actions is correct: the NuCore schema already
            # allows an empty then/else array.
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            actions.append(_compile_action_call(stmt.value))
        elif isinstance(stmt, ast.For):
            if not allow_repeat:
                raise RoutineCompileError("Repeat markers cannot be nested inside another repeat.")
            actions.extend(_compile_repeat(stmt))
        else:
            raise RoutineCompileError(
                f"Unsupported statement `{type(stmt).__name__}` in routine actions. Only device "
                "commands, `wait(...)`, `pass`, and `for _ in repeat(...)/every(...):` are allowed."
            )
    return actions


def _compile_action_call(expr: ast.Call) -> dict[str, Any]:
    func = expr.func
    if isinstance(func, ast.Name) and func.id == "wait":
        args, kwargs = _call_args(expr)
        duration_val = kwargs.get("duration") if "duration" in kwargs else (args[0] if args else None)
        seconds = _seconds_from_duration(
            duration_val,
            "wait(...) requires a duration(...) call, e.g. wait(duration(minute=5)) -- never a raw number.",
        )
        if seconds < 0:
            raise RoutineCompileError("wait(...) duration must not be negative.")
        return {"wait": {"duration": seconds, "random": bool(kwargs.get("random", False))}}

    if isinstance(func, ast.Attribute) and func.attr == "command":
        device_id = _match_device_ref(func.value)
        if device_id is None:
            raise RoutineCompileError("`.command(...)` must be called on a `device(...)` reference.")
        args, kwargs = _call_args(expr)
        command = kwargs.get("command") if "command" in kwargs else (args[0] if args else None)
        if command is None:
            raise RoutineCompileError("`device(...).command(command_id, ...)` requires a command id.")
        parameters = kwargs.get("params", [])
        if not isinstance(parameters, list):
            raise RoutineCompileError("`params=` must be a list of `param(...)` calls.")
        return {"device": device_id, "command": command, "parameters": parameters}

    raise RoutineCompileError("Unrecognised action call. Actions must be `device(...).command(...)` or `wait(...)`.")


def _compile_repeat(stmt: ast.For) -> list[dict[str, Any]]:
    call = stmt.iter
    if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name) or call.func.id not in ("repeat", "every"):
        raise RoutineCompileError("`for` loops must iterate `repeat(...)` or `every(...)`.")
    if stmt.orelse:
        raise RoutineCompileError("`for...else` is not supported.")

    args, kwargs = _call_args(call)
    if call.func.id == "repeat":
        count = kwargs.get("count")
        if count is None:
            raise RoutineCompileError("`repeat(count=..., ...)` requires `count`.")
        marker = {"repeat": {"type": "for", "count": count, "random": bool(kwargs.get("random", False))}}
    else:
        duration_val = kwargs.get("duration") if "duration" in kwargs else (args[0] if args else None)
        period_seconds = _seconds_from_duration(
            duration_val,
            "every(...) requires a duration(...) call, e.g. every(duration(hour=2)) -- never raw hours/minutes/seconds.",
        )
        if period_seconds < 0:
            raise RoutineCompileError("every(...) duration must not be negative.")
        hours, minutes, seconds = _decompose_seconds(period_seconds)
        marker = {"repeat": {"type": "every", "hours": hours, "minutes": minutes, "seconds": seconds}}

    body = _compile_actions(stmt.body, allow_repeat=False)
    if not body:
        raise RoutineCompileError("A repeat block must contain at least one action.")
    return [marker, *body]


# ---------------------------------------------------------------------------
# Low-level node helpers.
#
# These are the ONLY node shapes ever given semantic meaning. Anything not
# explicitly matched here falls through to a RoutineCompileError above.
# Nothing in this module calls compile()/exec()/eval() on the source — only
# ast.parse() (which does not execute code) followed by manual tree walking.
# ---------------------------------------------------------------------------

def _match_device_ref(node: ast.expr) -> str | None:
    """Return the device id if ``node`` is a bare ``device("id")`` call, else None."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "device":
        if len(node.args) == 1:
            value = _literal(node.args[0])
            if isinstance(value, str):
                return value
    return None


def _match_device_method(node: ast.expr) -> tuple[str, str, list[Any], dict[str, Any]]:
    """Match ``device("id").method(...)`` -> (device_id, method_name, args, kwargs)."""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        raise RoutineCompileError("Expected a `device(...).method(...)` call.")
    device_id = _match_device_ref(node.func.value)
    if device_id is None:
        raise RoutineCompileError("Expected a `device(...).method(...)` call.")
    args, kwargs = _call_args(node)
    return device_id, node.func.attr, args, kwargs


def _call_args(node: ast.Call) -> tuple[list[Any], dict[str, Any]]:
    """Extract literal positional args and keyword args from a Call node."""
    if any(isinstance(a, ast.Starred) for a in node.args):
        raise RoutineCompileError("`*args` expansion is not supported.")
    args = [_literal(a) for a in node.args]
    kwargs: dict[str, Any] = {}
    for kw in node.keywords:
        if kw.arg is None:
            raise RoutineCompileError("`**kwargs` expansion is not supported.")
        kwargs[kw.arg] = _literal(kw.value)
    return args, kwargs


def _literal(node: ast.expr) -> Any:
    """Extract a Python literal value from a restricted set of node shapes."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _literal(node.operand)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise RoutineCompileError("Unary +/- is only supported on numbers.")
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.List):
        return [_literal(elt) for elt in node.elts]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == "param":
            return _param_call(node)
        if node.func.id == "duration":
            return _duration_call(node)
    raise RoutineCompileError(
        "Only literal values (numbers, strings, booleans, None, lists, `param(...)`, and "
        f"`duration(...)`) are allowed; got `{ast.dump(node)}`."
    )


def _param_call(node: ast.Call) -> dict[str, Any]:
    args, kwargs = _call_args(node)
    return {
        "id": kwargs.get("id", args[0] if args else "n/a"),
        "value": kwargs.get("value"),
        "uom": kwargs.get("uom"),
        "precision": kwargs.get("precision"),
    }


def _duration_call(node: ast.Call) -> _Duration:
    args, kwargs = _call_args(node)
    if args:
        raise RoutineCompileError("duration(...) takes only keyword arguments: hour=, minute=, second=.")
    unknown = set(kwargs) - {"hour", "minute", "second"}
    if unknown:
        raise RoutineCompileError(
            f"duration(...) does not accept {', '.join(sorted(unknown))}; use hour=, minute=, and/or second= only."
        )
    for key, value in kwargs.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise RoutineCompileError(f"duration({key}=...) must be a number.")
    return _Duration(hour=kwargs.get("hour", 0), minute=kwargs.get("minute", 0), second=kwargs.get("second", 0))


def _decompose_seconds(total_seconds: int) -> tuple[int, int, int]:
    """Split a non-negative total-seconds count into a normalised (hours, minutes, seconds) triple."""
    hours, remainder = divmod(int(round(total_seconds)), 3600)
    minutes, seconds = divmod(remainder, 60)
    return hours, minutes, seconds


def _seconds_from_duration(value: Any, error_message: str) -> int:
    """Require ``value`` to be a :class:`_Duration` and return its signed total seconds."""
    if not isinstance(value, _Duration):
        raise RoutineCompileError(error_message)
    return value.total_seconds()
