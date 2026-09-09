"""IoXWrapper.node_ops's "delete" branch -- confirms the guard added alongside
Z-Wave/Zigbee/Matter pairing/exclusion support: deleting a Z-Wave/Zigbee/
Matter or plugin-backed node is rejected with a redirect message instead of
issuing the real REST DELETE, while an Insteon device (and groups/folders,
which have no real protocol family) still delete exactly as before.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from iox.iox_definitions import (
    DEVICE_FAMILY_INSTEON,
    DEVICE_FAMILY_LEGACY_Z_WAVE,
    DEVICE_FAMILY_MATTER,
    DEVICE_FAMILY_PLUGIN,
    DEVICE_FAMILY_Z_WAVE,
    DEVICE_FAMILY_ZIGBEE,
)
from iox.iox_wrapper import IoXWrapper


def _bare_wrapper(node) -> IoXWrapper:
    # Bypasses __init__ -- node_ops' delete branch only ever touches
    # self.nodes (via _get_node_type/_get_node_family) and self.delete.
    wrapper = object.__new__(IoXWrapper)
    wrapper.nodes = {"n1": node} if node is not None else {}
    wrapper.delete_calls: list[tuple] = []

    async def fake_delete(path, body=None, headers=None):
        wrapper.delete_calls.append((path, body, headers))
        return SimpleNamespace(status_code=200)

    wrapper.delete = fake_delete
    return wrapper


def _fake_node(family: str, name: str = "Some Device"):
    return SimpleNamespace(family=family, name=name)


@pytest.mark.asyncio
async def test_delete_insteon_device_still_issues_real_delete():
    wrapper = _bare_wrapper(_fake_node(DEVICE_FAMILY_INSTEON))
    result = await wrapper.node_ops("n1", "delete")
    assert wrapper.delete_calls
    assert getattr(result, "status_code", None) == 200


@pytest.mark.parametrize("family", [DEVICE_FAMILY_LEGACY_Z_WAVE, DEVICE_FAMILY_Z_WAVE])
@pytest.mark.asyncio
async def test_delete_zwave_device_is_rejected_with_activation_window_language(family):
    # zwave removal is a real two-step activation window -- the redirect
    # must tell the model to have the customer activate the device and
    # commit with finish_exclusion, not claim the removal is already done.
    wrapper = _bare_wrapper(_fake_node(family))
    result = await wrapper.node_ops("n1", "delete")
    assert not wrapper.delete_calls
    assert isinstance(result, str)
    assert "pair_device" in result
    assert "zwave" in result
    assert "start_exclusion" in result
    assert "activate" in result
    assert "finish_exclusion" in result


@pytest.mark.parametrize("family,protocol", [(DEVICE_FAMILY_ZIGBEE, "zigbee"), (DEVICE_FAMILY_MATTER, "matter")])
@pytest.mark.asyncio
async def test_delete_zigbee_matter_device_is_rejected_without_activation_window_language(family, protocol):
    # zigbee/matter removal is one direct call -- the redirect must NOT tell
    # the model to put the hub in "removal mode" or have the customer
    # activate anything, and must not mention finish_exclusion as a
    # required follow-up (that was the actual bug: this message used to say
    # "removal mode"/"activate"/"finish_exclusion to commit" for every
    # protocol alike, so the model parroted zwave's two-step language for
    # zigbee/matter too).
    wrapper = _bare_wrapper(_fake_node(family))
    result = await wrapper.node_ops("n1", "delete")
    assert not wrapper.delete_calls
    assert isinstance(result, str)
    assert "pair_device" in result
    assert protocol in result
    assert "start_exclusion" in result
    assert "removal mode" not in result
    assert "activate" not in result
    assert "finish_exclusion to commit" not in result
    assert "no finish_exclusion" in result
    assert "immediately" in result


@pytest.mark.asyncio
async def test_delete_plugin_device_is_rejected():
    wrapper = _bare_wrapper(_fake_node(DEVICE_FAMILY_PLUGIN, name="My Plugin Device"))
    result = await wrapper.node_ops("n1", "delete")
    assert not wrapper.delete_calls
    assert isinstance(result, str)
    assert "My Plugin Device" in result
    assert "delete_plugin" in result
    assert "list_installed_plugins" in result


@pytest.mark.asyncio
async def test_delete_group_without_family_attr_still_issues_real_delete():
    # Groups/folders don't carry a real protocol family -- _get_node_type
    # only sets type == "node" for genuine devices, so the family guard
    # never triggers for these regardless of what's on the object.
    wrapper = _bare_wrapper(None)
    wrapper.nodes = {}
    wrapper.groups = {"n1": SimpleNamespace(family=DEVICE_FAMILY_INSTEON, name="Some Group")}
    wrapper.folders = {}
    result = await wrapper.node_ops("n1", "delete")
    assert wrapper.delete_calls
    assert getattr(result, "status_code", None) == 200
