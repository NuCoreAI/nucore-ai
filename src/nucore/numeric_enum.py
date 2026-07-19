"""Detects editors whose enumerated value list is actually a disguised
numeric range, composite pair, or identity index -- so the compact DEVICE
DATABASE can describe them as a couple of numbers instead of inlining the
full label list, and so ``send_command`` can resolve a value back to the
real raw index.

Editor ids are hardcoded, not pattern-detected from label text: an editor
id has a fixed meaning within an install, but two unrelated editors could
coincidentally have similarly-shaped label text -- sniffing text patterns
risks misclassifying a real category enum as numeric. Detection and
resolution always re-parse the *live* editor passed in, never a cached
prompt-time table, and composite resolution is always a direct reverse
lookup built from the editor's own real label data -- never an assumed
packing formula (e.g. "on * 16 + off"), since that could be wrong for a
device this code has never seen.

Known editors:
- ``I_NUM_255``: the raw index literally IS the value (0-255); the enum
  labels are a decorative bitmask/hex breakdown of the same number.
- ``I_RR``: ramp rate -- labels are time values ("9.0 minutes", "0.1
  seconds") in a non-linear (non-uniformly-spaced) index order.
- ``I_BL_KP``: keypad backlight -- labels are "On <n> / Off <m>" pairs, a
  full cross product of two independent sub-levels packed into one index.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .value_resolution import _SECONDS_PER_UNIT_BY_ALIAS, _normalize

_IDENTITY = "identity"  # raw index IS the value
_TABLE = "table"  # value -> nearest table entry (non-linear)
_COMPOSITE_ON_OFF = "composite_on_off"  # two named sub-values -> reverse lookup

NUMERIC_ENUM_EDITOR_IDS: dict[str, str] = {
    "I_NUM_255": _IDENTITY,
    "I_RR": _TABLE,
    "I_BL_KP": _COMPOSITE_ON_OFF,
}

_ON_OFF_RE = re.compile(r"^on\s+(\d+)\s*/\s*off\s+(\d+)$")
_TIME_RE = re.compile(r"^([\d.]+)\s*(second|minute|hour)s?$")
_LABEL_SECONDS_PER_UNIT = {"second": 1.0, "minute": 60.0, "hour": 3600.0}


def _editor_names(editor) -> dict[str, str]:
    """Flatten every range's ``{value: label}`` pairs into one dict."""
    names: dict[str, str] = {}
    for r in editor.ranges:
        r_names = getattr(r, "names", None)
        if r_names:
            names.update(r_names)
    return names


def _parse_on_off(names: dict[str, str]) -> dict[int, tuple[int, int]] | None:
    pairs: dict[int, tuple[int, int]] = {}
    for key, label in names.items():
        m = _ON_OFF_RE.match(_normalize(label))
        if not m:
            return None
        pairs[int(key)] = (int(m.group(1)), int(m.group(2)))
    return pairs


def _parse_seconds_table(names: dict[str, str]) -> list[tuple[float, int]] | None:
    table: list[tuple[float, int]] = []
    for key, label in names.items():
        m = _TIME_RE.match(_normalize(label))
        if not m:
            return None
        seconds = float(m.group(1)) * _LABEL_SECONDS_PER_UNIT[m.group(2)]
        table.append((seconds, int(key)))
    return table


@dataclass
class NumericEnumSpec:
    kind: str
    # 'identity'/'table': (min, max) -- the range the model should treat as valid.
    # 'composite_on_off': {'on': (min, max), 'off': (min, max)}.
    descriptor: Any


def describe_numeric_enum(editor) -> "NumericEnumSpec | None":
    """Return a compact descriptor for *editor* if its id is a known
    disguised-numeric editor and its live label data actually matches the
    expected shape, else ``None`` -- the caller falls back to rendering
    the plain enum label list."""
    kind = NUMERIC_ENUM_EDITOR_IDS.get(getattr(editor, "id", None))
    if kind is None:
        return None
    names = _editor_names(editor)
    if not names:
        return None

    if kind == _IDENTITY:
        try:
            indices = [int(k) for k in names]
        except ValueError:
            return None
        return NumericEnumSpec(_IDENTITY, (min(indices), max(indices)))

    if kind == _TABLE:
        table = _parse_seconds_table(names)
        if not table:
            return None
        seconds_values = [s for s, _ in table]
        return NumericEnumSpec(_TABLE, (min(seconds_values), max(seconds_values)))

    if kind == _COMPOSITE_ON_OFF:
        pairs = _parse_on_off(names)
        if not pairs:
            return None
        on_values = [p[0] for p in pairs.values()]
        off_values = [p[1] for p in pairs.values()]
        return NumericEnumSpec(
            _COMPOSITE_ON_OFF,
            {"on": (min(on_values), max(on_values)), "off": (min(off_values), max(off_values))},
        )

    return None


def resolve_numeric_enum(editor, value: Any, *, unit: str | None = None) -> int:
    """Resolve *value* against the *live* editor's real label data to the
    raw wire index.

    *value* is a plain number for the identity/table kinds (table also
    accepts *unit*, e.g. "minutes"), or an ``{"on": .., "off": ..}`` dict
    for the composite kind.

    Raises:
        ValueError: with a message safe to relay to the model.
    """
    kind = NUMERIC_ENUM_EDITOR_IDS.get(getattr(editor, "id", None))
    if kind is None:
        raise ValueError(f"editor '{getattr(editor, 'id', None)}' is not a numeric-enum editor")
    names = _editor_names(editor)

    if kind == _IDENTITY:
        try:
            index = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"expected a plain number, got {value!r}")
        indices = [int(k) for k in names]
        lo, hi = min(indices), max(indices)
        if not (lo <= index <= hi):
            raise ValueError(f"value must be between {lo} and {hi}")
        return index

    if kind == _TABLE:
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"expected a plain number, got {value!r}")
        if unit:
            per_unit = _SECONDS_PER_UNIT_BY_ALIAS.get(_normalize(unit))
            if per_unit is None:
                raise ValueError(f"unrecognized unit '{unit}'; please give the value in seconds, minutes, or hours")
            seconds *= per_unit
        table = _parse_seconds_table(names)
        if not table:
            raise ValueError(f"editor '{editor.id}' no longer matches the expected time-table shape")
        nearest = min(table, key=lambda pair: abs(pair[0] - seconds))
        return nearest[1]

    if kind == _COMPOSITE_ON_OFF:
        if not isinstance(value, dict) or "on" not in value or "off" not in value:
            raise ValueError('this command needs both an "on" and an "off" level, e.g. {"on": 8, "off": 3}')
        pairs = _parse_on_off(names)
        if not pairs:
            raise ValueError(f"editor '{editor.id}' no longer matches the expected on/off shape")
        try:
            target = (int(value["on"]), int(value["off"]))
        except (TypeError, ValueError):
            raise ValueError("'on' and 'off' must both be plain numbers")
        for index, pair in pairs.items():
            if pair == target:
                return index
        on_values = [p[0] for p in pairs.values()]
        off_values = [p[1] for p in pairs.values()]
        raise ValueError(
            f"no matching level for on={target[0]}, off={target[1]}; "
            f"'on' must be between {min(on_values)} and {max(on_values)}, "
            f"'off' between {min(off_values)} and {max(off_values)}"
        )

    raise ValueError(f"unhandled numeric-enum kind '{kind}'")
