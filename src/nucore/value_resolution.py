"""Deterministic value resolution for ``send_command``.

Given a customer's already-parsed ``(value, unit)`` pair -- or an
already-matched enum label string -- and the target parameter/property's
real ``Editor``, resolves it to a final wire value: unit conversion,
precision rounding, min/max range validation, and enum-label -> key lookup.

Deliberately backend-only and deterministic: this module never parses
free-form natural language (a customer saying "a billion dollars" or "kinda
warm") -- that stays with the model, which is what natural-language
understanding actually requires. This module only ever receives numbers and
short unit/label tokens the model already resolved from the sentence, and
does the arithmetic/lookup that's exact and closed-world -- exactly where a
model is unreliable (arithmetic slips, invented conversion factors,
hallucinated enum keys) and deterministic code is not.

Unit conversion is a small, explicit, curated starter set (Fahrenheit<->
Celsius, seconds/minutes/hours) -- not a general unit-algebra system. Add
entries as real customer phrasing surfaces a genuine gap; never fall back to
guessing an unrecognized unit.
"""

from __future__ import annotations

from dataclasses import dataclass

from .editor import Editor, EditorMinMaxRange


class ValueResolutionError(Exception):
    """A value/unit/label could not be resolved against an editor.

    The message is written to be relayed to the model/customer verbatim --
    it explains what's needed rather than guessing.
    """


@dataclass
class ResolvedValue:
    value: float | int | str
    uom: int
    precision: int


_FAHRENHEIT_UOM_IDS = {"17"}
_CELSIUS_UOM_IDS = {"4"}
_FAHRENHEIT_ALIASES = {"f", "°f", "fahrenheit", "degf", "deg f", "degrees f", "degrees fahrenheit"}
_CELSIUS_ALIASES = {"c", "°c", "celsius", "degc", "deg c", "degrees c", "degrees celsius"}

# Seconds-per-unit for the handful of time_duration uoms this starter set
# supports converting between (both the "clock" and "duration" flavors of
# each unit, e.g. uom 44 "Minute" and uom 45 "Minutes", are treated
# identically here since they mean the same thing for value resolution).
_SECONDS_PER_UNIT_BY_UOM_ID = {
    "58": 1, "57": 1,  # seconds
    "45": 60, "44": 60,  # minutes
    "20": 3600,  # hours
}
_SECONDS_PER_UNIT_BY_ALIAS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
}


def _normalize(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _match_enum_label(editor: Editor, label: str):
    """Exact (normalized) match of *label* against every range's enum
    labels. Returns (key, range) on a single unambiguous match, else None."""
    target = _normalize(label)
    matches = []
    for r in editor.ranges:
        names = getattr(r, "names", None)
        if not names:
            continue
        for key, value_label in names.items():
            if value_label and _normalize(str(value_label)) == target:
                matches.append((key, r))
    if len(matches) == 1:
        return matches[0]
    return None


def _convert_numeric(value: float, unit: str | None, target: EditorMinMaxRange) -> float:
    """Convert *value* from the customer's *unit* into *target*'s own uom.

    Raises ValueResolutionError if *unit* is unrecognized/unconvertible.
    """
    target_uom_id = target.uom.id
    target_label = _normalize(target.uom.label or "")
    target_name = _normalize(target.uom.name or "")
    unit_norm = _normalize(unit) if unit else None

    if unit_norm is None or unit_norm in (target_label, target_name):
        return value  # no unit stated, or already matches -- passthrough

    if target.uom.category_id == "temperature":
        if target_uom_id in _FAHRENHEIT_UOM_IDS and unit_norm in _CELSIUS_ALIASES:
            return value * 9 / 5 + 32
        if target_uom_id in _CELSIUS_UOM_IDS and unit_norm in _FAHRENHEIT_ALIASES:
            return (value - 32) * 5 / 9
        if target_uom_id in _FAHRENHEIT_UOM_IDS and unit_norm in _FAHRENHEIT_ALIASES:
            return value
        if target_uom_id in _CELSIUS_UOM_IDS and unit_norm in _CELSIUS_ALIASES:
            return value
        raise ValueResolutionError(
            f"cannot convert unit '{unit}' to this value's unit ({target.uom.label}); "
            "please provide the value in Fahrenheit or Celsius, or clarify."
        )

    if target.uom.category_id == "time_duration":
        target_seconds_per_unit = _SECONDS_PER_UNIT_BY_UOM_ID.get(target_uom_id)
        unit_seconds_per_unit = _SECONDS_PER_UNIT_BY_ALIAS.get(unit_norm)
        if target_seconds_per_unit and unit_seconds_per_unit:
            return value * unit_seconds_per_unit / target_seconds_per_unit
        raise ValueResolutionError(
            f"cannot convert unit '{unit}' to this value's unit ({target.uom.label}); "
            "please provide the value in seconds, minutes, or hours, or clarify."
        )

    raise ValueResolutionError(
        f"cannot convert unit '{unit}' to this value's unit ({target.uom.label}); "
        "please provide the value directly in that unit."
    )


def resolve_value(editor: Editor, *, value, unit: str | None = None) -> ResolvedValue:
    """Resolve a customer-parsed ``(value, unit)`` pair -- or an
    already-matched enum label passed as *value* with *unit* omitted --
    against *editor*.

    *value* is either already a number (or a numeric string) or the exact
    enum label text the model already read out of the command/property's
    own editor description -- never free-form customer sentences.

    Raises:
        ValueResolutionError: When the value/unit/label can't be resolved.
    """
    if editor is None or not editor.ranges:
        raise ValueResolutionError("this command/property has no defined value editor to validate against")

    if isinstance(value, str):
        matched = _match_enum_label(editor, value)
        if matched is not None:
            key, r = matched
            return ResolvedValue(value=key, uom=int(r.uom.id), precision=0)
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ValueResolutionError(
                f"'{value}' does not match any known option for this command/property; please clarify."
            )

    numeric_range = next((r for r in editor.ranges if isinstance(r, EditorMinMaxRange)), None)
    if numeric_range is None:
        raise ValueResolutionError(
            f"'{value}' is a number but this command/property only accepts one of its defined options; "
            "please clarify."
        )

    converted = _convert_numeric(float(value), unit, numeric_range)

    precision = numeric_range.prec if numeric_range.prec is not None else 0
    converted = round(converted, precision)
    if precision == 0:
        converted = int(converted)

    if numeric_range.min is not None and converted < numeric_range.min:
        raise ValueResolutionError(
            f"{converted} is below the minimum allowed value ({numeric_range.min}); please clarify."
        )
    if numeric_range.max is not None and converted > numeric_range.max:
        raise ValueResolutionError(
            f"{converted} is above the maximum allowed value ({numeric_range.max}); please clarify."
        )

    return ResolvedValue(value=converted, uom=int(numeric_range.uom.id), precision=precision)
