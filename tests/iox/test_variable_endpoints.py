"""Verifies IoXWrapper's NuCore variable support: GET /api/variables/<type>
(list, feeds self.variables/self.condensed_variables), PUT (create), POST
(update), DELETE (delete), and the variable_names cross-reference
_load_routines attaches to each condensed routine.
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
    return object.__new__(IoXWrapper)


def _bare_wrapper_for_load_variables() -> IoXWrapper:
    wrapper = _bare_wrapper()
    wrapper.variables = {}
    wrapper.condensed_variables = []
    return wrapper


@pytest.mark.asyncio
async def test_variable_ops_create_hits_put_endpoint_with_name_and_prec():
    wrapper = _bare_wrapper()
    calls = []
    wrapper.put = lambda path, body=None, headers=None: (calls.append((path, json.loads(body))), FakeResp())[1]

    await wrapper.variable_ops(1, None, "create", name="Irrigation_Mode", prec=0)

    assert calls == [("/api/variables/1", {"name": "Irrigation_Mode", "prec": 0})]


@pytest.mark.asyncio
async def test_variable_ops_update_hits_post_endpoint_with_id():
    wrapper = _bare_wrapper()
    calls = []
    wrapper.post = lambda path, body=None, headers=None: (calls.append((path, json.loads(body))), FakeResp())[1]

    await wrapper.variable_ops(2, "3", "update", value=1, init=0)

    assert calls == [("/api/variables/2/3", {"value": 1, "init": 0})]


@pytest.mark.asyncio
async def test_variable_ops_delete_hits_delete_endpoint():
    wrapper = _bare_wrapper()
    calls = []
    wrapper.delete = lambda path: (calls.append(path), FakeResp())[1]

    await wrapper.variable_ops(1, "3", "delete")

    assert calls == ["/api/variables/1/3"]


@pytest.mark.asyncio
async def test_variable_ops_update_delete_require_id():
    wrapper = _bare_wrapper()
    assert await wrapper.variable_ops(1, None, "update", value=1) is None
    assert await wrapper.variable_ops(1, "", "delete") is None


@pytest.mark.asyncio
async def test_variable_ops_rejects_unknown_operation():
    wrapper = _bare_wrapper()
    assert await wrapper.variable_ops(1, "3", "rename") is None


@pytest.mark.asyncio
async def test_load_variables_merges_both_types_keyed_by_type_and_id():
    wrapper = _bare_wrapper_for_load_variables()

    def fake_get(path):
        if path == "/api/variables/1":
            return FakeResp(data=[{"id": "1", "val": 0, "init": 0, "prec": 0, "name": "Watering_the_plants", "ts": "t1"}])
        if path == "/api/variables/2":
            return FakeResp(data=[{"id": "3", "val": 1, "init": 0, "prec": 0, "name": "Irrigation_Mode", "ts": "t2"}])
        raise AssertionError(f"unexpected path {path}")

    wrapper.get = fake_get
    await wrapper._load_variables()

    assert set(wrapper.variables.keys()) == {"1:1", "2:3"}
    assert wrapper.variables["1:1"]["name"] == "Watering_the_plants"
    assert wrapper.variables["1:1"]["type"] == 1
    assert wrapper.variables["2:3"]["name"] == "Irrigation_Mode"
    assert len(wrapper.condensed_variables) == 2


@pytest.mark.asyncio
async def test_load_variables_rebuilds_from_scratch_each_call():
    """Unlike _load_routines (which appends), a fresh call must not
    accumulate stale entries from a previous refresh."""
    wrapper = _bare_wrapper_for_load_variables()
    wrapper.variables = {"1:99": {"id": "99", "name": "Stale", "type": 1}}
    wrapper.condensed_variables = [{"id": "99", "name": "Stale"}]
    wrapper.get = lambda path: FakeResp(data=[])

    await wrapper._load_variables()

    assert wrapper.variables == {}
    assert wrapper.condensed_variables == []


@pytest.mark.asyncio
async def test_load_variables_tolerates_a_failed_type():
    wrapper = _bare_wrapper_for_load_variables()

    def fake_get(path):
        if path == "/api/variables/1":
            return FakeResp(status_code=500)
        return FakeResp(data=[{"id": "3", "name": "Irrigation_Mode", "prec": 0}])

    wrapper.get = fake_get
    await wrapper._load_variables()

    assert set(wrapper.variables.keys()) == {"2:3"}


def test_get_variable_name_list_from_routine_resolves_var_condition_and_action():
    wrapper = _bare_wrapper()
    wrapper.variables = {
        "1:5": {"id": "5", "name": "Irrigation_Mode", "type": 1},
        "2:7": {"id": "7", "name": "Poolpump_has_already_run", "type": 2},
    }

    routine = {
        "if": [
            {"type": "var", "andOr": "and", "id": 5, "varType": "1", "op": "GT", "val": {"value": 1}},
            {"type": "paren", "andOr": "and", "conditions": [
                {"type": "var", "andOr": "and", "id": 7, "varType": "2", "op": "IS", "val": {"value": 1}},
            ]},
        ],
        "then": [{"type": "var", "varType": "1", "id": 5, "op": "EQ", "val": {"value": 0}}],
        "else": [],
    }

    names = wrapper._get_variable_name_list_from_routine(routine)
    assert set(names) == {"Irrigation_Mode", "Poolpump_has_already_run"}


def test_get_variable_name_list_from_routine_resolves_nested_var_ref_and_while_repeat():
    wrapper = _bare_wrapper()
    wrapper.variables = {
        "1:1": {"id": "1", "name": "Counter", "type": 1},
        "1:2": {"id": "2", "name": "Threshold", "type": 1},
    }

    routine = {
        "if": [],
        "then": [
            {"type": "var", "varType": "1", "id": "1", "op": "EQ", "var": {"id": "2", "type": "1"}},
            {"type": "repeat", "while": {"var": {"op": "GT", "varType": "1", "id": "1", "val": {"value": 0}}}},
        ],
        "else": [],
    }

    names = wrapper._get_variable_name_list_from_routine(routine)
    assert set(names) == {"Counter", "Threshold"}


def test_get_variable_name_list_from_routine_empty_for_none_and_no_refs():
    wrapper = _bare_wrapper()
    wrapper.variables = {}
    assert wrapper._get_variable_name_list_from_routine(None) == []
    assert wrapper._get_variable_name_list_from_routine({"if": [], "then": [], "else": []}) == []
