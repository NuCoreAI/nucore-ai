"""``list_preferences``/``preference_op`` -- customer preferences (aliases +
events, see design/user-pref.md). Shaped like ``variable_op``/
``list_variables`` -- plain immediate CRUD, no session -- since preference
edits are cheap and trivially reversible, unlike Plan/Diagnostics.

Preferences are unavailable (a clear error, not a crash) for an installation
that hasn't configured a ``preferences_dir`` -- see
``unified.preferences.preference_store.get_store``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from nucore import NuCoreInterface

from ..preferences.preference_store import get_store, next_occurrence_info

_NOT_CONFIGURED = {
    "error": (
        "preferences are not configured for this installation -- set a preferences_dir "
        "(via --preferences-dir or runtime config's 'preferences_dir') to enable this feature"
    )
}


async def list_preferences(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    pref_type = args.get("type")
    if pref_type is not None and pref_type not in ("alias", "event"):
        return {"error": "type must be 'alias' or 'event' if given"}

    store = get_store(nucore_interface)
    if store is None:
        return _NOT_CONFIGURED

    records = store.list(pref_type)
    today = date.today()
    annotated = []
    for record in records:
        if record.get("type") == "event":
            record = {**record, **next_occurrence_info(record, today)}
        annotated.append(record)
    return {"preferences": annotated}


def _validate_alias_fields(args: dict[str, Any]) -> str | None:
    if not args.get("alias") or not isinstance(args["alias"], str):
        return "type == 'alias' requires a non-empty 'alias' string"
    if not args.get("target") or not isinstance(args["target"], str):
        return "type == 'alias' requires a non-empty 'target' string"
    return None


def _validate_event_fields(args: dict[str, Any]) -> str | None:
    if not args.get("name") or not isinstance(args["name"], str):
        return "type == 'event' requires a non-empty 'name' string"

    recurrence = args.get("recurrence")
    if recurrence not in ("annual", "once"):
        return "type == 'event' requires recurrence to be 'annual' or 'once'"

    if recurrence == "annual":
        month, day = args.get("month"), args.get("day")
        if not isinstance(month, int) or not isinstance(day, int):
            return "recurrence == 'annual' requires integer 'month' and 'day'"
        try:
            date(2000, month, day)  # 2000 is a leap year -- validates Feb 29 too
        except ValueError as exc:
            return f"invalid month/day: {exc}"
    else:  # "once"
        raw_date = args.get("date")
        if not isinstance(raw_date, str):
            return "recurrence == 'once' requires a 'date' string (YYYY-MM-DD)"
        try:
            datetime.strptime(raw_date, "%Y-%m-%d")
        except ValueError as exc:
            return f"invalid date: {exc}"

    remind_days_before = args.get("remind_days_before")
    if remind_days_before is not None and (
        not isinstance(remind_days_before, int) or isinstance(remind_days_before, bool) or remind_days_before < 0
    ):
        return "remind_days_before must be a non-negative integer if given"

    return None


async def preference_op(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    operation = args.get("operation")
    if operation not in ("create", "delete"):
        return {"error": "operation is required and must be one of: create, delete"}

    store = get_store(nucore_interface)
    if store is None:
        return _NOT_CONFIGURED

    if operation == "delete":
        pref_id = args.get("id")
        if not pref_id:
            return {"error": "id is required for 'delete'"}
        if not store.remove(pref_id):
            return {"error": f"no preference found with id '{pref_id}'"}
        return {"id": pref_id, "status": "deleted"}

    # create
    pref_type = args.get("type")
    if pref_type not in ("alias", "event"):
        return {"error": "type is required and must be 'alias' or 'event'"}

    if pref_type == "alias":
        error = _validate_alias_fields(args)
        if error:
            return {"error": error}
        existing = [r for r in store.list("alias") if r.get("alias", "").lower() == args["alias"].lower()]
        if existing:
            return {
                "error": (
                    f"an alias for '{args['alias']}' already exists (id '{existing[0]['id']}') -- "
                    "delete it first if you want to change what it resolves to"
                )
            }
        record = store.add("alias", alias=args["alias"], target=args["target"])
        return record

    # event
    error = _validate_event_fields(args)
    if error:
        return {"error": error}

    fields = {"name": args["name"], "recurrence": args["recurrence"]}
    if args["recurrence"] == "annual":
        fields["month"] = args["month"]
        fields["day"] = args["day"]
    else:
        fields["date"] = args["date"]
    if args.get("remind_days_before") is not None:
        fields["remind_days_before"] = args["remind_days_before"]

    record = store.add("event", **fields)
    return {**record, **next_occurrence_info(record)}
