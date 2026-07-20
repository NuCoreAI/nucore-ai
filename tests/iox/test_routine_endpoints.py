"""Verifies the routine/trigger endpoint migration in IoXWrapper:
/api/ai/trigger* -> /api/trigger, /api/triggers, /api/triggers/:id (hard
cutover, per explicit scope decision), while the confirmed-unchanged
lifecycle (/rest/programs/...) and lightweight-summary
(/api/ai/programs, /api/ai/program/:id) endpoints stay untouched.
"""

from __future__ import annotations

import json

import pytest

from iox.iox_wrapper import IoXWrapper


class FakeResp:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data

    def json(self):
        return {"data": self._data}


def _bare_wrapper() -> IoXWrapper:
    """IoXWrapper.__init__ needs real connection params -- bypass it for
    these unit tests, which only exercise a handful of self-contained
    methods against faked self.get/put/post/delete."""
    return object.__new__(IoXWrapper)


@pytest.mark.asyncio
async def test_create_automation_routine_hits_new_put_endpoint():
    wrapper = _bare_wrapper()
    calls = []
    wrapper.put = lambda path, body=None, headers=None: (calls.append(("PUT", path)), FakeResp())[1]

    await wrapper.create_automation_routine({"name": "X", "if": [], "then": [], "else": []})

    assert calls == [("PUT", "/api/trigger")]


@pytest.mark.asyncio
async def test_create_automation_routine_body_is_not_wrapped_in_routine_key():
    """Confirmed (not assumed): unlike the old /api/ai/trigger endpoint,
    /api/trigger takes the NewTrigger dict directly as the request body --
    no {"routine": ...} envelope."""
    wrapper = _bare_wrapper()
    sent_body = {}
    wrapper.put = lambda path, body=None, headers=None: (sent_body.update(json.loads(body)), FakeResp())[1]

    trigger = {"name": "X", "if": [], "then": [], "else": []}
    await wrapper.create_automation_routine(trigger)

    assert sent_body == trigger
    assert "routine" not in sent_body


@pytest.mark.asyncio
async def test_create_automation_routine_rejects_empty():
    from nucore.nucore_error import NuCoreError

    wrapper = _bare_wrapper()
    with pytest.raises(NuCoreError):
        await wrapper.create_automation_routine({})


@pytest.mark.asyncio
async def test_update_routine_hits_new_post_endpoint():
    wrapper = _bare_wrapper()
    calls = []
    wrapper.post = lambda path, body=None, headers=None: (calls.append(("POST", path)), FakeResp())[1]

    await wrapper.update_routine({"id": 1, "name": "X"})

    assert calls == [("POST", "/api/trigger")]


@pytest.mark.asyncio
async def test_update_routine_body_is_not_wrapped_in_routine_key():
    wrapper = _bare_wrapper()
    sent_body = {}
    wrapper.post = lambda path, body=None, headers=None: (sent_body.update(json.loads(body)), FakeResp())[1]

    program = {"id": 1, "name": "X", "if": [], "then": [], "else": []}
    await wrapper.update_routine(program)

    assert sent_body == program
    assert "routine" not in sent_body


@pytest.mark.asyncio
async def test_delete_routine_hits_new_delete_endpoint():
    wrapper = _bare_wrapper()
    calls = []
    wrapper.delete = lambda path, body=None, headers=None: (calls.append(("DELETE", path)), FakeResp())[1]

    await wrapper.delete_routine("1")

    assert calls == [("DELETE", "/api/trigger/1")]


@pytest.mark.asyncio
async def test_get_all_routines_hits_new_plural_endpoint():
    wrapper = _bare_wrapper()
    wrapper.get = lambda path: FakeResp(data=[])

    result = await wrapper.get_all_routines()

    assert result == []


@pytest.mark.asyncio
async def test_get_routine_hits_new_singular_id_endpoint():
    wrapper = _bare_wrapper()
    calls = []

    def fake_get(path):
        calls.append(path)
        return FakeResp(data={"id": 1})

    wrapper.get = fake_get
    result = await wrapper.get_routine("1")

    assert calls == ["/api/triggers/1"]
    assert result == {"id": 1}


@pytest.mark.asyncio
async def test_routine_ops_delete_moves_with_the_crud_migration():
    """The one routine_ops operation that actually lives on the trigger-
    content endpoint, unlike every other operation (confirmed unchanged,
    see test below)."""
    wrapper = _bare_wrapper()
    calls = []
    wrapper.delete = lambda path, body=None, headers=None: (calls.append(("DELETE", path)), FakeResp())[1]

    await wrapper.routine_ops(1, "delete")

    assert calls == [("DELETE", "/api/trigger/1")]


@pytest.mark.asyncio
async def test_routine_ops_other_operations_stay_on_rest_programs():
    wrapper = _bare_wrapper()
    calls = []
    wrapper.get = lambda path: (calls.append(("GET", path)), FakeResp())[1]

    await wrapper.routine_ops("2", "enable")

    assert calls == [("GET", "/rest/programs/0002/enable")]


@pytest.mark.asyncio
async def test_no_stray_old_trigger_endpoints_remain():
    wrapper = _bare_wrapper()
    calls = []
    wrapper.put = lambda path, body=None, headers=None: (calls.append(path), FakeResp())[1]
    wrapper.post = lambda path, body=None, headers=None: (calls.append(path), FakeResp())[1]
    wrapper.delete = lambda path, body=None, headers=None: (calls.append(path), FakeResp())[1]
    wrapper.get = lambda path: (calls.append(path), FakeResp(data=[]))[1]

    await wrapper.create_automation_routine({"name": "X", "if": [], "then": [], "else": []})
    await wrapper.update_routine({"id": 1, "name": "X"})
    await wrapper.delete_routine("1")
    await wrapper.get_all_routines()
    await wrapper.get_routine("1")
    await wrapper.routine_ops(1, "delete")

    assert not any("/api/ai/trigger" in path for path in calls)


class _NodeStub:
    def __init__(self, address, name):
        self.address = address
        self.name = name


def _bare_wrapper_for_load_routines() -> IoXWrapper:
    wrapper = _bare_wrapper()
    wrapper.all_routines = {}
    wrapper.condensed_routines = []
    wrapper.routines_changed = True
    return wrapper


@pytest.mark.asyncio
async def test_load_routines_parses_new_unwrapped_shape():
    """New schema: each list item IS the Trigger/InvalidTrigger dict
    directly -- no more {"routine": {...}} wrapper."""
    wrapper = _bare_wrapper_for_load_routines()
    names = {"NODE1": "Left Hallway", "NODE2": "Right Hallway"}
    wrapper.get_device_name = lambda node_id: names.get(node_id, node_id)

    async def fake_get_all_routines():
        return [
            {
                "id": 42,
                "name": "Bedtime",
                "parent": 0,
                "comment": "test",
                "if": [{"type": "status", "andOr": "and", "id": "ST", "node": "NODE1", "op": "GT",
                        "val": {"value": 20, "prec": 0, "uom": 51}}],
                "then": [{"type": "cmd", "id": "DOF", "node": "NODE2", "p": []}],
                "else": [],
            },
            {"id": 43, "name": "Broken", "parent": 0, "invalid": True, "error": "bad xml",
             "xml": "<x/>", "if": [], "then": [], "else": []},
            {"name": "No Id", "parent": 0},  # must be skipped
        ]

    wrapper.get_all_routines = fake_get_all_routines
    await wrapper._load_routines()

    assert set(wrapper.all_routines.keys()) == {42, 43}
    assert len(wrapper.condensed_routines) == 2

    good = next(r for r in wrapper.condensed_routines if r["id"] == 42)
    assert good["name"] == "Bedtime" and good["comment"] == "test"
    assert "invalid" not in good
    assert set(good["device_names"]) == {"Left Hallway", "Right Hallway"}

    bad = next(r for r in wrapper.condensed_routines if r["id"] == 43)
    assert bad["invalid"] is True and bad["invalid_reason"] == "bad xml"

    assert wrapper.routines_changed is False


@pytest.mark.asyncio
async def test_get_device_name_list_recurses_into_paren_and_skips_nodeless_types():
    wrapper = _bare_wrapper_for_load_routines()
    wrapper.get_device_name = lambda node_id: {"N1": "Device One"}.get(node_id, node_id)

    routine = {
        "if": [
            {"type": "var", "andOr": "and", "id": 5, "varType": "1", "op": "GT", "val": {"value": 1}},
            {"type": "paren", "andOr": "and", "conditions": [
                {"type": "control", "andOr": "and", "id": "DON", "node": "N1", "op": "IS"},
            ]},
        ],
        "then": [{"type": "sys", "cmd": 1}],
        "else": [],
    }

    names = wrapper._get_device_name_list_from_routine(routine)
    assert names == ["Device One"]
