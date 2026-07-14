#!/usr/bin/env python3
"""
Renders the condensed routines list (id/name/comment/device_names/invalid/
invalid_reason dicts) as compact Python literals instead of JSON.

Deduplicates invalid_reason strings into a shared REASONS lookup table --
the same $ref-style pattern DedupeProfiles uses for device property enums --
since a handful of distinct reasons (e.g. "If: Unsupported schedule sunset
on specific dates") repeat across many invalid routines.
"""

from __future__ import annotations

from typing import Any


class DedupeRoutines:
    PYTHON_LEGEND = (
        "# Routines summary as Python literals (dict/list/tuple only --\n"
        "# parseable with ast.literal_eval). Same information as the JSON\n"
        "# condensed-routines list, without repeating field names per row.\n"
        "#\n"
        "# REASONS: id -> reason text. A handful of distinct \"why this routine\n"
        "#   can't run as authored\" explanations, shared across many routines.\n"
        "#\n"
        "# ROUTINES: list of (id, name, comment, device_names, invalid, invalid_reason)\n"
        "#   tuples, one per routine.\n"
        "#     id: routine id (int).\n"
        "#     name: routine name.\n"
        "#     comment: user-authored comment, '' if none.\n"
        "#     device_names: list of device names referenced by the routine's\n"
        "#       conditions/actions.\n"
        "#     invalid: True if this routine cannot currently run as authored.\n"
        "#     invalid_reason: None if valid, else a REASONS id -- substitute\n"
        "#       REASONS[invalid_reason] for the actual explanation text.\n"
    )

    @staticmethod
    def render_python(routines: list[dict[str, Any]]) -> str:
        """Render a condensed-routines list as Python literals, with
        ``invalid_reason`` deduplicated into a shared ``REASONS`` table."""
        reasons: dict[str, str] = {}
        for routine in routines:
            reason = routine.get("invalid_reason")
            if reason and reason not in reasons:
                reasons[reason] = f"r{len(reasons) + 1}"

        lines: list[str] = [DedupeRoutines.PYTHON_LEGEND]

        if reasons:
            lines.append("\nREASONS = {")
            for reason, rid in reasons.items():
                lines.append(f"  {rid!r}: {reason!r},")
            lines.append("}")

        lines.append("\nROUTINES = [")
        for routine in routines:
            reason = routine.get("invalid_reason")
            reason_ref = reasons.get(reason) if reason else None
            comment = routine.get("comment") or ""
            device_names = routine.get("device_names") or []
            lines.append(
                f"  ({routine.get('id')!r}, {routine.get('name')!r}, "
                f"{comment!r}, {device_names!r}, "
                f"{routine.get('invalid', False)!r}, {reason_ref!r}),"
            )
        lines.append("]")

        return "\n".join(lines)
