"""get_property must support reading several device+property pairs in a
single call instead of forcing one call per pair, and must reuse a single
`get_properties(device_id)` fetch for every entry that targets the same
device -- that call already returns every property for the device in one
hub round trip, so re-fetching per entry would throw away the free batching
win this rewrite exists for.
"""

from __future__ import annotations

import pytest

from nucore.cmd import Command
from nucore.node import Node
from nucore.nodedef import NodeCommands, NodeDef, NodeProperty, Property
from nucore.nucore_interface import NuCoreInterface
from unified.dispatch import execute_tool


def _build_node(address: str, name: str, properties: dict[str, str]) -> Node:
    """*properties* maps property id -> display name."""
    node_def = NodeDef(
        id=f"{address}_profile",
        properties={pid: NodeProperty(id=pid, editor=None, name=pname) for pid, pname in properties.items()},
        cmds=NodeCommands(accepts=[Command(id="DON", name="On", parameters=[])], sends=[]),
    )
    node = object.__new__(Node)
    node.address = address
    node.name = name
    node.node_def = node_def
    return node


class FakeBackend(NuCoreInterface):
    def __init__(self):
        super().__init__(json_output=True, formatter_type="minimal")
        kitchen = _build_node("n001_kitchen", "Kitchen Light", {"ST": "Status", "CLIHUM": "Current Humidity"})
        thermostat = _build_node("n002_thermostat", "Thermostat", {"CLITEMP": "Current Temperature"})
        self.nodes = {kitchen.address: kitchen, thermostat.address: thermostat}
        self.groups = {}
        self.folders = {}
        # device_id -> live property values for that device.
        self._live_values = {
            "n001_kitchen": {
                "ST": Property(id="ST", value="100", formatted="On", uom="78"),
                "CLIHUM": Property(id="CLIHUM", value="45", formatted="45%", uom="51"),
            },
            "n002_thermostat": {
                "CLITEMP": Property(id="CLITEMP", value="720", formatted="72.0 F", uom="17"),
            },
        }
        self.get_properties_calls: dict[str, int] = {}

    async def get_properties(self, device_id):
        self.get_properties_calls[device_id] = self.get_properties_calls.get(device_id, 0) + 1
        return self._live_values.get(device_id)

    async def _load(self, **kwargs): raise NotImplementedError
    async def _load_routines(self): raise NotImplementedError
    async def create_automation_routine(self, trigger): raise NotImplementedError
    async def update_routine(self, program): raise NotImplementedError
    def get_device_name(self, device_id): raise NotImplementedError
    def get_device_id(self, device_str): raise NotImplementedError
    async def get_all_routines_summary(self): raise NotImplementedError
    async def get_routine_summary(self, routine_id): raise NotImplementedError
    async def get_all_routines(self): raise NotImplementedError
    async def get_routine(self, routine_id): raise NotImplementedError
    async def add_node(self, node_name, type): raise NotImplementedError
    async def node_ops(self, node_id, operation, **kwargs): raise NotImplementedError
    async def routine_ops(self, routine_id, operation): raise NotImplementedError
    async def _load_variables(self): pass
    async def variable_ops(self, var_type, var_id, operation, **kwargs): raise NotImplementedError
    async def send_commands(self, commands): raise NotImplementedError
    async def group_scene_add_member(self, *a, **kw): raise NotImplementedError
    async def group_scene_remove_member(self, *a, **kw): raise NotImplementedError
    async def group_scene_update_link(self, *a, **kw): raise NotImplementedError
    async def group_scene_get_node_roles(self, *a, **kw): raise NotImplementedError
    async def group_scene_get_link_types(self, *a, **kw): raise NotImplementedError
    async def scene_test(self, device_id): raise NotImplementedError
    async def run_diagnostic_step(self, step, **params): raise NotImplementedError
    async def _subscribe_events(self, *a, **kw): raise NotImplementedError
    async def add_device(self, device_address, **kwargs): raise NotImplementedError
    async def discover_devices(self): raise NotImplementedError
    async def finish_device_discovery(self): raise NotImplementedError
    async def remove_device(self, device_address, protocol=None, **kwargs): raise NotImplementedError


def _result_for(result, device_id, property_name):
    return next(r for r in result["results"] if r.get("device_id") == device_id and r.get("property") == property_name)


@pytest.mark.asyncio
async def test_single_entry_reads_one_device_property():
    backend = FakeBackend()
    result = await execute_tool(
        "get_property",
        {"properties": [{"device_id": "n001_kitchen", "property": "Status"}]},
        nucore_interface=backend,
    )

    assert result["summary"] == {"total": 1, "successful": 1, "failed": 0}
    assert "error" not in result
    assert result["results"] == [
        {"device_id": "n001_kitchen", "device": "Kitchen Light", "property": "Status", "value": "On", "successful": True}
    ]


@pytest.mark.asyncio
async def test_multiple_properties_on_same_device_calls_get_properties_once():
    backend = FakeBackend()
    result = await execute_tool(
        "get_property",
        {"properties": [
            {"device_id": "n001_kitchen", "property": "Status"},
            {"device_id": "n001_kitchen", "property": "Current Humidity"},
        ]},
        nucore_interface=backend,
    )

    assert result["summary"] == {"total": 2, "successful": 2, "failed": 0}
    assert backend.get_properties_calls == {"n001_kitchen": 1}
    assert _result_for(result, "n001_kitchen", "Status")["value"] == "On"
    assert _result_for(result, "n001_kitchen", "Current Humidity")["value"] == "45%"


@pytest.mark.asyncio
async def test_multiple_devices_each_get_properties_called_once():
    backend = FakeBackend()
    result = await execute_tool(
        "get_property",
        {"properties": [
            {"device_id": "n001_kitchen", "property": "Status"},
            {"device_id": "n002_thermostat", "property": "Current Temperature"},
        ]},
        nucore_interface=backend,
    )

    assert result["summary"] == {"total": 2, "successful": 2, "failed": 0}
    assert backend.get_properties_calls == {"n001_kitchen": 1, "n002_thermostat": 1}


@pytest.mark.asyncio
async def test_wildcard_expands_to_every_property_on_that_device():
    backend = FakeBackend()
    result = await execute_tool(
        "get_property",
        {"properties": [{"device_id": "n001_kitchen", "property": "*"}]},
        nucore_interface=backend,
    )

    assert backend.get_properties_calls == {"n001_kitchen": 1}
    assert result["summary"] == {"total": 2, "successful": 2, "failed": 0}
    names = {r["property"] for r in result["results"]}
    assert names == {"Status", "Current Humidity"}


@pytest.mark.asyncio
async def test_partial_failure_bad_device_mixed_with_good_entries():
    backend = FakeBackend()
    result = await execute_tool(
        "get_property",
        {"properties": [
            {"device_id": "n001_kitchen", "property": "Status"},
            {"device_id": "does_not_exist", "property": "Status"},
        ]},
        nucore_interface=backend,
    )

    assert result["summary"] == {"total": 2, "successful": 1, "failed": 1}
    assert "error" in result
    bad = _result_for(result, "does_not_exist", "Status")
    assert bad["successful"] is False
    assert "no device found" in bad["error"]
    # The bad device never needed a get_properties() call at all.
    assert backend.get_properties_calls == {"n001_kitchen": 1}


@pytest.mark.asyncio
async def test_partial_failure_bad_property_name_same_device_still_one_fetch():
    backend = FakeBackend()
    result = await execute_tool(
        "get_property",
        {"properties": [
            {"device_id": "n001_kitchen", "property": "Status"},
            {"device_id": "n001_kitchen", "property": "Nonexistent"},
        ]},
        nucore_interface=backend,
    )

    assert result["summary"] == {"total": 2, "successful": 1, "failed": 1}
    bad = _result_for(result, "n001_kitchen", "Nonexistent")
    assert "not a known property" in bad["error"]
    assert backend.get_properties_calls == {"n001_kitchen": 1}


@pytest.mark.asyncio
async def test_all_entries_fail():
    backend = FakeBackend()
    result = await execute_tool(
        "get_property",
        {"properties": [{"device_id": "does_not_exist", "property": "Status"}]},
        nucore_interface=backend,
    )

    assert result["summary"] == {"total": 1, "successful": 0, "failed": 1}
    assert "error" in result


@pytest.mark.asyncio
async def test_properties_must_be_a_non_empty_list():
    backend = FakeBackend()

    empty = await execute_tool("get_property", {"properties": []}, nucore_interface=backend)
    assert empty == {"error": "'properties' must be a non-empty list"}

    missing = await execute_tool("get_property", {}, nucore_interface=backend)
    assert missing == {"error": "'properties' must be a non-empty list"}


@pytest.mark.asyncio
async def test_malformed_entry_reports_a_per_entry_error():
    backend = FakeBackend()
    result = await execute_tool(
        "get_property",
        {"properties": [{"device_id": "n001_kitchen"}, "not-an-object"]},
        nucore_interface=backend,
    )

    assert result["summary"] == {"total": 2, "successful": 0, "failed": 2}
    assert "device_id and property are both required" in result["results"][0]["error"]
    assert "not an object" in result["results"][1]["error"]
