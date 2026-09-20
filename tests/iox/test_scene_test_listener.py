"""scene_test's event-driven collection -- INSTEONDiagnostics's
``_stream_scene_test_into_file``/``scene_test`` (control "_7", action "1",
gap-timeout-only termination -- see design/iox_apis/subscription_events.md
for _7/"1" == UD_PROGRESS_EVENT_UPDATE), plus IoXDiagnostics.scene_test's
group/family gating in front of it.

Mirrors tests/iox/test_insteon_links_streaming.py's patterns: a minimal
concrete NuCoreInterface subclass standing in for IoXWrapper, and firing
events via ``_dispatch_event_listeners`` directly (no real websocket/
hardware involved).
"""

from __future__ import annotations

import asyncio
import os
import time
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest

from nucore.group import Group, GroupLink, GroupMember, GroupMemberType
from nucore.nucore_interface import NuCoreInterface
from iox.diagnostics.insteon_diag import (
    INSTEONDiagnostics,
    _dotted_insteon_address_to_nucore,
    _extract_std_cleanup_ack_devices,
    _strip_instance_suffix,
)
from iox.diagnostics.iox_diagnostics import IoXDiagnostics


class FakeIoXWrapper(NuCoreInterface):
    """Minimal concrete NuCoreInterface standing in for a real IoXWrapper --
    only the extra surface scene_test actually calls (post,
    _family_api_path) gets a real (stubbed) implementation; everything else
    is an unexercised NotImplementedError stub, same as
    test_insteon_links_streaming.py's FakeIoXWrapper."""

    def __init__(self):
        super().__init__(json_output=True, formatter_type="minimal")
        self.post_calls: list[tuple[str, str]] = []

    async def _load(self, **kwargs): raise NotImplementedError
    async def _load_routines(self): raise NotImplementedError
    async def _load_variables(self): raise NotImplementedError
    async def send_commands(self, commands): raise NotImplementedError
    async def create_automation_routine(self, routine): raise NotImplementedError
    async def update_routine(self, routine): raise NotImplementedError
    async def get_properties(self, device_id): raise NotImplementedError
    def get_device_name(self, device_id): return None
    def get_device_id(self, device_str): raise NotImplementedError
    async def get_all_routines_summary(self): raise NotImplementedError
    async def get_routine_summary(self, routine_id): raise NotImplementedError
    async def get_all_routines(self): raise NotImplementedError
    async def get_routine(self, routine_id): raise NotImplementedError
    async def add_node(self, node_name, type): raise NotImplementedError
    async def node_ops(self, node_id, operation, **kwargs): raise NotImplementedError
    async def routine_ops(self, routine_id, operation): raise NotImplementedError
    async def variable_ops(self, var_type, var_id, operation, **kwargs): raise NotImplementedError
    async def group_scene_add_member(self, *a, **kw): raise NotImplementedError
    async def group_scene_remove_member(self, *a, **kw): raise NotImplementedError
    async def group_scene_update_link(self, *a, **kw): raise NotImplementedError
    async def group_scene_get_node_roles(self, *a, **kw): raise NotImplementedError
    async def group_scene_get_link_types(self, *a, **kw): raise NotImplementedError
    async def scene_test(self, device_id): raise NotImplementedError
    async def add_device(self, device_address, **kwargs): raise NotImplementedError
    async def discover_devices(self, protocol=None, mode="include", **kwargs): raise NotImplementedError
    async def finish_device_discovery(self, protocol=None, **kwargs): raise NotImplementedError
    async def remove_device(self, device_address, protocol=None, **kwargs): raise NotImplementedError
    async def _subscribe_events(self, on_message_callback, on_connect_callback=None, on_disconnect_callback=None):
        raise NotImplementedError

    async def post(self, path, body, headers=None):
        self.post_calls.append((path, body))
        return _FakeResponse(200)

    def _family_api_path(self, suffix, family=None, instance=None):
        return suffix


class _FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code


def _progress_event(value=1):
    return {"value": value}


def _make_group(device_group="5", family="1", address="SCENE1"):
    xml = (
        f'<group flag="4"><address>{address}</address><name>Test Scene</name>'
        f"<family>{family}</family>"
        + (f"<deviceGroup>{device_group}</deviceGroup>" if device_group is not None else "")
        + "</group>"
    )
    return Group(ET.fromstring(xml))


def _add_responder(group: Group, address: str, name: str) -> None:
    """Add a real scene member the way Group.add_links actually does for the
    standard "group is its own controller" shape: as a GroupLink on the
    container GroupMember's own .links, not as a separate group.members
    entry -- see _process_scene_test_file's docstring for why."""
    container = group.members[group.address]
    container.links[address] = GroupLink(node=SimpleNamespace(address=address, name=name))


# ---------------------------------------------------------------------------
# _stream_scene_test_into_file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_scene_test_collects_events_until_quiet(tmp_path):
    wrapper = FakeIoXWrapper()
    diag = INSTEONDiagnostics(wrapper)
    file_path = str(tmp_path / "scene_test.txt")

    async def trigger():
        return None

    async def fire_two_events():
        await asyncio.sleep(0.01)
        wrapper._dispatch_event_listeners("_7", "1", "dev1", _progress_event(1))
        await asyncio.sleep(0.01)
        wrapper._dispatch_event_listeners("_7", "1", "dev2", _progress_event(2))

    asyncio.create_task(fire_two_events())

    start = time.monotonic()
    event_count = await diag._stream_scene_test_into_file(file_path, trigger, max_gap_timeout=0.15)
    elapsed = time.monotonic() - start

    assert event_count == 2
    assert elapsed < 1.0  # ended on the gap timeout after the 2nd event, not some larger ceiling
    lines = [line for line in open(file_path).read().splitlines() if line]
    assert len(lines) == 2
    assert wrapper._event_listeners == {}


@pytest.mark.asyncio
async def test_stream_scene_test_timeout_with_zero_events(tmp_path):
    wrapper = FakeIoXWrapper()
    diag = INSTEONDiagnostics(wrapper)
    file_path = str(tmp_path / "scene_test.txt")

    async def trigger():
        return None

    event_count = await diag._stream_scene_test_into_file(file_path, trigger, max_gap_timeout=0.05)

    assert event_count == 0
    assert not os.path.exists(file_path)
    assert wrapper._event_listeners == {}


@pytest.mark.asyncio
async def test_stream_scene_test_ignores_events_for_a_different_control_or_action(tmp_path):
    wrapper = FakeIoXWrapper()
    diag = INSTEONDiagnostics(wrapper)
    file_path = str(tmp_path / "scene_test.txt")

    async def trigger():
        return None

    async def fire_wrong_events():
        await asyncio.sleep(0.01)
        wrapper._dispatch_event_listeners("_7", "2", "dev1", _progress_event())  # wrong action
        wrapper._dispatch_event_listeners("_5", "1", "dev1", _progress_event())  # wrong control

    asyncio.create_task(fire_wrong_events())

    event_count = await diag._stream_scene_test_into_file(file_path, trigger, max_gap_timeout=0.1)

    assert event_count == 0
    assert not os.path.exists(file_path)


@pytest.mark.asyncio
async def test_stream_scene_test_unregisters_listener_even_if_trigger_raises(tmp_path):
    wrapper = FakeIoXWrapper()
    diag = INSTEONDiagnostics(wrapper)
    file_path = str(tmp_path / "scene_test.txt")

    async def trigger():
        raise RuntimeError("network exploded")

    event_count = await diag._stream_scene_test_into_file(file_path, trigger, max_gap_timeout=0.05)

    assert event_count == 0  # trigger's exception never propagated or affected the outcome
    assert wrapper._event_listeners == {}


# ---------------------------------------------------------------------------
# INSTEONDiagnostics.scene_test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insteon_scene_test_posts_raw_off_and_returns_result(tmp_path, monkeypatch):
    wrapper = FakeIoXWrapper()
    diag = INSTEONDiagnostics(wrapper)
    expected_path = str(tmp_path / "scene_test_SCENE1.txt")
    monkeypatch.setattr(diag, "_get_scene_test_file_path", lambda device_id: expected_path)

    async def fake_stream(file_path, trigger, max_gap_timeout=None):
        await trigger()  # exercise the real trigger (the raw-off POST) for this test
        return 2

    monkeypatch.setattr(diag, "_stream_scene_test_into_file", fake_stream)
    group = _make_group()

    result = await diag.scene_test("SCENE1", 5, group)

    assert result["successful"] is True
    assert result["event_count"] == 2
    assert result["file_path"] == expected_path
    assert len(wrapper.post_calls) == 1
    path, body = wrapper.post_calls[0]
    assert path == "scene-test/raw-off"
    assert '"physicalGroupNum": 5' in body


# ---------------------------------------------------------------------------
# _extract_std_cleanup_ack_devices / _dotted_insteon_address_to_nucore
# ---------------------------------------------------------------------------


def test_extract_std_cleanup_ack_devices_finds_the_address_between_bracket_and_arrow():
    events = ["some prefix [Std-Cleanup Ack] 12.34.56 --> 11.22.33 0"]
    assert _extract_std_cleanup_ack_devices(events) == ["12.34.56"]


def test_extract_std_cleanup_ack_devices_ignores_lines_without_the_marker():
    events = ["12.34.56 1 GRP-RX   2 1", "some irrelevant progress noise"]
    assert _extract_std_cleanup_ack_devices(events) == []


def test_extract_std_cleanup_ack_devices_skips_a_malformed_line_missing_the_arrow():
    events = ["[Std-Cleanup Ack] 12.34.56 no arrow here"]
    assert _extract_std_cleanup_ack_devices(events) == []


def test_extract_std_cleanup_ack_devices_collects_multiple_matches():
    events = [
        "[Std-Cleanup Ack] 12.34.56 --> 11.22.33 0",
        "irrelevant noise",
        "[Std-Cleanup Ack] AA.BB.CC --> 11.22.33 0",
    ]
    assert _extract_std_cleanup_ack_devices(events) == ["12.34.56", "AA.BB.CC"]


def test_dotted_insteon_address_to_nucore_drops_leading_zeros_per_octet():
    assert _dotted_insteon_address_to_nucore("0F.18.08") == "F 18 8"


def test_dotted_insteon_address_to_nucore_no_leading_zeros_present():
    assert _dotted_insteon_address_to_nucore("12.34.56") == "12 34 56"


def test_strip_instance_suffix_drops_the_trailing_single_digit():
    assert _strip_instance_suffix("F 18 8 1") == "F 18 8"


# ---------------------------------------------------------------------------
# _process_scene_test_file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_scene_test_file_details_keeps_only_matching_keywords(tmp_path):
    wrapper = FakeIoXWrapper()
    diag = INSTEONDiagnostics(wrapper)
    file_path = str(tmp_path / "scene_test.txt")
    group = _make_group()
    lines = [
        diag.format_scene_test_event("dev1", "12.34.56 1 INST-SRX   2 1 62 06.07.08 06.07.08 0"),
        diag.format_scene_test_event("dev2", "some irrelevant progress noise"),
        diag.format_scene_test_event("dev3", "12.34.56 1 Std-Cleanup Ack"),
        diag.format_scene_test_event(None, "12.34.56 1 GRP-RX   2 1"),
        diag.format_scene_test_event("dev4", "12.34.56 1 CLEAN-UP-RPT 01 00"),
    ]
    with open(file_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    result = await diag._process_scene_test_file(file_path, event_count=5, group=group)

    assert result["event_count"] == 5  # unfiltered count passed through unchanged
    assert result["details"] == [
        "12.34.56 1 INST-SRX   2 1 62 06.07.08 06.07.08 0",
        "12.34.56 1 Std-Cleanup Ack",
        "12.34.56 1 GRP-RX   2 1",
        "12.34.56 1 CLEAN-UP-RPT 01 00",
    ]


@pytest.mark.asyncio
async def test_process_scene_test_file_ignores_the_non_json_header_line(tmp_path):
    wrapper = FakeIoXWrapper()
    diag = INSTEONDiagnostics(wrapper)
    file_path = str(tmp_path / "scene_test.txt")
    group = _make_group()
    with open(file_path, "w") as f:
        f.write("Scene Test for SCENE1 (physical group 5)\n")
        f.write(diag.format_scene_test_event("dev1", "12.34.56 1 GRP-RX 2 1") + "\n")

    result = await diag._process_scene_test_file(file_path, event_count=1, group=group)

    assert result["details"] == ["12.34.56 1 GRP-RX 2 1"]


@pytest.mark.asyncio
async def test_process_scene_test_file_returns_empty_details_with_no_matches(tmp_path):
    wrapper = FakeIoXWrapper()
    diag = INSTEONDiagnostics(wrapper)
    file_path = str(tmp_path / "scene_test.txt")
    group = _make_group()
    with open(file_path, "w") as f:
        f.write(diag.format_scene_test_event("dev1", "irrelevant noise") + "\n")

    result = await diag._process_scene_test_file(file_path, event_count=1, group=group)

    assert result["details"] == []
    assert result["event_count"] == 1


@pytest.mark.asyncio
async def test_process_scene_test_file_summary_marks_responding_and_silent_members(tmp_path):
    wrapper = FakeIoXWrapper()
    diag = INSTEONDiagnostics(wrapper)
    file_path = str(tmp_path / "scene_test.txt")
    group = _make_group(address="SCENE1")
    _add_responder(group, "12 34 56 1", "Lamp")
    _add_responder(group, "99 88 77 1", "Sensor")
    line = diag.format_scene_test_event("n1", "some prefix [Std-Cleanup Ack] 12.34.56 --> 11.22.33 0")
    with open(file_path, "w") as f:
        f.write(line + "\n")

    result = await diag._process_scene_test_file(file_path, event_count=1, group=group)

    assert result["summary"] == [
        {"name": "Lamp", "address": "12 34 56 1", "status": "success"},
        {"name": "Sensor", "address": "99 88 77 1", "status": "failure"},
    ]
    assert result["note"] == diag._SCENE_TEST_NO_RESPONSE_NOTE


@pytest.mark.asyncio
async def test_process_scene_test_file_note_appears_once_even_with_multiple_failures(tmp_path):
    wrapper = FakeIoXWrapper()
    diag = INSTEONDiagnostics(wrapper)
    file_path = str(tmp_path / "scene_test.txt")
    group = _make_group(address="SCENE1")
    _add_responder(group, "11 11 11 1", "A")
    _add_responder(group, "22 22 22 1", "B")
    with open(file_path, "w") as f:
        f.write(diag.format_scene_test_event("n1", "irrelevant -- nothing acked") + "\n")

    result = await diag._process_scene_test_file(file_path, event_count=1, group=group)

    assert [m["status"] for m in result["summary"]] == ["failure", "failure"]
    assert all("note" not in m for m in result["summary"])
    assert result["note"] == diag._SCENE_TEST_NO_RESPONSE_NOTE


@pytest.mark.asyncio
async def test_process_scene_test_file_no_note_when_all_members_succeed(tmp_path):
    wrapper = FakeIoXWrapper()
    diag = INSTEONDiagnostics(wrapper)
    file_path = str(tmp_path / "scene_test.txt")
    group = _make_group(address="SCENE1")
    _add_responder(group, "12 34 56 1", "Lamp")
    line = diag.format_scene_test_event("n1", "some prefix [Std-Cleanup Ack] 12.34.56 --> 11.22.33 0")
    with open(file_path, "w") as f:
        f.write(line + "\n")

    result = await diag._process_scene_test_file(file_path, event_count=1, group=group)

    assert result["summary"] == [{"name": "Lamp", "address": "12 34 56 1", "status": "success"}]
    assert "note" not in result


@pytest.mark.asyncio
async def test_process_scene_test_file_summary_is_empty_with_no_real_responders(tmp_path):
    wrapper = FakeIoXWrapper()
    diag = INSTEONDiagnostics(wrapper)
    file_path = str(tmp_path / "scene_test.txt")
    group = _make_group(address="SCENE1")  # Group.__init__ seeds members["SCENE1"] as the container, no links
    with open(file_path, "w") as f:
        f.write(diag.format_scene_test_event("n1", "irrelevant") + "\n")

    result = await diag._process_scene_test_file(file_path, event_count=1, group=group)

    assert result["summary"] == []


@pytest.mark.asyncio
async def test_process_scene_test_file_ignores_group_members_not_in_the_containers_links(tmp_path):
    """A device cross-linked to another device (its own group.members entry,
    per Group.add_links) isn't necessarily one of the scene's own real
    responders -- only group.members[group.address].links counts, per
    _process_scene_test_file's docstring."""
    wrapper = FakeIoXWrapper()
    diag = INSTEONDiagnostics(wrapper)
    file_path = str(tmp_path / "scene_test.txt")
    group = _make_group(address="SCENE1")
    _add_responder(group, "12 34 56 1", "Lamp")
    group.members["99 88 77 1"] = GroupMember(address="99 88 77 1", name="Cross-linked Device", type=GroupMemberType.MEMBER_IS_CONTROLLER)
    line = diag.format_scene_test_event("n1", "some prefix [Std-Cleanup Ack] 12.34.56 --> 11.22.33 0")
    with open(file_path, "w") as f:
        f.write(line + "\n")

    result = await diag._process_scene_test_file(file_path, event_count=1, group=group)

    assert result["summary"] == [{"name": "Lamp", "address": "12 34 56 1", "status": "success"}]


# ---------------------------------------------------------------------------
# IoXDiagnostics.scene_test -- group/family gating
# ---------------------------------------------------------------------------


class _FakeGateWrapper:
    def __init__(self, node=None):
        self._node = node

    def get_node(self, device_id):
        return self._node


class _FakeInsteonDiag:
    def __init__(self):
        self.calls: list[tuple[str, int, Group]] = []

    async def scene_test(self, device_id, physical_group_num, group):
        self.calls.append((device_id, physical_group_num, group))
        return {
            "successful": True,
            "file_path": "/tmp/scene_test_SCENE1.txt",
            "event_count": 2,
            "summary": [],
            "details": [],
        }


@pytest.mark.asyncio
async def test_scene_test_rejects_non_group_node():
    diag = IoXDiagnostics(_FakeGateWrapper(node=object()))

    result = await diag.scene_test("dev1")

    assert result == {"successful": False, "error": "not a group, or no physical group assigned"}


@pytest.mark.asyncio
async def test_scene_test_rejects_group_with_no_device_group():
    group = _make_group(device_group=None)
    diag = IoXDiagnostics(_FakeGateWrapper(node=group))

    result = await diag.scene_test("SCENE1")

    assert result == {"successful": False, "error": "not a group, or no physical group assigned"}


@pytest.mark.asyncio
async def test_scene_test_delegates_to_insteon_diag_on_success():
    group = _make_group(device_group="5", family="1")
    diag = IoXDiagnostics(_FakeGateWrapper(node=group))
    fake_insteon_diag = _FakeInsteonDiag()
    diag._insteon_diag = fake_insteon_diag  # pre-set so _init_insteon_diag(None) skips construction

    result = await diag.scene_test("SCENE1")

    assert result["successful"] is True
    assert fake_insteon_diag.calls == [("SCENE1", 5, group)]


@pytest.mark.asyncio
async def test_scene_test_brackets_the_call_with_debug_level_3_then_0():
    group = _make_group(device_group="5", family="1")
    diag = IoXDiagnostics(_FakeGateWrapper(node=group))
    diag._insteon_diag = _FakeInsteonDiag()

    levels: list[int] = []

    async def _fake_set_debug_level(level):
        levels.append(level)
        return {"successful": True}

    diag.set_debug_level = _fake_set_debug_level

    await diag.scene_test("SCENE1")

    assert levels == [3, 0]


@pytest.mark.asyncio
async def test_scene_test_resets_debug_level_even_if_insteon_diag_raises():
    group = _make_group(device_group="5", family="1")
    diag = IoXDiagnostics(_FakeGateWrapper(node=group))

    class _RaisingInsteonDiag:
        async def scene_test(self, device_id, physical_group_num, group):
            raise RuntimeError("boom")

    diag._insteon_diag = _RaisingInsteonDiag()

    levels: list[int] = []

    async def _fake_set_debug_level(level):
        levels.append(level)
        return {"successful": True}

    diag.set_debug_level = _fake_set_debug_level

    with pytest.raises(RuntimeError):
        await diag.scene_test("SCENE1")

    assert levels == [3, 0]
