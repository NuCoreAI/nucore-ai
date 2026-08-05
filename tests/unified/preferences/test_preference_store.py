"""PreferenceStore: flat-JSON CRUD (add/list/remove), persistence across
instances, corrupt-file resilience, and next_occurrence_info's date math for
both recurrence types. Also covers get_store's "not configured" contract --
there's deliberately no default path (see design/user-pref.md).
"""

from __future__ import annotations

from datetime import date

from unified.preferences.preference_store import PreferenceStore, get_store, next_occurrence_info


def test_add_and_list_roundtrip(tmp_path):
    store = PreferenceStore(tmp_path / "preferences.json")

    record = store.add("alias", alias="mbr", target="Master Bedroom Scene")

    assert record["id"] == "p1"
    assert store.list() == [record]
    assert store.list("alias") == [record]
    assert store.list("event") == []


def test_remove_by_id(tmp_path):
    store = PreferenceStore(tmp_path / "preferences.json")
    record = store.add("alias", alias="mbr", target="Master Bedroom Scene")

    assert store.remove(record["id"]) is True
    assert store.list() == []
    assert store.remove(record["id"]) is False  # already gone


def test_next_id_increments_and_is_stable_after_removal(tmp_path):
    store = PreferenceStore(tmp_path / "preferences.json")
    p1 = store.add("alias", alias="a", target="A")
    p2 = store.add("alias", alias="b", target="B")

    assert (p1["id"], p2["id"]) == ("p1", "p2")

    store.remove(p1["id"])
    p3 = store.add("alias", alias="c", target="C")

    assert p3["id"] == "p3"  # doesn't reuse p1's freed id


def test_persists_across_a_second_store_instance(tmp_path):
    path = tmp_path / "preferences.json"
    store_a = PreferenceStore(path)
    store_a.add("alias", alias="mbr", target="Master Bedroom Scene")

    store_b = PreferenceStore(path)  # fresh instance, same file

    assert store_b.list("alias")[0]["alias"] == "mbr"


def test_missing_file_starts_empty_without_raising(tmp_path):
    store = PreferenceStore(tmp_path / "does-not-exist.json")

    assert store.list() == []


def test_corrupt_file_does_not_crash(tmp_path):
    path = tmp_path / "preferences.json"
    path.write_text("not json at all {{{", encoding="utf-8")
    store = PreferenceStore(path)

    assert store.list() == []


def test_get_store_returns_none_when_preferences_dir_not_set():
    class Bare:
        pass

    assert get_store(Bare()) is None


def test_get_store_constructs_from_preferences_dir_attribute(tmp_path):
    class Bare:
        pass

    backend = Bare()
    backend.preferences_dir = str(tmp_path)

    store = get_store(backend)
    store.add("alias", alias="mbr", target="Master Bedroom Scene")

    assert (tmp_path / "preferences.json").exists()
    assert get_store(backend) is store  # same instance reused, not reconstructed


def test_next_occurrence_info_annual_this_year_if_still_upcoming():
    record = {"type": "event", "recurrence": "annual", "month": 6, "day": 15}
    today = date(2026, 6, 1)

    info = next_occurrence_info(record, today)

    assert info["next_occurrence"] == "2026-06-15"
    assert info["days_until"] == 14


def test_next_occurrence_info_annual_rolls_to_next_year_if_passed():
    record = {"type": "event", "recurrence": "annual", "month": 6, "day": 15}
    today = date(2026, 7, 1)

    info = next_occurrence_info(record, today)

    assert info["next_occurrence"] == "2027-06-15"
    assert info["days_until"] == (date(2027, 6, 15) - today).days


def test_next_occurrence_info_annual_feb_29_on_non_leap_year_falls_back_to_feb_28():
    record = {"type": "event", "recurrence": "annual", "month": 2, "day": 29}
    today = date(2026, 1, 1)  # 2026 is not a leap year

    info = next_occurrence_info(record, today)

    assert info["next_occurrence"] == "2026-02-28"


def test_next_occurrence_info_once():
    record = {"type": "event", "recurrence": "once", "date": "2026-11-03"}
    today = date(2026, 6, 1)

    info = next_occurrence_info(record, today)

    assert info["next_occurrence"] == "2026-11-03"
    assert info["days_until"] == (date(2026, 11, 3) - today).days


def test_next_occurrence_info_due_soon_flag():
    today = date(2026, 6, 14)
    record = {
        "type": "event",
        "recurrence": "annual",
        "month": 6,
        "day": 15,
        "remind_days_before": 2,
    }

    info = next_occurrence_info(record, today)

    assert info["days_until"] == 1
    assert info["due_soon"] is True


def test_next_occurrence_info_not_due_soon_when_outside_window():
    today = date(2026, 1, 1)
    record = {
        "type": "event",
        "recurrence": "annual",
        "month": 6,
        "day": 15,
        "remind_days_before": 2,
    }

    info = next_occurrence_info(record, today)

    assert info["due_soon"] is False


def test_next_occurrence_info_omits_due_soon_when_no_reminder_set():
    record = {"type": "event", "recurrence": "once", "date": "2026-11-03"}

    info = next_occurrence_info(record, date(2026, 6, 1))

    assert "due_soon" not in info
