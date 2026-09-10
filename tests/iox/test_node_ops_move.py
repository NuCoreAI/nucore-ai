"""IoXWrapper.node_ops's "move" branch -- confirms the fix for moving a node
to the top level/root, traced from a real transcript where the model tried
new_parent_id='' (rejected as missing) and new_parent_id='none' (rejected as
an unresolvable node id, since 'none' is only the *read-side* sentinel the
system prompt uses to display an already-top-level item, not a real id).

Moving to a real, resolvable parent id is unchanged: resolves the parent's
type via _get_node_type and sends {'parentAddress', 'parentNodeType'} both.
Moving to the top level (new_parent_id empty/omitted) skips _get_node_type
entirely and sends an empty parentAddress with parentNodeType omitted --
confirmed against eisy-ui's own "Remove From Folder" action, which sends
this exact shape for the hub's real "clear the parent" signal.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from iox.iox_wrapper import IoXWrapper


def _bare_wrapper(node, *, groups=None, folders=None) -> IoXWrapper:
    # Bypasses __init__ -- node_ops' move branch only ever touches
    # self.nodes/self.groups/self.folders (via _get_node_type) and self.patch.
    wrapper = object.__new__(IoXWrapper)
    wrapper.nodes = {"n1": node} if node is not None else {}
    wrapper.groups = groups or {}
    wrapper.folders = folders or {}
    wrapper.patch_calls: list[tuple] = []

    async def fake_patch(path, body=None, headers=None):
        wrapper.patch_calls.append((path, body, headers))
        return SimpleNamespace(status_code=200)

    wrapper.patch = fake_patch
    return wrapper


def _fake_node(name: str = "Some Device"):
    return SimpleNamespace(name=name)


@pytest.mark.asyncio
async def test_move_to_a_real_parent_still_resolves_and_sends_its_type():
    wrapper = _bare_wrapper(_fake_node(), folders={"f1": _fake_node("Upstairs")})
    result = await wrapper.node_ops("n1", "move", new_parent_id="f1")

    assert getattr(result, "status_code", None) == 200
    assert len(wrapper.patch_calls) == 1
    path, body, _headers = wrapper.patch_calls[0]
    assert path == "/api/nodes/n1/"
    assert json.loads(body) == {"nodeType": "node", "parentAddress": "f1", "parentNodeType": "folder"}


@pytest.mark.asyncio
async def test_move_with_empty_new_parent_id_moves_to_root_without_resolving_a_type():
    wrapper = _bare_wrapper(_fake_node())
    result = await wrapper.node_ops("n1", "move", new_parent_id="")

    assert getattr(result, "status_code", None) == 200
    assert len(wrapper.patch_calls) == 1
    path, body, _headers = wrapper.patch_calls[0]
    assert path == "/api/nodes/n1/"
    sent = json.loads(body)
    assert sent == {"nodeType": "node", "parentAddress": ""}
    assert "parentNodeType" not in sent


@pytest.mark.asyncio
async def test_move_with_no_new_parent_id_kwarg_at_all_also_moves_to_root():
    wrapper = _bare_wrapper(_fake_node())
    result = await wrapper.node_ops("n1", "move")

    assert getattr(result, "status_code", None) == 200
    sent = json.loads(wrapper.patch_calls[0][1])
    assert sent == {"nodeType": "node", "parentAddress": ""}


@pytest.mark.asyncio
async def test_move_to_an_unresolvable_parent_id_still_errors():
    # Confirms this fix only changes behavior for "no id at all" -- a
    # genuinely bad id (e.g. the model guessing 'none' as a placeholder,
    # confusing the read-side top-level sentinel for a real id) must still
    # be rejected, not silently treated as root.
    wrapper = _bare_wrapper(_fake_node())
    result = await wrapper.node_ops("n1", "move", new_parent_id="none")

    assert not wrapper.patch_calls
    assert result == "Node not found: none"
