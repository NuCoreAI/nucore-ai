"""INSTEONDiagnostics._quick_plm_sanity_check -- the real "expected" link
count: 2 records per primary Insteon node, 1 record per non-primary Insteon
node (a multi-button/multi-instance device's sub-node, per ``pnode`` --
NOT ``node.parent``/``parent_type``/``node_parent_is_node()``, which reflect
UI folder placement rather than Insteon sub-addressing and were confirmed to
over-count primaries against a real logged DEVICE DATABASE: genuine
sub-nodes placed in a UI folder rather than nested under their own primary
node), 1 record per group (flat, unfiltered by family), and 1 record per
Insteon-family group member. Insteon-family filtering on the node/membership
side keeps a mixed-protocol system (Insteon plus Z-Wave/Zigbee/etc.) from
being inflated by devices the PLM's link table has nothing to do with. No
existing test exercised this counting logic before -- every other test stubs
_quick_plm_sanity_check itself out (see test_insteon_diagnose.py/
test_plm_exclusion.py/test_run_diagnostics.py).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from iox.diagnostics.insteon_diag import INSTEONDiagnostics

_INSTEON = 1
_Z_WAVE = 12
_ZIGBEE = 14


def _links_blob(rows: list[str]) -> str:
    lines = ["Links Table\n", "```csv\n", "idx,role,group,device,data\n"]
    lines += [f"{row}\n" for row in rows]
    lines.append("```\n")
    return "".join(lines)


def _node(
    *,
    family: int = _INSTEON,
    primary: bool = True,
    pnode: str | None = None,
    address: str = "n",
    parent_type: str | None = None,
) -> SimpleNamespace:
    """``parent_type`` is accepted but never read by the real code under
    test -- only present here so a test can plant a misleading value and
    prove the fix doesn't accidentally consult it (see
    test_a_ui_folder_parented_sub_node_is_still_non_primary_via_pnode)."""
    return SimpleNamespace(
        family=family, address=address, pnode=pnode, parent_type=parent_type, node_is_device_primary=lambda: primary
    )


def _member(family: int = _INSTEON) -> SimpleNamespace:
    return SimpleNamespace(family=family)


def _group(*, family: int = _INSTEON, members: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(family=family, members=members or {})


def _diag_for_sanity_check(*, nodes: dict, groups: dict, plm_rows: list[str], plm_connected: bool = True):
    diag = object.__new__(INSTEONDiagnostics)
    diag._plm_connected = plm_connected
    diag._iox_wrapper = SimpleNamespace(nodes=nodes, groups=groups)

    async def _get_all_plm_links(**kwargs):
        return _links_blob(plm_rows)

    diag._get_all_plm_links = _get_all_plm_links
    return diag


@pytest.mark.asyncio
async def test_primary_only_insteon_system_matches_formula():
    nodes = {"n1": _node(address="n1"), "n2": _node(address="n2")}
    groups = {"g1": _group(members={"g1": _member(), "m1": _member()})}  # self + 1 real member, both Insteon
    # expected = 2*2 (primary nodes) + 0 (non-primary) + 1*1 (groups) + 2 (g1's 2 Insteon members) = 7
    plm_rows = [f"{i},controller,1,01 02 0{i},00" for i in range(7)]
    diag = _diag_for_sanity_check(nodes=nodes, groups=groups, plm_rows=plm_rows)

    result = await diag._quick_plm_sanity_check()

    assert result["passed"] is True
    assert "7" in result["report"]


@pytest.mark.asyncio
async def test_primary_and_non_primary_nodes_counted_differently():
    # One KeypadLinc-style device: 1 primary node + 2 non-primary button
    # nodes, each pointing back at the primary via pnode.
    nodes = {
        "n1": _node(address="n1", primary=True),
        "n1-2": _node(address="n1-2", primary=False, pnode="n1"),
        "n1-3": _node(address="n1-3", primary=False, pnode="n1"),
    }
    # expected = 2*1 (primary) + 1*2 (non-primary) + 0 (no groups) + 0 = 4
    plm_rows = [f"{i},controller,1,01 02 0{i},00" for i in range(4)]
    diag = _diag_for_sanity_check(nodes=nodes, groups={}, plm_rows=plm_rows)

    result = await diag._quick_plm_sanity_check()

    assert result["passed"] is True
    assert "primary Insteon nodes (1)" in result["report"]
    assert "non-primary Insteon nodes (2)" in result["report"]


@pytest.mark.asyncio
async def test_a_ui_folder_parented_sub_node_is_still_non_primary_via_pnode():
    # Regression test for the real bug: a genuine Insteon sub-node (has a
    # pnode) that the hub happens to place in a UI folder rather than
    # nesting under its own primary node (parent_type == 'folder', NOT
    # 'node'). A parent_type-based check would wrongly call this primary;
    # the pnode-based check correctly calls it non-primary regardless of
    # where the hub put it in the UI tree.
    nodes = {
        "n1": _node(address="n1", primary=True, parent_type="folder"),
        "n1-8": _node(address="n1-8", primary=False, pnode="n1", parent_type="folder"),
    }
    plm_rows = ["1,controller,1,01 02 03,00", "2,responder,1,01 02 03,00", "3,controller,1,01 02 03,00"]
    # expected = 2*1 (primary n1) + 1*1 (non-primary n1-8, via pnode) + 0 + 0 = 3
    diag = _diag_for_sanity_check(nodes=nodes, groups={}, plm_rows=plm_rows)

    result = await diag._quick_plm_sanity_check()

    assert result["passed"] is True
    assert "primary Insteon nodes (1)" in result["report"]
    assert "non-primary Insteon nodes (1)" in result["report"]


@pytest.mark.asyncio
async def test_a_node_with_no_pnode_is_treated_as_primary_even_if_the_flag_says_otherwise():
    # Defensive OR: node_is_device_primary() False but pnode is None (no
    # real parent) -- still counted as primary, not non-primary.
    nodes = {"n1": _node(address="n1", primary=False, pnode=None)}
    plm_rows = ["1,controller,1,01 02 03,00", "2,responder,1,01 02 03,00"]  # expected = 2*1 = 2
    diag = _diag_for_sanity_check(nodes=nodes, groups={}, plm_rows=plm_rows)

    result = await diag._quick_plm_sanity_check()

    assert result["passed"] is True
    assert "primary Insteon nodes (1)" in result["report"]
    assert "non-primary Insteon nodes (0)" in result["report"]


@pytest.mark.asyncio
async def test_mixed_protocol_system_is_no_longer_inflated_by_non_insteon_nodes():
    # 1 Insteon node, 3 non-Insteon nodes, no groups -- an unfiltered count
    # would have counted all 4 nodes, making a perfectly healthy 1-Insteon-
    # node PLM (2 link records) look like it was missing half its expected
    # links. The fix only counts the 1 Insteon node.
    nodes = {
        "n1": _node(family=_INSTEON, address="n1"),
        "n2": _node(family=_Z_WAVE, address="n2"),
        "n3": _node(family=_Z_WAVE, address="n3"),
        "n4": _node(family=_ZIGBEE, address="n4"),
    }
    plm_rows = ["1,controller,1,01 02 03,00", "2,responder,1,01 02 03,00"]  # expected = 2*1 = 2
    diag = _diag_for_sanity_check(nodes=nodes, groups={}, plm_rows=plm_rows)

    result = await diag._quick_plm_sanity_check()

    assert result["passed"] is True
    assert "primary Insteon nodes (1)" in result["report"]


@pytest.mark.asyncio
async def test_member_family_is_checked_per_member_not_via_the_group_s_own_family():
    # The group's own family is Z-Wave, but one of its members is Insteon --
    # that member must still be counted (member-level filtering), while the
    # group's own non-Insteon self-entry must not be.
    groups = {
        "g1": _group(family=_Z_WAVE, members={"g1": _member(family=_Z_WAVE), "m1": _member(family=_INSTEON)}),
    }
    # expected = 0 (nodes) + 1*1 (groups, flat) + 1 (only m1 is Insteon) = 2
    plm_rows = ["1,controller,1,01 02 03,00", "2,responder,1,01 02 03,00"]
    diag = _diag_for_sanity_check(nodes={}, groups=groups, plm_rows=plm_rows)

    result = await diag._quick_plm_sanity_check()

    assert result["passed"] is True
    assert "groups (1)" in result["report"]
    assert "Insteon node group memberships (1)" in result["report"]


@pytest.mark.asyncio
async def test_a_group_counts_as_1_regardless_of_its_own_family_even_with_no_insteon_members():
    # The group term is deliberately NOT family-filtered -- a non-Insteon
    # group with zero Insteon members still contributes 1 to `expected`.
    groups = {"g1": _group(family=_Z_WAVE, members={"g1": _member(family=_Z_WAVE)})}
    plm_rows = ["1,controller,1,01 02 03,00"]  # expected = 0 + 1 (groups) + 0 (no Insteon members) = 1
    diag = _diag_for_sanity_check(nodes={}, groups=groups, plm_rows=plm_rows)

    result = await diag._quick_plm_sanity_check()

    assert result["passed"] is True
    assert "groups (1)" in result["report"]
    assert "Insteon node group memberships (0)" in result["report"]


@pytest.mark.asyncio
async def test_boundary_just_inside_the_10_percent_tolerance_passes():
    nodes = {f"n{i}": _node(address=f"n{i}") for i in range(50)}  # expected = 2*50 = 100
    plm_rows = [f"{i},controller,1,01 02 03,00" for i in range(110)]  # diff_pct == 10.0%
    diag = _diag_for_sanity_check(nodes=nodes, groups={}, plm_rows=plm_rows)

    result = await diag._quick_plm_sanity_check()

    assert result["passed"] is True
    assert "10.0%" in result["report"]
    assert "within" in result["report"]


@pytest.mark.asyncio
async def test_boundary_just_outside_the_10_percent_tolerance_fails():
    nodes = {f"n{i}": _node(address=f"n{i}") for i in range(50)}  # expected = 2*50 = 100
    plm_rows = [f"{i},controller,1,01 02 03,00" for i in range(111)]  # diff_pct == 11.0%
    diag = _diag_for_sanity_check(nodes=nodes, groups={}, plm_rows=plm_rows)

    result = await diag._quick_plm_sanity_check()

    assert result["passed"] is False
    assert "11.0%" in result["report"]
    assert "OUTSIDE" in result["report"]
