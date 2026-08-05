"""``PreferenceStore`` -- flat-JSON-backed CRUD for customer preferences
(aliases + events). See design/user-pref.md for the full rationale: tiny
volume, no query SQLite would meaningfully speed up, and a plain file
matches the rest of this codebase's convention of representing everything
the LLM sees as plain dict/list literals.

Not hub-native (no ``/rest/...`` endpoint backs this the way devices/routines
do), so this lives entirely in ``unified/`` and never touches
``NuCoreInterface``/``IoXWrapper``.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

_FILENAME = "preferences.json"


class PreferenceStore:
    """One JSON file holding a flat list of preference records, each with a
    stable ``id`` and a ``type`` (``"alias"`` or ``"event"``) discriminating
    which other fields are populated. Loaded lazily on first access; never
    raises on a missing/corrupt file (starts fresh instead) since this is
    read on every turn's prompt build and a bad file shouldn't break that.

    There is deliberately no default path -- the caller (see ``get_store``
    below) must supply one, sourced from ``--preferences-dir``/runtime
    config's ``preferences_dir``. Preferences are simply unavailable for an
    installation that hasn't configured either.
    """

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._records: list[dict[str, Any]] | None = None

    def _ensure_loaded(self) -> None:
        if self._records is not None:
            return
        if not self._path.exists():
            self._records = []
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._records = data if isinstance(data, list) else []
        except Exception:
            self._records = []

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._records, indent=2), encoding="utf-8")
        tmp.replace(self._path)  # atomic on POSIX -- avoids a half-written file on crash

    def _next_id(self) -> str:
        existing = [
            int(r["id"][1:])
            for r in self._records
            if isinstance(r.get("id"), str) and r["id"][:1] == "p" and r["id"][1:].isdigit()
        ]
        return f"p{max(existing, default=0) + 1}"

    def list(self, type: str | None = None) -> list[dict[str, Any]]:
        self._ensure_loaded()
        if type is None:
            return list(self._records)
        return [r for r in self._records if r.get("type") == type]

    def add(self, type: str, **fields: Any) -> dict[str, Any]:
        self._ensure_loaded()
        record = {"id": self._next_id(), "type": type, **fields}
        self._records.append(record)
        self._save()
        return record

    def remove(self, pref_id: str) -> bool:
        self._ensure_loaded()
        before = len(self._records)
        self._records = [r for r in self._records if r.get("id") != pref_id]
        removed = len(self._records) != before
        if removed:
            self._save()
        return removed


def get_store(nucore_interface: Any) -> PreferenceStore | None:
    """Lazily attach one ``PreferenceStore`` per ``nucore_interface``
    instance -- same lazy-getattr/setattr pattern as Plan's ``_get_engine``,
    but public (not underscore-prefixed) since both ``prompt_builder.py`` and
    ``handlers/preferences.py`` need to call it, unlike ``_get_engine``,
    which only ever had one caller.

    Returns ``None`` -- rather than falling back to some default location --
    when this installation hasn't configured a ``preferences_dir`` (via
    ``--preferences-dir`` or runtime config), which callers must handle by
    treating preferences as unavailable, not by inventing a location of
    their own.
    """
    store = getattr(nucore_interface, "_preference_store", None)
    if store is not None:
        return store

    preferences_dir = getattr(nucore_interface, "preferences_dir", None)
    if not preferences_dir:
        return None

    store = PreferenceStore(Path(preferences_dir) / _FILENAME)
    nucore_interface._preference_store = store
    return store


def _annual_occurrence(month: int, day: int, today: date) -> date:
    try:
        candidate = date(today.year, month, day)
    except ValueError:
        candidate = date(today.year, 2, 28)  # Feb 29 on a non-leap year
    if candidate < today:
        try:
            candidate = date(today.year + 1, month, day)
        except ValueError:
            candidate = date(today.year + 1, 2, 28)
    return candidate


def next_occurrence_info(record: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    """For a ``type == "event"`` record, compute ``next_occurrence``
    (ISO date), ``days_until`` (signed int), and -- when ``remind_days_before``
    is set -- ``due_soon`` (bool). Pure/derived, computed fresh on every call
    rather than stored, since "today" changes."""
    today = today or date.today()

    if record.get("recurrence") == "annual":
        occurrence = _annual_occurrence(int(record["month"]), int(record["day"]), today)
    else:  # "once"
        occurrence = datetime.strptime(record["date"], "%Y-%m-%d").date()

    days_until = (occurrence - today).days
    info: dict[str, Any] = {"next_occurrence": occurrence.isoformat(), "days_until": days_until}

    remind_days_before = record.get("remind_days_before")
    if remind_days_before is not None:
        info["due_soon"] = 0 <= days_until <= remind_days_before

    return info
