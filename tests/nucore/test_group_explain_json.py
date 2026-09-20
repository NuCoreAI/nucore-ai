"""Group.explain_json() -- "does this group/scene control anything" detection.

Root-caused against a live production complaint: a scene ("All backyard",
address 32902) whose /api/groups/links data has the group's OWN address as
the sole 'ctl' id (i.e. the group node itself is the controller of N
responder devices -- a standard Insteon scene shape) was reported as "This
is a collection but does not control anything else" despite genuinely
having 4 real device links, because explain_json() used len(self.members)
<= 1 as a proxy for "no real links" -- true here only because add_links()
enriches the pre-seeded container GroupMember (see Group.__init__) instead
of adding a new one, since the 'ctl' id equals the group's own address.
self.members never grows past 1 in this shape, regardless of how many real
links the container has.
"""

from __future__ import annotations

from types import SimpleNamespace

import xml.etree.ElementTree as ET

from nucore.group import Group


def _make_group(address="32902", name="All backyard") -> Group:
    elem = ET.fromstring(
        f"<node flag='132'><address>{address}</address><name>{name}</name>"
        f"<family><instance>1</instance></family><deviceGroup>5</deviceGroup></node>"
    )
    return Group(elem)


def _make_node(address, name):
    return SimpleNamespace(name=name, family=1, instance=1, address=address, node_def=SimpleNamespace(properties={}))


def test_group_is_its_own_controller_with_real_links_is_not_a_collection():
    g = _make_group()
    nodes = {
        "6 DE 4D 1": _make_node("6 DE 4D 1", "Backyard Dimmer"),
        "28 87 5C 1": _make_node("28 87 5C 1", "Pool and Jaccuzzi"),
    }
    links_root = {
        "id": "32902",
        "ctl": [
            {
                "id": "32902",  # the group's OWN address -- the reported bug shape
                "links": [
                    {"type": "native", "node": "6 DE 4D 1", "linkdef": None, "params": []},
                    {"type": "native", "node": "28 87 5C 1", "linkdef": None, "params": []},
                ],
            }
        ],
    }

    g.add_links(links_root, nodes, {"_unused": True})

    assert list(g.members.keys()) == ["32902"]  # confirms this is the exact reported shape
    result = g.explain_json()

    assert result["nucore_scene_activation"] != "This is a collection but does not control anything else"
    activation_labels = {label for entry in result["nucore_scene_activation"] for label in entry}
    assert "Backyard Dimmer [address=6 DE 4D 1]" in activation_labels
    assert "Pool and Jaccuzzi [address=28 87 5C 1]" in activation_labels


def test_group_with_no_links_at_all_is_still_a_collection():
    g = _make_group()

    result = g.explain_json()

    assert result == {"nucore_scene_activation": "This is a collection but does not control anything else"}


def test_group_is_its_own_controller_but_ctl_entry_has_no_links_is_still_a_collection():
    g = _make_group()
    links_root = {"id": "32902", "ctl": [{"id": "32902", "links": []}]}

    g.add_links(links_root, {}, {"_unused": True})

    result = g.explain_json()

    assert result == {"nucore_scene_activation": "This is a collection but does not control anything else"}


def test_cross_linked_member_devices_still_populate_controller_activation_map():
    """Regression guard for the OTHER shape (e.g. "MBR-HWY" in production):
    distinct member devices cross-linked to each other, not the group's own
    address -- self.members already grows past 1 here, so this shape never
    hit the bug. Confirms the fix didn't change this existing behavior."""
    g = _make_group(address="35676", name="MBR-HWY")
    nodes = {
        "1F 62 BC 1": _make_node("1F 62 BC 1", "MBR-HWY-KPL"),
        "F 18 8 3": _make_node("F 18 8 3", "MBR-KPLA-HWY"),
    }
    links_root = {
        "id": "35676",
        "ctl": [
            {"id": "1F 62 BC 1", "links": [{"type": "native", "node": "F 18 8 3", "linkdef": None, "params": []}]},
            {"id": "F 18 8 3", "links": [{"type": "native", "node": "1F 62 BC 1", "linkdef": None, "params": []}]},
        ],
    }

    g.add_links(links_root, nodes, {"_unused": True})

    assert len(g.members) == 3  # container + the two cross-linked devices
    result = g.explain_json()

    assert set(result["controller_activation_map"].keys()) == {
        "MBR-HWY-KPL [address=1F 62 BC 1]",
        "MBR-KPLA-HWY [address=F 18 8 3]",
    }
