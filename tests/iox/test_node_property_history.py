"""IoXWrapper.set_node_property_history_recording/get_node_property_history
-- device/property resolution (same rules as get_property: per-device-scoped
exact-name match, union across a multi-device query), URL/query-string
construction against design/history.md's documented endpoints, and parsing
the real XML response shape (confirmed against a live hub -- design/history.md's
originally guessed JSON shape was wrong).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from iox.iox_wrapper import IoXWrapper, _parse_node_property_history_xml
from nucore.cmd import Command
from nucore.node import Node
from nucore.nodedef import NodeCommands, NodeDef, NodeProperty

# A real sample captured from a live hub (Pool Pump, ZY008_1, "Status"
# property, Sept 4-10) -- used as a golden fixture below rather than a
# hand-rolled approximation, so the parser is checked against the actual
# confirmed shape, not a guess about it.
_LIVE_SAMPLE_XML = """<history>
<node id="ZY008_1">
<properties>
<property id="ST" name="Status">
<event timestamp="2026-09-04T15:18:43.231576-07:00">
<value uom="78" precision="0">
<scaled>0</scaled>
<float>0.0</float>
<formatted>Off</formatted>
</value>
</event>
<event timestamp="2026-09-04T19:00:00.237540-07:00">
<value uom="78" precision="0">
<scaled>100</scaled>
<float>100.0</float>
<formatted>On</formatted>
</value>
</event>
</property>
</properties>
</node>
</history>"""


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


def _bare_wrapper(nodes: dict, *, response=None) -> IoXWrapper:
    # Bypasses __init__ (no real hub connection needed) -- these two methods
    # only ever touch self.nodes/groups/folders (via inherited get_node/
    # resolve_property_id) and self.get.
    wrapper = object.__new__(IoXWrapper)
    wrapper.nodes = nodes
    wrapper.groups = {}
    wrapper.folders = {}
    wrapper.get_calls: list[str] = []

    async def fake_get(path):
        wrapper.get_calls.append(path)
        return response if response is not None else SimpleNamespace(status_code=200, text="<history></history>")

    wrapper.get = fake_get
    return wrapper


# ------------------------------------------------------------------
# XML parsing (the confirmed real response shape)
# ------------------------------------------------------------------


def test_parse_live_sample_matches_the_confirmed_shape():
    groups = _parse_node_property_history_xml(_LIVE_SAMPLE_XML)
    assert groups == [
        {
            "node": "ZY008_1",
            "property": "ST",
            "property_name": "Status",
            "history": [
                {"timestamp": "2026-09-04T15:18:43.231576-07:00", "value": "0", "formatted": "Off", "uom": "78", "prec": "0"},
                {"timestamp": "2026-09-04T19:00:00.237540-07:00", "value": "100", "formatted": "On", "uom": "78", "prec": "0"},
            ],
        }
    ]


def test_parse_empty_history_yields_an_empty_list_not_an_error():
    assert _parse_node_property_history_xml("<history></history>") == []


def test_parse_multiple_nodes_and_properties_each_become_their_own_group():
    xml = """<history>
<node id="A">
<properties>
<property id="ST" name="Status">
<event timestamp="t1"><value uom="78" precision="0"><scaled>0</scaled><formatted>Off</formatted></value></event>
</property>
<property id="CLIHUM" name="Humidity">
<event timestamp="t2"><value uom="22" precision="0"><scaled>45</scaled><formatted>45%</formatted></value></event>
</property>
</properties>
</node>
<node id="B">
<properties>
<property id="ST" name="Status">
<event timestamp="t3"><value uom="78" precision="0"><scaled>100</scaled><formatted>On</formatted></value></event>
</property>
</properties>
</node>
</history>"""
    groups = _parse_node_property_history_xml(xml)
    assert [(g["node"], g["property"]) for g in groups] == [("A", "ST"), ("A", "CLIHUM"), ("B", "ST")]


def test_parse_malformed_xml_raises_parse_error():
    import xml.etree.ElementTree as ET

    with pytest.raises(ET.ParseError):
        _parse_node_property_history_xml("not xml at all <<<")


# ------------------------------------------------------------------
# IoXWrapper.get_node_property_history / set_node_property_history_recording
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_history_unknown_device_is_a_clear_error():
    wrapper = _bare_wrapper({"A": _build_node("A", properties=["ST"])})
    result = await wrapper.get_node_property_history(["MISSING"], ["ST"])
    assert result["successful"] is False
    assert "MISSING" in result["data"]
    assert wrapper.get_calls == []  # never reached the HTTP call


@pytest.mark.asyncio
async def test_get_history_unknown_property_on_every_device_is_a_clear_error():
    wrapper = _bare_wrapper({"A": _build_node("A", properties=["ST"])})
    result = await wrapper.get_node_property_history(["A"], ["NoSuchProperty"])
    assert result["successful"] is False
    assert "NoSuchProperty" in result["data"]
    assert wrapper.get_calls == []


@pytest.mark.asyncio
async def test_get_history_property_valid_on_only_one_of_several_devices_still_resolves():
    # Same rule as get_property/resolve_property_id: a name valid on ANY of
    # the given devices is accepted, not required on all of them.
    wrapper = _bare_wrapper(
        {
            "A": _build_node("A", properties=["ST"]),
            "B": _build_node("B", properties=["Temperature"]),
        }
    )
    result = await wrapper.get_node_property_history(["A", "B"], ["ST"])
    assert result["successful"] is True
    assert wrapper.get_calls == ["/rest/history/node/properties/get?node=A%2CB&property=ST&limit=500"]


@pytest.mark.asyncio
async def test_get_history_builds_the_expected_query_string():
    wrapper = _bare_wrapper(
        {
            "A": _build_node("A", properties=["ST", "CLIHUM"]),
            "B": _build_node("B", properties=["ST"]),
        }
    )
    await wrapper.get_node_property_history(
        ["A", "B"],
        ["ST"],
        start="2026-01-01T00:00:00-08:00",
        end="2026-01-02T00:00:00-08:00",
        one_before=True,
        one_after=True,
        limit=50,
    )
    assert len(wrapper.get_calls) == 1
    path = wrapper.get_calls[0]
    assert path.startswith("/rest/history/node/properties/get?")
    # Cross-product with a single property name resolves to one property id
    # per device it's found on -- here "ST" exists on both A and B, so it
    # still de-dupes to one id in the query, not one per device.
    assert "node=A%2CB" in path
    assert "property=ST" in path
    assert "start=2026-01-01T00%3A00%3A00-08%3A00" in path
    assert "end=2026-01-02T00%3A00%3A00-08%3A00" in path
    assert "oneBefore=true" in path
    assert "oneAfter=true" in path
    assert "limit=50" in path


@pytest.mark.asyncio
async def test_get_history_omits_start_end_before_after_when_not_given():
    wrapper = _bare_wrapper({"A": _build_node("A", properties=["ST"])})
    await wrapper.get_node_property_history(["A"], ["ST"])
    path = wrapper.get_calls[0]
    assert "start=" not in path
    assert "end=" not in path
    assert "oneBefore" not in path
    assert "oneAfter" not in path


@pytest.mark.asyncio
async def test_get_history_non_200_response_is_a_clear_error_not_a_crash():
    wrapper = _bare_wrapper(
        {"A": _build_node("A", properties=["ST"])},
        response=SimpleNamespace(status_code=500, text=""),
    )
    result = await wrapper.get_node_property_history(["A"], ["ST"])
    assert result["successful"] is False
    assert "500" in result["data"]


@pytest.mark.asyncio
async def test_get_history_no_response_is_a_clear_error_not_a_crash():
    wrapper = _bare_wrapper({"A": _build_node("A", properties=["ST"])})

    async def fake_get_none(path):
        wrapper.get_calls.append(path)
        return None

    wrapper.get = fake_get_none
    result = await wrapper.get_node_property_history(["A"], ["ST"])
    assert result["successful"] is False


@pytest.mark.asyncio
async def test_get_history_non_xml_response_is_a_clear_error_not_a_crash():
    wrapper = _bare_wrapper(
        {"A": _build_node("A", properties=["ST"])},
        response=SimpleNamespace(status_code=200, text="not xml at all <<<"),
    )
    result = await wrapper.get_node_property_history(["A"], ["ST"])
    assert result["successful"] is False
    assert "non-XML" in result["data"]


@pytest.mark.asyncio
async def test_get_history_success_parses_the_real_xml_shape():
    wrapper = _bare_wrapper(
        {"A": _build_node("A", properties=["ST"])},
        response=SimpleNamespace(status_code=200, text=_LIVE_SAMPLE_XML),
    )
    result = await wrapper.get_node_property_history(["A"], ["ST"])
    assert result["successful"] is True
    assert result["data"][0]["node"] == "ZY008_1"
    assert result["data"][0]["property"] == "ST"
    assert len(result["data"][0]["history"]) == 2
    assert result["data"][0]["history"][0]["formatted"] == "Off"


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,suffix", [(True, "on"), (False, "off")])
async def test_set_recording_hits_the_right_on_off_url(enabled, suffix):
    wrapper = _bare_wrapper({})
    result = await wrapper.set_node_property_history_recording(enabled)
    assert result == {"successful": True, "enabled": enabled}
    assert wrapper.get_calls == [f"/rest/history/node/properties/recording/{suffix}"]


@pytest.mark.asyncio
async def test_set_recording_non_200_is_a_clear_error_not_a_crash():
    wrapper = _bare_wrapper({}, response=SimpleNamespace(status_code=403, text=""))
    result = await wrapper.set_node_property_history_recording(True)
    assert result["successful"] is False
    assert "403" in result["data"]
