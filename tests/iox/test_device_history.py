"""IoXWrapper.get_device_history -- device/property resolution (same rules
as get_property: per-device-scoped exact-name match, union across a
multi-device query) and querying DEVLOG.DB (the ISY/eisy firmware's
structured DEV.LOG capture, see design/history_impl_isy.md) via Python's
built-in `sqlite3` module against a temporary on-disk database, rather than
mocking it -- this exercises the actual SQL built against the actual
schema, including the LOCAL_ISO(...) function registered on every
connection.
"""

from __future__ import annotations

import datetime
import sqlite3
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import iox.iox_wrapper as iox_wrapper_module
from iox.iox_wrapper import IoXWrapper, _sql_quote, _iso_to_epoch, _validate_readonly_select_sql, _sql_has_order_by
from nucore.cmd import Command
from nucore.node import Node
from nucore.nodedef import NodeCommands, NodeDef, NodeProperty


def _build_node(address: str, *, properties=None) -> Node:
    node_def = NodeDef(
        id=f"{address}_profile",
        properties={p: NodeProperty(id=p, editor=None, name=p) for p in (properties or [])},
        cmds=NodeCommands(accepts=[Command(id="DON", name="On")], sends=[]),
    )
    node = object.__new__(Node)
    node.address = address
    node.name = address
    node.node_def = node_def
    return node


def _bare_wrapper(nodes: dict) -> IoXWrapper:
    wrapper = object.__new__(IoXWrapper)
    wrapper.nodes = nodes
    wrapper.groups = {}
    wrapper.folders = {}
    return wrapper


@pytest.fixture()
def devlog_db(tmp_path, monkeypatch):
    """A real, populated DEVLOG.DB on disk, with iox_wrapper pointed at it
    instead of the real firmware path."""
    db_path = tmp_path / "DEVLOG.DB"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE DevLogEvents (
           Id           INTEGER PRIMARY KEY AUTOINCREMENT,
           EventTime    INTEGER NOT NULL,
           NodeAddress  TEXT,  NodeName TEXT,
           ControlId    TEXT NOT NULL, ControlLabel TEXT,
           Action       TEXT,
           Actor        TEXT NOT NULL,
           EventType    INTEGER NOT NULL,
           IsCommand    INTEGER NOT NULL
        )
        """
    )
    rows = [
        (1000, "A", "Pool Pump", "ST", "Status", "Off", "System", 1, 0),
        (1010, "A", "Pool Pump", "ST", "Status", "On", "Web", 1, 1),
        (1020, "A", "Pool Pump", "ST", "Status", "On", "System", 1, 0),
        (1030, "A", "Pool Pump", "ST", "Status", "Off", "Routine", 1, 1),
        (1040, "A", "Pool Pump", "ST", "Status", "Off", "System", 1, 0),
        (2000, "B", "Attic Fan", "ST", "Status", "It's O'Clock", "System", 1, 0),
    ]
    conn.executemany(
        "INSERT INTO DevLogEvents (EventTime, NodeAddress, NodeName, ControlId, ControlLabel, Action, Actor, EventType, IsCommand) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(iox_wrapper_module, "_DEVLOG_DB_PATH", str(db_path))
    return db_path


@pytest.fixture()
def devlog_db_multi_control(tmp_path, monkeypatch):
    """A DEVLOG.DB where node A logged activity under TWO different
    controls -- ST ("Status", the system echo) and DON ("On", the Web-issued
    command that caused it) -- the exact shape that makes a
    properties=["Status"]-scoped structured query blind to the corroborating
    command row (see PROPERTIES_WILDCARD's docstring and
    design/history_impl_isy.md's ControlLabel resolution section)."""
    db_path = tmp_path / "DEVLOG.DB"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE DevLogEvents (
           Id           INTEGER PRIMARY KEY AUTOINCREMENT,
           EventTime    INTEGER NOT NULL,
           NodeAddress  TEXT,  NodeName TEXT,
           ControlId    TEXT NOT NULL, ControlLabel TEXT,
           Action       TEXT,
           Actor        TEXT NOT NULL,
           EventType    INTEGER NOT NULL,
           IsCommand    INTEGER NOT NULL
        )
        """
    )
    rows = [
        (1000, "A", "Backyard Steps", "DON", "On", "0", "Web", 1, 1),
        (1001, "A", "Backyard Steps", "ST", "Status", "On", "System", 1, 0),
    ]
    conn.executemany(
        "INSERT INTO DevLogEvents (EventTime, NodeAddress, NodeName, ControlId, ControlLabel, Action, Actor, EventType, IsCommand) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(iox_wrapper_module, "_DEVLOG_DB_PATH", str(db_path))
    return db_path


# ------------------------------------------------------------------
# Small helpers
# ------------------------------------------------------------------


def test_sql_quote_escapes_embedded_single_quotes():
    assert _sql_quote("O'Brien") == "'O''Brien'"
    assert _sql_quote("plain") == "'plain'"


def test_iso_to_epoch_parses_offset_and_z_timestamps():
    assert _iso_to_epoch("2026-01-01T00:00:00+00:00") == 1767225600
    assert _iso_to_epoch("2026-01-01T00:00:00Z") == 1767225600


# ------------------------------------------------------------------
# Device/property resolution (no DB touched for these failure paths)
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_history_unknown_device_is_a_clear_error():
    wrapper = _bare_wrapper({"A": _build_node("A", properties=["ST"])})
    result = await wrapper.get_device_history(["MISSING"], ["ST"])
    assert result["successful"] is False
    assert "MISSING" in result["data"]


@pytest.mark.asyncio
async def test_get_history_unknown_property_on_every_device_is_a_clear_error():
    wrapper = _bare_wrapper({"A": _build_node("A", properties=["ST"])})
    result = await wrapper.get_device_history(["A"], ["NoSuchProperty"])
    assert result["successful"] is False
    assert "NoSuchProperty" in result["data"]


@pytest.mark.asyncio
async def test_get_history_property_valid_on_only_one_of_several_devices_still_resolves(devlog_db):
    # Same rule as get_property/resolve_property_id: a name valid on ANY of
    # the given devices is accepted, not required on all of them.
    wrapper = _bare_wrapper(
        {
            "A": _build_node("A", properties=["ST"]),
            "B": _build_node("B", properties=["Temperature"]),
        }
    )
    result = await wrapper.get_device_history(["A", "B"], ["ST"])
    assert result["successful"] is True


@pytest.mark.asyncio
async def test_get_history_wildcard_properties_skips_resolution_entirely():
    # ["*"] must not go through resolve_property_id -- a device with no
    # matching properties at all would otherwise hit the "not a known
    # property" error path.
    wrapper = _bare_wrapper({"A": _build_node("A", properties=[])})
    result = await wrapper.get_device_history(["A"], ["*"], start="2026-01-01T00:00:00+00:00")
    assert result["successful"] is True


# ------------------------------------------------------------------
# Real sqlite3-CLI-backed queries
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_history_wildcard_returns_every_control_not_just_one_property(devlog_db_multi_control):
    # The bug this generalizes: a Web-issued "On" command is logged under
    # ControlId=DON, not ST -- properties=["Status"] alone would never see
    # it (see the next test). Wildcard must return both.
    wrapper = _bare_wrapper({"A": _build_node("A", properties=["ST", "DON"])})
    result = await wrapper.get_device_history(["A"], ["*"])
    assert result["successful"] is True
    groups = result["data"]
    controls = {g["property"] for g in groups}
    assert controls == {"ST", "DON"}
    don_group = next(g for g in groups if g["property"] == "DON")
    assert don_group["history"][0]["actor"] == "Web"
    assert don_group["history"][0]["is_command"] is True


@pytest.mark.asyncio
async def test_get_history_specific_property_misses_the_other_control(devlog_db_multi_control):
    # Confirms the bug this wildcard fixes: scoping to just "Status" (ST)
    # never surfaces the corroborating Web-issued DON row, even though it's
    # in the same window for the same device.
    wrapper = _bare_wrapper({"A": _build_node("A", properties=["ST", "DON"])})
    result = await wrapper.get_device_history(["A"], ["ST"])
    assert result["successful"] is True
    groups = result["data"]
    assert len(groups) == 1
    assert groups[0]["property"] == "ST"
    assert all(e["actor"] == "System" for e in groups[0]["history"])


@pytest.mark.asyncio
async def test_get_history_returns_rows_grouped_by_node_and_property(devlog_db):
    wrapper = _bare_wrapper({"A": _build_node("A", properties=["ST"])})
    result = await wrapper.get_device_history(["A"], ["ST"])
    assert result["successful"] is True
    groups = result["data"]
    assert len(groups) == 1
    group = groups[0]
    assert group["node"] == "A"
    assert group["device_name"] == "Pool Pump"
    assert group["property"] == "ST"
    assert group["property_name"] == "Status"
    assert len(group["history"]) == 5
    # Chronological order, and actor/is_command carried through per entry.
    assert [e["action"] for e in group["history"]] == ["Off", "On", "On", "Off", "Off"]
    assert group["history"][1]["actor"] == "Web"
    assert group["history"][1]["is_command"] is True
    assert group["history"][0]["actor"] == "System"
    assert group["history"][0]["is_command"] is False


@pytest.mark.asyncio
async def test_get_history_respects_start_end_window(devlog_db):
    wrapper = _bare_wrapper({"A": _build_node("A", properties=["ST"])})
    # ISO timestamps matching the fixture's EventTime values exactly.
    from datetime import datetime, timezone

    start_iso = datetime.fromtimestamp(1010, tz=timezone.utc).isoformat()
    end_iso = datetime.fromtimestamp(1030, tz=timezone.utc).isoformat()
    result = await wrapper.get_device_history(["A"], ["ST"], start=start_iso, end=end_iso)
    assert result["successful"] is True
    history = result["data"][0]["history"]
    assert [e["action"] for e in history] == ["On", "On", "Off"]


@pytest.mark.asyncio
async def test_get_history_one_before_one_after_boundary_rows(devlog_db):
    from datetime import datetime, timezone

    wrapper = _bare_wrapper({"A": _build_node("A", properties=["ST"])})
    start_iso = datetime.fromtimestamp(1020, tz=timezone.utc).isoformat()
    end_iso = datetime.fromtimestamp(1020, tz=timezone.utc).isoformat()
    result = await wrapper.get_device_history(
        ["A"], ["ST"], start=start_iso, end=end_iso, one_before=True, one_after=True
    )
    assert result["successful"] is True
    history = result["data"][0]["history"]
    # In-window: just the 1020 row. Boundary: 1010 before it, 1030 after it.
    assert [e["action"] for e in history] == ["On", "On", "Off"]


@pytest.mark.asyncio
async def test_get_history_limit_caps_rows(devlog_db):
    wrapper = _bare_wrapper({"A": _build_node("A", properties=["ST"])})
    result = await wrapper.get_device_history(["A"], ["ST"], limit=2)
    assert result["successful"] is True
    assert len(result["data"][0]["history"]) == 2


@pytest.mark.asyncio
async def test_get_history_handles_values_with_embedded_quotes(devlog_db):
    wrapper = _bare_wrapper({"B": _build_node("B", properties=["ST"])})
    result = await wrapper.get_device_history(["B"], ["ST"])
    assert result["successful"] is True
    assert result["data"][0]["history"][0]["action"] == "It's O'Clock"


@pytest.mark.asyncio
async def test_get_history_missing_database_is_a_clear_error_not_a_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(iox_wrapper_module, "_DEVLOG_DB_PATH", str(tmp_path / "does-not-exist.db"))
    wrapper = _bare_wrapper({"A": _build_node("A", properties=["ST"])})
    result = await wrapper.get_device_history(["A"], ["ST"])
    assert result["successful"] is False


@pytest.mark.asyncio
async def test_get_history_invalid_timestamp_is_a_clear_error_not_a_crash(devlog_db):
    wrapper = _bare_wrapper({"A": _build_node("A", properties=["ST"])})
    result = await wrapper.get_device_history(["A"], ["ST"], start="not-a-timestamp")
    assert result["successful"] is False


# ------------------------------------------------------------------
# _validate_readonly_select_sql -- pure function, no DB needed.
# ------------------------------------------------------------------


def test_validate_readonly_select_sql_accepts_select_and_with():
    cleaned, err = _validate_readonly_select_sql("SELECT * FROM DevLogEvents")
    assert err is None
    assert cleaned == "SELECT * FROM DevLogEvents"

    cleaned, err = _validate_readonly_select_sql(
        "WITH x AS (SELECT 1) SELECT * FROM x"
    )
    assert err is None


def test_validate_readonly_select_sql_strips_trailing_semicolon():
    cleaned, err = _validate_readonly_select_sql("SELECT 1;")
    assert err is None
    assert cleaned == "SELECT 1"


def test_validate_readonly_select_sql_rejects_multiple_statements():
    cleaned, err = _validate_readonly_select_sql("SELECT 1; DROP TABLE DevLogEvents")
    assert cleaned is None
    assert "single SQL statement" in err


@pytest.mark.parametrize("bad_sql", [
    "DELETE FROM DevLogEvents",
    "INSERT INTO DevLogEvents (Id) VALUES (1)",
    "UPDATE DevLogEvents SET Action='x'",
    "CREATE TABLE x (a INT)",
    "DROP TABLE DevLogEvents",
])
def test_validate_readonly_select_sql_rejects_non_select_leading_keyword(bad_sql):
    cleaned, err = _validate_readonly_select_sql(bad_sql)
    assert cleaned is None
    assert "SELECT" in err


def test_validate_readonly_select_sql_rejects_forbidden_keyword_mid_query():
    cleaned, err = _validate_readonly_select_sql("SELECT * FROM DevLogEvents; ATTACH DATABASE ':memory:' AS x")
    assert cleaned is None
    # Caught by the semicolon check first, but either way it's rejected.
    assert err is not None


def test_validate_readonly_select_sql_rejects_forbidden_keyword_without_semicolon():
    cleaned, err = _validate_readonly_select_sql("SELECT * FROM DevLogEvents PRAGMA foo")
    assert cleaned is None
    assert "PRAGMA" in err


def test_validate_readonly_select_sql_ignores_keyword_inside_string_literal():
    cleaned, err = _validate_readonly_select_sql("SELECT * FROM DevLogEvents WHERE Action = 'DROP'")
    assert err is None
    assert cleaned == "SELECT * FROM DevLogEvents WHERE Action = 'DROP'"


def test_validate_readonly_select_sql_rejects_empty_and_oversized_input():
    cleaned, err = _validate_readonly_select_sql("")
    assert cleaned is None
    assert "empty" in err

    cleaned, err = _validate_readonly_select_sql("SELECT " + "1" * 5000)
    assert cleaned is None
    assert "limit" in err


# ------------------------------------------------------------------
# _sql_has_order_by -- pure function, no DB needed.
# ------------------------------------------------------------------


def test_sql_has_order_by_detects_top_level_order_by():
    assert _sql_has_order_by("SELECT * FROM DevLogEvents ORDER BY EventTime") is True


def test_sql_has_order_by_false_when_absent():
    assert _sql_has_order_by("SELECT * FROM DevLogEvents WHERE NodeAddress='A'") is False


def test_sql_has_order_by_detects_one_inside_a_subquery():
    assert _sql_has_order_by(
        "SELECT * FROM (SELECT * FROM DevLogEvents ORDER BY EventTime LIMIT 10)"
    ) is True


def test_sql_has_order_by_is_case_insensitive_and_tolerates_extra_whitespace():
    assert _sql_has_order_by("select * from DevLogEvents order   by EventTime") is True


def test_sql_has_order_by_ignores_order_by_inside_string_literal():
    assert _sql_has_order_by("SELECT * FROM DevLogEvents WHERE Action = 'order by hand'") is False


# ------------------------------------------------------------------
# Raw-SQL mode via the wrapper (real sqlite3 CLI, real temp DB).
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_history_sql_mode_returns_rows(devlog_db):
    wrapper = _bare_wrapper({})
    result = await wrapper.get_device_history(
        sql="SELECT NodeAddress, Action FROM DevLogEvents WHERE NodeAddress='A' ORDER BY EventTime"
    )
    assert result["successful"] is True
    assert result["truncated"] is False
    assert result["unordered"] is False
    assert [r["Action"] for r in result["data"]] == ["Off", "On", "On", "Off", "Off"]


@pytest.mark.asyncio
async def test_get_history_sql_mode_supports_aggregation(devlog_db):
    wrapper = _bare_wrapper({})
    result = await wrapper.get_device_history(
        sql="SELECT Actor, count(*) AS n FROM DevLogEvents WHERE NodeAddress='A' GROUP BY Actor ORDER BY Actor"
    )
    assert result["successful"] is True
    assert result["unordered"] is False
    counts = {r["Actor"]: r["n"] for r in result["data"]}
    assert counts == {"Routine": 1, "System": 3, "Web": 1}


@pytest.mark.asyncio
async def test_get_history_sql_mode_truncates_and_flags(devlog_db):
    wrapper = _bare_wrapper({})
    result = await wrapper.get_device_history(
        sql="SELECT * FROM DevLogEvents WHERE NodeAddress='A' ORDER BY EventTime", limit=2
    )
    assert result["successful"] is True
    assert len(result["data"]) == 2
    assert result["truncated"] is True
    assert result["unordered"] is False


@pytest.mark.asyncio
async def test_get_history_sql_mode_flags_unordered_when_no_order_by(devlog_db):
    wrapper = _bare_wrapper({})
    result = await wrapper.get_device_history(sql="SELECT * FROM DevLogEvents WHERE NodeAddress='A'")
    assert result["successful"] is True
    assert result["unordered"] is True


@pytest.mark.asyncio
async def test_get_history_sql_mode_respects_hard_row_cap(devlog_db, monkeypatch):
    monkeypatch.setattr(iox_wrapper_module, "_DEVLOG_SQL_MAX_ROWS", 2)
    wrapper = _bare_wrapper({})
    result = await wrapper.get_device_history(
        sql="SELECT * FROM DevLogEvents WHERE NodeAddress='A' ORDER BY EventTime", limit=500
    )
    assert result["successful"] is True
    assert len(result["data"]) == 2
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_get_history_sql_mode_and_structured_params_together_is_an_error(devlog_db):
    wrapper = _bare_wrapper({"A": _build_node("A", properties=["ST"])})
    result = await wrapper.get_device_history(["A"], ["ST"], sql="SELECT * FROM DevLogEvents")
    assert result["successful"] is False
    assert "not both" in result["data"]


@pytest.mark.asyncio
async def test_get_history_sql_mode_rejects_multiple_statements_and_leaves_table_untouched(devlog_db):
    wrapper = _bare_wrapper({})
    result = await wrapper.get_device_history(sql="SELECT 1; DROP TABLE DevLogEvents")
    assert result["successful"] is False

    conn = sqlite3.connect(str(devlog_db))
    count = conn.execute("SELECT count(*) FROM DevLogEvents").fetchone()[0]
    conn.close()
    assert count == 6


@pytest.mark.asyncio
async def test_get_history_sql_mode_rejects_non_select_statement_and_leaves_table_untouched(devlog_db):
    wrapper = _bare_wrapper({})
    result = await wrapper.get_device_history(sql="DELETE FROM DevLogEvents")
    assert result["successful"] is False

    conn = sqlite3.connect(str(devlog_db))
    count = conn.execute("SELECT count(*) FROM DevLogEvents").fetchone()[0]
    conn.close()
    assert count == 6


@pytest.mark.asyncio
async def test_get_history_sql_mode_rejects_forbidden_keyword(devlog_db):
    wrapper = _bare_wrapper({})
    result = await wrapper.get_device_history(sql="ATTACH DATABASE ':memory:' AS x")
    assert result["successful"] is False


@pytest.mark.asyncio
async def test_get_history_sql_mode_allows_keyword_as_literal_value(devlog_db):
    wrapper = _bare_wrapper({})
    result = await wrapper.get_device_history(sql="SELECT * FROM DevLogEvents WHERE Action = 'DROP'")
    assert result["successful"] is True
    assert result["data"] == []


@pytest.mark.asyncio
async def test_get_history_sql_mode_missing_database_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(iox_wrapper_module, "_DEVLOG_DB_PATH", str(tmp_path / "does-not-exist.db"))
    wrapper = _bare_wrapper({})
    result = await wrapper.get_device_history(sql="SELECT * FROM DevLogEvents")
    assert result["successful"] is False


# ------------------------------------------------------------------
# Timezone conversion -- LOCAL_ISO(EventTime), the installation's real
# timezone (never the server process's own OS timezone).
# ------------------------------------------------------------------


def _mock_timespecs(monkeypatch, tz_name: str | None):
    """Monkeypatch IoXWrapper.get_timespecs (what _resolve_history_tzinfo
    calls) to return a fixed timezone -- or None, to simulate the hub call
    failing/being unavailable."""

    async def _fake_get_timespecs(self):
        return {"timezone": tz_name} if tz_name else None

    monkeypatch.setattr(IoXWrapper, "get_timespecs", _fake_get_timespecs)


def _expected_local_iso(epoch: int, tz_name: str) -> str:
    """Same conversion get_device_history is supposed to perform --
    used as the test oracle, not copied production code, since this is
    exactly the stdlib zoneinfo/datetime behavior the fix relies on."""
    return (
        datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc)
        .astimezone(ZoneInfo(tz_name))
        .isoformat()
    )


@pytest.mark.asyncio
async def test_get_history_structured_mode_localizes_per_event_across_dst(devlog_db, monkeypatch):
    # Two real epochs on opposite sides of a US DST transition -- proves
    # the conversion is applied per-event (real historical DST), not once
    # using "now"'s offset.
    epoch_jan = int(datetime.datetime(2026, 1, 15, 12, 0, 0, tzinfo=datetime.timezone.utc).timestamp())
    epoch_jul = int(datetime.datetime(2026, 7, 15, 12, 0, 0, tzinfo=datetime.timezone.utc).timestamp())
    conn = sqlite3.connect(str(devlog_db))
    conn.executemany(
        "INSERT INTO DevLogEvents (EventTime, NodeAddress, NodeName, ControlId, ControlLabel, Action, Actor, EventType, IsCommand) "
        "VALUES (?, 'C', 'Winter/Summer Test', 'ST', 'Status', 'On', 'System', 1, 0)",
        [(epoch_jan,), (epoch_jul,)],
    )
    conn.commit()
    conn.close()

    _mock_timespecs(monkeypatch, "America/Los_Angeles")
    wrapper = _bare_wrapper({"C": _build_node("C", properties=["ST"])})
    result = await wrapper.get_device_history(["C"], ["ST"])
    assert result["successful"] is True
    history = result["data"][0]["history"]
    assert len(history) == 2

    expected_jan = _expected_local_iso(epoch_jan, "America/Los_Angeles")
    expected_jul = _expected_local_iso(epoch_jul, "America/Los_Angeles")
    assert expected_jan.endswith("-08:00")  # PST -- sanity check on the oracle itself
    assert expected_jul.endswith("-07:00")  # PDT
    assert history[0]["timestamp"] == expected_jan
    assert history[1]["timestamp"] == expected_jul


@pytest.mark.asyncio
async def test_get_history_structured_mode_falls_back_to_utc_without_timespecs(devlog_db, monkeypatch):
    _mock_timespecs(monkeypatch, None)
    wrapper = _bare_wrapper({"A": _build_node("A", properties=["ST"])})
    result = await wrapper.get_device_history(["A"], ["ST"], limit=1)
    assert result["successful"] is True
    assert result["data"][0]["history"][0]["timestamp"].endswith("+00:00")


@pytest.mark.asyncio
async def test_get_history_sql_mode_local_iso_is_callable_directly(devlog_db, monkeypatch):
    _mock_timespecs(monkeypatch, "America/Los_Angeles")
    wrapper = _bare_wrapper({})
    result = await wrapper.get_device_history(
        sql="SELECT EventTime, LOCAL_ISO(EventTime) AS lt FROM DevLogEvents WHERE NodeAddress='A' ORDER BY EventTime LIMIT 1"
    )
    assert result["successful"] is True
    row = result["data"][0]
    assert row["lt"] == _expected_local_iso(row["EventTime"], "America/Los_Angeles")


@pytest.mark.asyncio
async def test_get_history_sql_mode_local_iso_usable_in_group_by(devlog_db, monkeypatch):
    _mock_timespecs(monkeypatch, "America/Los_Angeles")
    wrapper = _bare_wrapper({})
    result = await wrapper.get_device_history(
        sql=(
            "SELECT date(LOCAL_ISO(EventTime)) AS day, count(*) AS n FROM DevLogEvents "
            "WHERE NodeAddress='A' GROUP BY day"
        )
    )
    assert result["successful"] is True
    assert sum(r["n"] for r in result["data"]) == 5


@pytest.mark.asyncio
async def test_get_history_sql_mode_falls_back_to_utc_without_timespecs(devlog_db, monkeypatch):
    _mock_timespecs(monkeypatch, None)
    wrapper = _bare_wrapper({})
    result = await wrapper.get_device_history(
        sql="SELECT EventTime, LOCAL_ISO(EventTime) AS lt FROM DevLogEvents WHERE NodeAddress='A' ORDER BY EventTime LIMIT 1"
    )
    assert result["successful"] is True
    assert result["data"][0]["lt"].endswith("+00:00")


@pytest.mark.asyncio
async def test_devlog_connection_rejects_writes_at_the_engine_level(devlog_db):
    # Bypasses both _validate_readonly_select_sql and the SELECT-wrapping
    # entirely -- proves `mode=ro` + `PRAGMA query_only` block a write at
    # the sqlite3 engine level, independent of the text-scan validator.
    rows, err = await iox_wrapper_module._run_devlog_sqlite_query(
        "UPDATE DevLogEvents SET Action='HACKED' WHERE NodeAddress='A'",
        datetime.timezone.utc,
    )
    assert rows is None
    assert err is not None

    conn = sqlite3.connect(str(devlog_db))
    action = conn.execute("SELECT Action FROM DevLogEvents WHERE NodeAddress='A' AND EventTime=1000").fetchone()[0]
    conn.close()
    assert action == "Off"


@pytest.mark.asyncio
async def test_devlog_query_times_out_on_a_long_running_query(devlog_db, monkeypatch):
    monkeypatch.setattr(iox_wrapper_module, "_SQLITE_QUERY_TIMEOUT_S", 0.05)
    sql = (
        "WITH RECURSIVE cnt(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM cnt LIMIT 50000000) "
        "SELECT count(*) FROM cnt"
    )
    rows, err = await iox_wrapper_module._run_devlog_sqlite_query(sql, datetime.timezone.utc)
    assert rows is None
    assert "timed out" in err
