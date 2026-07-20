"""``compile_trigger_source`` -- entry point for the routine DSL compiler,
targeting NuCore's ``Trigger``/``NewTrigger`` schema (9 condition types, 18
action types -- see the family modules under ``conditions/``/``actions/``
for the per-type grammar).

A pure ``ast``-based walker, never ``exec``/``eval``, that only gives
meaning to the handful of node shapes documented in
``tool_create_or_update_routine.json``'s tool description. Anything else
raises :class:`~.errors.TriggerCompileError` with a message specific enough
to drive an LLM repair turn.

The schema's ``if`` list is FLAT, with each condition carrying its own
``andOr`` -- not a nested boolean tree. Python's own operator precedence
already resolves a mixed ``and``/``or``
expression into nested ``ast.BoolOp`` nodes with different operators at each
level (`a and b or c` and `(a and b) or c` produce the *identical* tree --
parenthesization doesn't survive into the ast, only the grouping it implies
does) -- so :func:`_compile_bool_expr` walks that nesting directly and emits
a ``paren`` condition exactly where the schema needs one, with no separate
"did the user write explicit parens" tracking required.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any, Callable

from .errors import TriggerCompileError

# ---------------------------------------------------------------------------
# Shared literal/call/duration helpers -- used by every condition/action family.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Duration:
    """Internal representation of a ``duration(hour=, minute=, second=)``
    call -- never exposed to the LLM as JSON; exists purely so the model
    never has to do unit arithmetic itself. The compiler is the only thing
    that converts it, to a single signed total-seconds integer."""

    hour: float = 0
    minute: float = 0
    second: float = 0

    def total_seconds(self) -> int:
        return int(round(self.hour * 3600 + self.minute * 60 + self.second))


_DAY_KEYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")

# Python comparison operator -> new schema's StatusConditionOperator token.
# The DSL keeps real Python operators (more natural for the model to write,
# and identical ergonomics to v1); only the compiled *output* uses the
# schema's GE/GT/IS/ISNOT/LE/LT tokens.
_COMPARE_OPS: dict[type, str] = {
    ast.Gt: "GT",
    ast.GtE: "GE",
    ast.Lt: "LT",
    ast.LtE: "LE",
    ast.Eq: "IS",
    ast.NotEq: "ISNOT",
}


def literal(node: ast.expr) -> Any:
    """Extract a plain Python literal (number/string/bool/tuple/list/dict of
    literals) from an ast node. Never evaluates arbitrary expressions."""
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        raise TriggerCompileError(f"expected a literal value, got: {ast.unparse(node)}")


def call_args(expr: ast.Call) -> tuple[list[ast.expr], dict[str, ast.expr]]:
    """Extract raw ``(positional_args, keyword_args)`` ast nodes from an
    ``ast.Call`` -- deliberately NOT pre-resolved to literals via
    :func:`literal`, since a kwarg's value is often itself a nested call
    (``duration(...)``, ``param(...)``, a future ``var_ref(...)``) that the
    caller needs to dispatch on, not literal-evaluate. Callers call
    :func:`literal` themselves on whichever specific args/kwargs they expect
    to be plain values."""
    if any(isinstance(a, ast.Starred) for a in expr.args):
        raise TriggerCompileError("*args are not supported")
    if any(kw.arg is None for kw in expr.keywords):
        raise TriggerCompileError("**kwargs are not supported")
    return list(expr.args), {kw.arg: kw.value for kw in expr.keywords}


def duration_call(expr: ast.expr) -> Duration:
    """Parse a ``duration(hour=.., minute=.., second=..)`` call."""
    if not (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == "duration"):
        raise TriggerCompileError(f"expected duration(hour=.., minute=.., second=..), got: {ast.unparse(expr)}")
    _, kwargs = call_args(expr)
    unknown = set(kwargs) - {"hour", "minute", "second"}
    if unknown:
        raise TriggerCompileError(f"duration(...) does not accept: {', '.join(sorted(unknown))}")
    return Duration(**{k: literal(v) for k, v in kwargs.items()})


def days_dict(days_str: Any) -> dict[str, bool]:
    """Parse a comma-separated ``days="mon,wed,fri"`` string into the
    schema's ``{mon: true, wed: true, fri: true}`` shape."""
    if not isinstance(days_str, str):
        raise TriggerCompileError("days= must be a comma-separated string, e.g. \"mon,wed,fri\"")
    days = {d.strip().lower() for d in days_str.split(",") if d.strip()}
    unknown = days - set(_DAY_KEYS)
    if unknown:
        raise TriggerCompileError(
            f"unknown day(s): {', '.join(sorted(unknown))}; use a comma-separated subset of "
            "sun,mon,tue,wed,thu,fri,sat"
        )
    if not days:
        raise TriggerCompileError("days= must name at least one day")
    return {d: True for d in days}


def match_device_call(expr: ast.expr) -> tuple[str, str, list[Any], dict[str, Any]] | None:
    """Match ``device("<id>").method(...)``.

    Returns ``(device_id, method_name, args, kwargs)``, or ``None`` if
    *expr* isn't a call on a ``device(...)`` reference (the caller falls
    through to try other condition/action shapes)."""
    if not (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute)):
        return None
    device_call = expr.func.value
    if not (
        isinstance(device_call, ast.Call)
        and isinstance(device_call.func, ast.Name)
        and device_call.func.id == "device"
    ):
        return None
    dargs, dkwargs = call_args(device_call)
    device_id_node = dkwargs.get("id", dargs[0] if dargs else None)
    if device_id_node is None:
        raise TriggerCompileError("device(...) requires a device/group id string")
    device_id = literal(device_id_node)
    if not isinstance(device_id, str) or not device_id:
        raise TriggerCompileError("device(...) requires a device/group id string")
    args, kwargs = call_args(expr)
    return device_id, expr.func.attr, args, kwargs


def is_index_uom(uom: Any) -> bool:
    """UOM 25/146/148 (enumerated index) never gets precision-scaled --
    mirrors ``is_enumeration_uom`` in ``nucore/uom.py``, reimplemented here
    (not imported) to keep this compiler dependency-free of the live
    NuCoreInterface/device layer, matching v1's architecture."""
    return str(uom) in {"25", "146", "148"}


def scale_value(value: Any, uom: Any, precision: Any) -> int:
    """Scale a plain numeric value by precision, per the wire convention
    (raw value * 10**precision), except for index/enum uoms which are never
    scaled. Shared by every condition/action family that carries a
    val/param value (status, cmd params, var actions, ...)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TriggerCompileError("value must be a number.")
    if is_index_uom(uom):
        return int(value)
    return int(round(value * (10 ** int(precision))))


def compile_cmd_param(expr: ast.expr) -> dict[str, Any]:
    """Parse a ``param(id=, value=, uom=, precision=)`` call into a
    ``CmdParam`` dict -- shared by the ``cmd`` action and ``lp``'s nested
    ``rsp.cmd.p``."""
    if not (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == "param"):
        raise TriggerCompileError("params= must be a list of param(...) calls.")
    args, kwargs = call_args(expr)
    id_node = kwargs.get("id", args[0] if args else None)
    param_id = literal(id_node) if id_node is not None else "n/a"

    if "uom" not in kwargs or "precision" not in kwargs or "value" not in kwargs:
        raise TriggerCompileError("param(...) requires id, value, uom, and precision.")
    uom = literal(kwargs["uom"])
    precision = literal(kwargs["precision"])
    value = literal(kwargs["value"])
    scaled = scale_value(value, uom, precision)

    return {"type": "val", "id": param_id, "val": {"value": scaled, "prec": int(precision), "uom": uom}}


def compile_param_list(node: ast.expr | None) -> list[dict[str, Any]]:
    """Parse a ``params=[param(...), ...]`` list literal into ``CmdParam[]``."""
    if node is None:
        return []
    if not isinstance(node, ast.List):
        raise TriggerCompileError("params= must be a list of param(...) calls.")
    return [compile_cmd_param(item) for item in node.elts]


def compare_op_token(op_node: ast.cmpop) -> str:
    token = _COMPARE_OPS.get(type(op_node))
    if token is None:
        raise TriggerCompileError(f"unsupported comparison operator: {type(op_node).__name__}")
    return token


# String-keyed equivalent of _COMPARE_OPS, for the handful of new-domain
# constructs (inet, var's while) that take op= as a plain string kwarg
# instead of a real Python comparison (there's no natural "expression" shape
# for those -- e.g. a utility price signal isn't a device reference the DSL
# can put on the left of a real `>`).
COMPARE_STR_TOKENS: dict[str, str] = {">": "GT", ">=": "GE", "<": "LT", "<=": "LE", "==": "IS", "!=": "ISNOT"}


def parse_x10_house(node: ast.expr, label: str) -> str:
    """Shared by the ``x10`` condition and action -- a single house-code
    letter A-P."""
    house = literal(node)
    if not isinstance(house, str) or len(house) != 1 or not ("A" <= house.upper() <= "P"):
        raise TriggerCompileError(f"{label}'s house= must be a single letter A-P.")
    return house.upper()


def parse_x10_command(node: ast.expr, label: str) -> int:
    """Shared by the ``x10`` condition and action -- command code 0-15."""
    command = literal(node)
    if isinstance(command, bool) or not isinstance(command, int) or not (0 <= command <= 15):
        raise TriggerCompileError(f"{label}'s command= must be an integer 0-15.")
    return command


def parse_x10_unit(node: ast.expr, label: str) -> int:
    """Shared by the ``x10`` condition and action -- unit code 1-16."""
    unit = literal(node)
    if isinstance(unit, bool) or not isinstance(unit, int) or not (1 <= unit <= 16):
        raise TriggerCompileError(f"{label}'s unit= must be an integer 1-16.")
    return unit


def parse_var_ref(expr: ast.expr) -> tuple[int, str, int]:
    """Parse a ``var_ref(id=.., type=.., precision=..)`` call -- shared by
    the ``var`` condition, the ``var`` action's ``var=`` mode, and
    ``repeat``'s ``while``. ``precision=`` is required on every call, even
    when a given use won't end up needing it (e.g. comparing to another
    variable) -- same reasoning as ``param()`` always requiring ``uom=``/
    ``precision=``: one consistent, explicit signature, sourced from
    list_variables, so the compiler never has to guess or consult live
    state.

    Returns ``(var_id, var_type_str, precision)`` where ``var_type_str`` is
    ``"1"`` (integer variable) or ``"2"`` (state variable), matching the
    schema's ``VarType`` string-enum encoding (not the plain int the DSL
    accepts)."""
    if not (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == "var_ref"):
        raise TriggerCompileError("expected var_ref(id=.., type=.., precision=..)")
    _, kwargs = call_args(expr)
    if "id" not in kwargs or "type" not in kwargs or "precision" not in kwargs:
        raise TriggerCompileError(
            "var_ref(...) requires id=, type= (1=integer variable, 2=state variable), and precision= "
            "(that variable's own precision, from list_variables)."
        )
    var_id = literal(kwargs["id"])
    if isinstance(var_id, bool) or not isinstance(var_id, int):
        raise TriggerCompileError("var_ref(...)'s id= must be an integer.")
    var_type = literal(kwargs["type"])
    if var_type not in (1, 2):
        raise TriggerCompileError("var_ref(...)'s type= must be 1 (integer variable) or 2 (state variable).")
    precision = literal(kwargs["precision"])
    if isinstance(precision, bool) or not isinstance(precision, int) or precision < 0:
        raise TriggerCompileError("var_ref(...)'s precision= must be a non-negative integer.")
    return var_id, str(var_type), precision


# ---------------------------------------------------------------------------
# Dispatch tables -- populated by each condition/action family module via
# `register_condition_leaf`/`register_action`. Kept here, not per-family, so
# there's a single place to see what's implemented vs. still a stub.
# ---------------------------------------------------------------------------

# condition-leaf dispatch key -> compiler fn. Keys: "compare" (any ast.Compare
# reaches this, handled specially -- see _compile_condition_leaf), or a bare
# function name for ast.Call-shaped conditions (device(...).was_controlled is
# matched separately via match_device_call, not through this table).
DeviceMethodCompiler = Callable[[str, list[ast.expr], dict[str, ast.expr]], dict[str, Any]]

_CONDITION_CALL_COMPILERS: dict[str, Callable[[ast.Call], dict[str, Any]]] = {}
_CONDITION_DEVICE_METHOD_COMPILERS: dict[str, DeviceMethodCompiler] = {}

# ast.Compare can mean either a `status` condition (left = device(...).status(...))
# or a `var` condition (left = var_ref(...)) -- each registered compiler
# inspects the left-hand shape and returns None (not a match, let the next
# one try) or a compiled dict (a match, possibly raising TriggerCompileError
# for a shape-specific problem once matched).
_COMPARE_COMPILERS: list[Callable[[ast.Compare], dict[str, Any] | None]] = []

# action dispatch: bare function name (wait, notify, ...) -> compiler fn, or
# device-method name (command, ...) -> compiler fn, same split as conditions.
_ACTION_CALL_COMPILERS: dict[str, Callable[[ast.Call], dict[str, Any]]] = {}
_ACTION_DEVICE_METHOD_COMPILERS: dict[str, DeviceMethodCompiler] = {}


def register_condition_call(name: str, fn: Callable[[ast.Call], dict[str, Any]]) -> None:
    _CONDITION_CALL_COMPILERS[name] = fn


def register_condition_device_method(name: str, fn: DeviceMethodCompiler) -> None:
    _CONDITION_DEVICE_METHOD_COMPILERS[name] = fn


def register_compare_compiler(fn: Callable[[ast.Compare], dict[str, Any] | None]) -> None:
    _COMPARE_COMPILERS.append(fn)


def register_action_call(name: str, fn: Callable[[ast.Call], dict[str, Any]]) -> None:
    _ACTION_CALL_COMPILERS[name] = fn


def register_action_device_method(name: str, fn: DeviceMethodCompiler) -> None:
    _ACTION_DEVICE_METHOD_COMPILERS[name] = fn


# ---------------------------------------------------------------------------
# Boolean expression walk (conditions) -- generalizes v1's flat `if` line
# into the new schema's flat andOr-tagged list + `paren` nesting.
# ---------------------------------------------------------------------------


def _compile_condition_leaf(expr: ast.expr) -> dict[str, Any] | None:
    # Hard rule: there is exactly ONE comment in a routine -- the routine's
    # own `comment` field, supplied separately, never authored inline in the
    # DSL. A bare string here is silently dropped rather than compiled into
    # a node or rejected -- the model may write one out of habit (mirroring
    # a natural "note to self"), and stripping it is friendlier than making
    # an otherwise-valid routine fail to compile over it.
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return None

    if isinstance(expr, ast.Compare):
        for compiler in _COMPARE_COMPILERS:
            result = compiler(expr)
            if result is not None:
                return result
        raise TriggerCompileError(f"unrecognized comparison: {ast.unparse(expr)}")

    matched = match_device_call(expr)
    if matched is not None:
        device_id, method, args, kwargs = matched
        compiler = _CONDITION_DEVICE_METHOD_COMPILERS.get(method)
        if compiler is None:
            raise TriggerCompileError(f"'{method}' is not a valid device(...) condition -- did you mean was_controlled?")
        return compiler(device_id, args, kwargs)

    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name):
        compiler = _CONDITION_CALL_COMPILERS.get(expr.func.id)
        if compiler is not None:
            return compiler(expr)

    raise TriggerCompileError(f"unrecognized condition: {ast.unparse(expr)}")


def _compile_bool_expr(expr: ast.expr) -> list[dict[str, Any]]:
    if not isinstance(expr, ast.BoolOp):
        leaf = _compile_condition_leaf(expr)
        if leaf is None:
            return []
        leaf.setdefault("andOr", "and")
        return [leaf]

    op = "and" if isinstance(expr.op, ast.And) else "or"
    result: list[dict[str, Any]] = []
    for value in expr.values:
        if isinstance(value, ast.BoolOp) and type(value.op) is not type(expr.op):
            nested = _compile_bool_expr(value)
            if nested:
                result.append({"type": "paren", "andOr": op, "conditions": nested})
        else:
            leaf = _compile_condition_leaf(value)
            if leaf is None:
                continue
            leaf["andOr"] = op
            result.append(leaf)
    return result


# ---------------------------------------------------------------------------
# Action list walk.
# ---------------------------------------------------------------------------


def _compile_action_stmt(stmt: ast.stmt) -> dict[str, Any] | None:
    if isinstance(stmt, ast.Pass):
        return None

    # Same hard rule as conditions (see _compile_condition_leaf): the only
    # comment a routine has is its own `comment` field. A bare string action
    # statement is dropped, not compiled into a node.
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
        return None

    if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
        raise TriggerCompileError(f"unrecognized action statement: {ast.unparse(stmt)}")

    expr = stmt.value
    matched = match_device_call(expr)
    if matched is not None:
        device_id, method, args, kwargs = matched
        compiler = _ACTION_DEVICE_METHOD_COMPILERS.get(method)
        if compiler is None:
            raise TriggerCompileError(f"'{method}' is not a valid device(...) action -- did you mean command?")
        return compiler(device_id, args, kwargs)

    if isinstance(expr.func, ast.Name):
        compiler = _ACTION_CALL_COMPILERS.get(expr.func.id)
        if compiler is not None:
            return compiler(expr)

    raise TriggerCompileError(f"unrecognized action: {ast.unparse(expr)}")


def compile_actions(stmts: list[ast.stmt]) -> list[dict[str, Any]]:
    """Compile a flat list of action statements. ``repeat(...)``/``every(...)``/
    ``while_repeat(...)`` are ordinary statements in this list, not a
    wrapping block -- per the schema, a Repeat action has no body field at
    all; everything textually after it in the SAME list is what repeats, per
    the real hub's semantics. No Python `for`-loop syntax is involved.

    A real, observed model mistake: writing the marker AFTER the actions it
    was meant to repeat (natural-language phrasing like "turn on, wait, turn
    off, repeat 5 times" narrates the repeat instruction last, but the DSL
    requires the opposite order) -- since a marker with nothing after it
    repeats zero actions by definition, this is never valid, so it's
    rejected here rather than left to silently produce a no-op repeat.
    """
    actions: list[dict[str, Any]] = []
    for stmt in stmts:
        compiled = _compile_action_stmt(stmt)
        if compiled is not None:
            actions.append(compiled)

    if actions and actions[-1].get("type") == "repeat":
        raise TriggerCompileError(
            "repeat(...)/every(...)/while_repeat(...) must come BEFORE the actions it repeats, not after -- "
            "it's the last action in this then/else block, so it has nothing to repeat. Move it to "
            "immediately before the actions you want repeated, e.g. `repeat(count=5)` then the actions, "
            "not the actions then `repeat(count=5)`."
        )

    return actions


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------


def compile_trigger_source(
    *,
    name: str,
    trigger_id: int | None,
    comment: str | None,
    source: str,
    parent: int | None = None,
) -> dict[str, Any]:
    """Compile one routine's DSL *source* into a ``NewTrigger``/``UpdatedTrigger``-
    shaped dict.

    *parent* is routine-placement metadata, never expressed in the DSL
    itself -- confirmed (not assumed) that the real update endpoint requires
    it to be the routine's existing parent folder id, or the update fails/
    misbehaves. The caller (``create_or_update_routine``) is responsible for
    fetching the routine's current ``parent`` before calling this for an
    update -- never omitted or guessed here.

    Raises:
        TriggerCompileError: On any syntax/shape the DSL does not recognize.
            The message is written to be fed back to the model verbatim.
    """
    if not isinstance(source, str) or not source.strip():
        raise TriggerCompileError("Routine 'code' is empty.")

    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError as exc:
        raise TriggerCompileError(f"Python syntax error: {exc}") from exc

    body = tree.body
    if not body:
        raise TriggerCompileError("Routine 'code' has no statements.")

    if len(body) == 1 and isinstance(body[0], ast.If):
        if_node = body[0]
        if len(if_node.orelse) == 1 and isinstance(if_node.orelse[0], ast.If):
            raise TriggerCompileError("`elif` is not supported. Use a single `if`/`else` only.")
        condition = _compile_bool_expr(if_node.test)
        then_actions = compile_actions(if_node.body)
        else_actions = compile_actions(if_node.orelse) if if_node.orelse else []
    else:
        if any(isinstance(stmt, ast.If) for stmt in body):
            raise TriggerCompileError(
                "Routine source must be either exactly one top-level `if <condition>: ... else: ...` "
                "statement, or, for a routine with no condition/trigger, only action statements and no `if` at all."
            )
        condition = []
        then_actions = compile_actions(body)
        else_actions = []

    out: dict[str, Any] = {"name": name}
    if trigger_id is not None:
        out["id"] = trigger_id
    if parent is not None:
        out["parent"] = parent
    if comment:
        out["comment"] = comment
    out["if"] = condition
    out["then"] = then_actions
    out["else"] = else_actions
    return out
