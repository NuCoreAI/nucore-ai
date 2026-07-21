"""Devices/groups in the minimal DEVICE DATABASE surface disabled/error state
sparsely -- via top-level DISABLED/IN_ERROR id lists that only exist (and
only list the exceptions) when at least one device/group is abnormal, never
a per-device field paid for by every device regardless of state.
"""

from __future__ import annotations

from nucore.group import Group
from nucore.node import Node
from nucore.node_base import NodeHierarchy, NodeTypes
from nucore.nodedef import NodeCommands, NodeDef
from nucore.profile import RuntimeProfile
from rag.dedupe_profiles import DedupeProfiles
from rag.minimal_rag_formatter import MinimalRagFormatter


def _build_node(cls, address: str, name: str, *, enabled: bool = True, in_error: bool = False):
    node = object.__new__(cls)
    node.address = address
    node.name = name
    node.enabled = enabled
    node.flag = NodeTypes.NODE_IS_IN_ERR if in_error else 0
    node.parent = None
    node.parent_type = NodeHierarchy.UD_HIERARCHY_NODE_TYPE_NOTSET
    if cls is Group:
        node.members = {}
    return node


def _profile(node_def_id: str, nodes: list) -> RuntimeProfile:
    node_def = NodeDef(id=node_def_id, properties={}, cmds=NodeCommands(accepts=[], sends=[]))
    return RuntimeProfile(nodedef=node_def, nodes=set(nodes))


def test_format_profile_omits_status_keys_for_normal_device():
    node = _build_node(Node, "n001", "Kitchen Light")
    profile = _profile("dimmer", [node])
    result = MinimalRagFormatter(json_output=True)._format_profile(profile)
    (device,) = result["devices"]
    assert "disabled" not in device
    assert "error" not in device


def test_format_profile_flags_disabled_device():
    node = _build_node(Node, "n002", "Broken Sensor", enabled=False)
    profile = _profile("sensor", [node])
    result = MinimalRagFormatter(json_output=True)._format_profile(profile)
    (device,) = result["devices"]
    assert device["disabled"] is True
    assert "error" not in device


def test_format_profile_flags_error_device_and_group():
    node = _build_node(Node, "n003", "Faulty Lock", in_error=True)
    group = _build_node(Group, "g001", "Downstairs Scene", in_error=True)
    profile = _profile("lock", [node])
    group_profile = _profile("scene", [group])

    device_result = MinimalRagFormatter(json_output=True)._format_profile(profile)
    group_result = MinimalRagFormatter(json_output=True)._format_profile(group_profile)

    assert device_result["devices"][0]["error"] is True
    assert group_result["groups"][0]["error"] is True


def test_render_python_omits_tables_when_nothing_abnormal():
    data = {
        "profiles": [{"id": "dimmer", "devices": [{"id": "n001", "name": "Kitchen Light", "parent": "none"}]}],
        "folders": [],
    }
    out = DedupeProfiles.render_python(data)
    assert "DISABLED =" not in out
    assert "IN_ERROR =" not in out


def test_render_python_emits_sparse_disabled_and_error_tables():
    data = {
        "profiles": [
            {
                "id": "sensor",
                "devices": [
                    {"id": "n001", "name": "Fine Sensor", "parent": "none"},
                    {"id": "n002", "name": "Disabled Sensor", "parent": "none", "disabled": True},
                    {"id": "n003", "name": "Broken Sensor", "parent": "none", "error": True},
                    {"id": "n004", "name": "Both Sensor", "parent": "none", "disabled": True, "error": True},
                ],
            }
        ],
        "folders": [],
    }
    out = DedupeProfiles.render_python(data)
    assert "DISABLED = ['n002', 'n004']" in out
    assert "IN_ERROR = ['n003', 'n004']" in out
    disabled_line = next(line for line in out.splitlines() if line.startswith("DISABLED"))
    error_line = next(line for line in out.splitlines() if line.startswith("IN_ERROR"))
    # n001 (normal) never appears in either table.
    assert "n001" not in disabled_line
    assert "n001" not in error_line
