"""get_device_history handler tests -- arg validation, mode dispatch
(structured vs. raw SQL), unwrapping the {"successful", "data"} envelope,
and computing the structured-mode pagination hint fields. Device/property
resolution and the real sqlite3 query live in IoXWrapper (see
tests/iox/test_device_history.py); this file exercises the handler in
isolation via a FakeBackend that returns canned envelopes directly, the
same way other handler tests in this directory fake their NuCoreInterface
methods rather than a real backend.
"""

from __future__ import annotations

from typing import Any

import pytest

from nucore.nucore_interface import NuCoreInterface
from unified.dispatch import execute_tool


class FakeBackend(NuCoreInterface):
    def __init__(self):
        super().__init__(json_output=True, formatter_type="minimal")
        self.history_result: Any = {
            "successful": True,
            "data": [{"node": "1", "property": "ST", "history": []}],
        }
        self.last_call: dict[str, Any] | None = None

    async def get_device_history(
        self,
        device_ids=None,
        properties=None,
        *,
        start=None,
        end=None,
        one_before=False,
        one_after=False,
        limit=500,
        sql=None,
    ):
        self.last_call = {
            "device_ids": device_ids,
            "properties": properties,
            "start": start,
            "end": end,
            "one_before": one_before,
            "one_after": one_after,
            "limit": limit,
            "sql": sql,
        }
        return self.history_result

    async def run_diagnostic_step(self, step, **params): raise NotImplementedError
    async def add_device(self, device_address, **kwargs): raise NotImplementedError
    async def discover_devices(self): raise NotImplementedError
    async def finish_device_discovery(self): raise NotImplementedError
    async def remove_device(self, device_address, protocol=None, **kwargs): raise NotImplementedError
    async def _load(self, **kwargs): raise NotImplementedError
    async def _load_routines(self): raise NotImplementedError
    async def _load_variables(self): pass
    async def send_commands(self, commands): raise NotImplementedError
    async def create_automation_routine(self, trigger): raise NotImplementedError
    async def update_routine(self, program): raise NotImplementedError
    async def get_routine(self, routine_id): raise NotImplementedError
    async def get_properties(self, device_id): raise NotImplementedError
    def get_device_name(self, device_id): raise NotImplementedError
    def get_device_id(self, device_str): raise NotImplementedError
    async def get_all_routines_summary(self): raise NotImplementedError
    async def get_routine_summary(self, routine_id): raise NotImplementedError
    async def get_all_routines(self): raise NotImplementedError
    async def add_node(self, node_name, type): raise NotImplementedError
    async def node_ops(self, node_id, operation, **kwargs): raise NotImplementedError
    async def routine_ops(self, routine_id, operation): raise NotImplementedError
    async def variable_ops(self, var_type, var_id, operation, **kwargs): raise NotImplementedError
    async def group_scene_add_member(self, *a, **kw): raise NotImplementedError
    async def group_scene_remove_member(self, *a, **kw): raise NotImplementedError
    async def group_scene_update_link(self, *a, **kw): raise NotImplementedError
    async def group_scene_get_node_roles(self, *a, **kw): raise NotImplementedError
    async def group_scene_get_link_types(self, *a, **kw): raise NotImplementedError
    async def _subscribe_events(self, *a, **kw): raise NotImplementedError


# ------------------------------------------------------------------
# Structured mode
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_history_requires_device_ids_and_properties_when_no_sql():
    backend = FakeBackend()
    assert "error" in await execute_tool(
        "get_device_history", {"properties": ["ST"]}, nucore_interface=backend
    )
    assert "error" in await execute_tool(
        "get_device_history", {"device_ids": ["A"]}, nucore_interface=backend
    )
    assert "error" in await execute_tool(
        "get_device_history", {"device_ids": [], "properties": ["ST"]}, nucore_interface=backend
    )


@pytest.mark.asyncio
async def test_get_history_forwards_all_structured_params():
    backend = FakeBackend()
    await execute_tool(
        "get_device_history",
        {
            "device_ids": ["A", "B"],
            "properties": ["ST", "CLIHUM"],
            "start": "2026-01-01T00:00:00-08:00",
            "end": "2026-01-02T00:00:00-08:00",
            "one_before": True,
            "one_after": True,
            "limit": 50,
        },
        nucore_interface=backend,
    )
    assert backend.last_call == {
        "device_ids": ["A", "B"],
        "properties": ["ST", "CLIHUM"],
        "start": "2026-01-01T00:00:00-08:00",
        "end": "2026-01-02T00:00:00-08:00",
        "one_before": True,
        "one_after": True,
        "limit": 50,
        "sql": None,
    }


@pytest.mark.asyncio
async def test_get_history_defaults_limit_to_500():
    backend = FakeBackend()
    await execute_tool(
        "get_device_history", {"device_ids": ["A"], "properties": ["ST"]}, nucore_interface=backend
    )
    assert backend.last_call["limit"] == 500


@pytest.mark.asyncio
async def test_get_history_structured_backend_failure_surfaces_as_error():
    backend = FakeBackend()
    backend.history_result = {"successful": False, "data": "no device found with id 'A'"}
    result = await execute_tool(
        "get_device_history", {"device_ids": ["A"], "properties": ["ST"]}, nucore_interface=backend
    )
    assert result == {"error": "no device found with id 'A'"}


@pytest.mark.asyncio
async def test_get_history_structured_backend_exception_surfaces_as_error():
    class ExplodingBackend(FakeBackend):
        async def get_device_history(self, *a, **kw):
            raise RuntimeError("boom")

    result = await execute_tool(
        "get_device_history", {"device_ids": ["A"], "properties": ["ST"]}, nucore_interface=ExplodingBackend()
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_get_history_returns_results_without_pagination_hint_when_under_limit():
    backend = FakeBackend()
    backend.history_result = {
        "successful": True,
        "data": [
            {
                "node": "A",
                "property": "ST",
                "history": [{"timestamp": "t1", "action": "On", "actor": "Web", "is_command": True}],
            }
        ],
    }
    result = await execute_tool(
        "get_device_history", {"device_ids": ["A"], "properties": ["ST"], "limit": 500}, nucore_interface=backend
    )
    assert result["results"] == backend.history_result["data"]
    assert "more_available" not in result


@pytest.mark.asyncio
async def test_get_history_flags_pagination_when_record_count_hits_limit():
    backend = FakeBackend()
    backend.history_result = {
        "successful": True,
        "data": [
            {
                "node": "A",
                "property": "ST",
                "history": [
                    {"timestamp": "t1", "action": "On", "actor": "Web", "is_command": True},
                    {"timestamp": "t2", "action": "Off", "actor": "Routine", "is_command": True},
                ],
            }
        ],
    }
    result = await execute_tool(
        "get_device_history", {"device_ids": ["A"], "properties": ["ST"], "limit": 2}, nucore_interface=backend
    )
    assert result["more_available"] is True
    assert result["next_start"] == "t2"


@pytest.mark.asyncio
async def test_get_history_unrecognized_shape_is_a_clear_error_not_a_crash():
    backend = FakeBackend()
    backend.history_result = {"successful": True, "data": "not a dict or list"}
    result = await execute_tool(
        "get_device_history", {"device_ids": ["A"], "properties": ["ST"]}, nucore_interface=backend
    )
    assert "error" in result


# ------------------------------------------------------------------
# Raw-SQL mode
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_history_sql_mode_passes_through_rows_and_truncated_flag():
    backend = FakeBackend()
    backend.history_result = {
        "successful": True,
        "data": [{"NodeAddress": "A"}],
        "truncated": True,
        "unordered": False,
    }

    result = await execute_tool(
        "get_device_history", {"sql": "SELECT * FROM DevLogEvents"}, nucore_interface=backend
    )

    assert result == {"rows": [{"NodeAddress": "A"}], "truncated": True, "unordered": False}
    assert backend.last_call["sql"] == "SELECT * FROM DevLogEvents"


@pytest.mark.asyncio
async def test_get_history_sql_mode_passes_through_unordered_flag():
    backend = FakeBackend()
    backend.history_result = {
        "successful": True,
        "data": [{"NodeAddress": "A"}],
        "truncated": False,
        "unordered": True,
    }

    result = await execute_tool(
        "get_device_history", {"sql": "SELECT * FROM DevLogEvents WHERE NodeAddress='A'"}, nucore_interface=backend
    )

    assert result["unordered"] is True


@pytest.mark.asyncio
async def test_get_history_sql_mode_defaults_limit_to_500():
    backend = FakeBackend()
    await execute_tool("get_device_history", {"sql": "SELECT 1"}, nucore_interface=backend)
    assert backend.last_call["limit"] == 500


@pytest.mark.asyncio
async def test_get_history_rejects_sql_combined_with_structured_params():
    backend = FakeBackend()
    result = await execute_tool(
        "get_device_history",
        {"sql": "SELECT * FROM DevLogEvents", "device_ids": ["A"], "properties": ["ST"]},
        nucore_interface=backend,
    )
    assert "error" in result
    assert backend.last_call is None


@pytest.mark.asyncio
async def test_get_history_sql_mode_backend_failure_surfaces_as_error():
    backend = FakeBackend()
    backend.history_result = {"successful": False, "data": "sql must be a single SELECT (or WITH ... SELECT) statement"}
    result = await execute_tool(
        "get_device_history", {"sql": "DELETE FROM DevLogEvents"}, nucore_interface=backend
    )
    assert result == {"error": "sql must be a single SELECT (or WITH ... SELECT) statement"}


@pytest.mark.asyncio
async def test_get_history_sql_mode_backend_exception_surfaces_as_error():
    class ExplodingBackend(FakeBackend):
        async def get_device_history(self, *a, **kw):
            raise RuntimeError("boom")

    result = await execute_tool(
        "get_device_history", {"sql": "SELECT 1"}, nucore_interface=ExplodingBackend()
    )
    assert "error" in result
