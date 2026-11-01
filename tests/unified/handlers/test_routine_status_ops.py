"""unified.handlers.routine_status_ops.routine_status_op -- confirms _op_ok/
_op_error correctly treat a plain error string/non-2xx response as a failure,
and that delete is gated by a code-enforced two-call confirm (confirmed: true),
same shape as node_op/variable_op/delete_plugin.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from unified.handlers.routine_status_ops import routine_status_op


class _FakeBackend:
    def __init__(self, routine_ops_result):
        self._routine_ops_result = routine_ops_result
        self.calls: list = []

    async def routine_ops(self, routine_id, operation):
        self.calls.append((routine_id, operation))
        return self._routine_ops_result


@pytest.mark.asyncio
async def test_requires_id_and_operation():
    backend = _FakeBackend(SimpleNamespace(status_code=200))
    result = await routine_status_op(backend, {"operation": "enable"})
    assert result == {"error": "id and operation are both required"}
    assert backend.calls == []


@pytest.mark.asyncio
async def test_enable_succeeds_without_any_confirm_step():
    backend = _FakeBackend(SimpleNamespace(status_code=200))
    result = await routine_status_op(backend, {"id": 42, "operation": "enable"})
    assert result == {"id": 42, "operation": "enable", "status": "ok"}
    assert backend.calls == [(42, "enable")]


@pytest.mark.asyncio
async def test_successful_non_delete_operation_marks_routines_changed():
    """Bug fix: this handler previously never touched routines_changed at
    all, relying entirely on the hub's own _1 websocket event to keep
    condensed_routines/ROUTINES DATABASE honest -- enable/disable change a
    field ROUTINES DATABASE renders directly (and this is set unconditionally
    for any successful operation, matching variable_op's own convention)."""
    backend = _FakeBackend(SimpleNamespace(status_code=200))
    backend.routines_changed = False
    await routine_status_op(backend, {"id": 42, "operation": "enable"})
    assert backend.routines_changed is True


@pytest.mark.asyncio
async def test_backend_failure_surfaces_as_error():
    backend = _FakeBackend("routine not found")
    result = await routine_status_op(backend, {"id": 42, "operation": "stop"})
    assert result == {"error": "'stop' failed for routine 42: routine not found"}


@pytest.mark.asyncio
async def test_backend_failure_does_not_mark_routines_changed():
    backend = _FakeBackend("routine not found")
    backend.routines_changed = False
    await routine_status_op(backend, {"id": 42, "operation": "stop"})
    assert backend.routines_changed is False


@pytest.mark.asyncio
async def test_delete_without_confirmed_previews_without_deleting():
    backend = _FakeBackend(SimpleNamespace(status_code=200))
    result = await routine_status_op(backend, {"id": 42, "operation": "delete"})
    assert result == {
        "confirmation_required": True,
        "id": 42,
        "message": (
            "This permanently deletes this routine -- nothing has been deleted yet. Call "
            "routine_status_op again with confirmed: true only after the customer has "
            "explicitly agreed, never speculatively. The system only honors this if their "
            "very next reply is exactly the word YES (all caps) and nothing else; it "
            "independently verifies this regardless of what this call's own confirmed "
            "argument says."
        ),
    }
    assert backend.calls == []


@pytest.mark.asyncio
async def test_delete_confirmed_true_performs_the_delete():
    backend = _FakeBackend(SimpleNamespace(status_code=200))
    result = await routine_status_op(backend, {"id": 42, "operation": "delete", "confirmed": True})
    assert result == {"id": 42, "operation": "delete", "status": "ok"}
    assert backend.calls == [(42, "delete")]


@pytest.mark.asyncio
async def test_delete_confirmed_true_marks_routines_changed():
    """The most likely real-world cause of the stale-ROUTINES-DATABASE bug
    report: a deleted routine's row lingered in condensed_routines until the
    hub's own _1 event happened to arrive, making its freed id look like it
    was reused by something else rather than the routine simply being gone."""
    backend = _FakeBackend(SimpleNamespace(status_code=200))
    backend.routines_changed = False
    await routine_status_op(backend, {"id": 42, "operation": "delete", "confirmed": True})
    assert backend.routines_changed is True


@pytest.mark.asyncio
async def test_delete_preview_does_not_mark_routines_changed():
    backend = _FakeBackend(SimpleNamespace(status_code=200))
    backend.routines_changed = False
    await routine_status_op(backend, {"id": 42, "operation": "delete"})
    assert backend.routines_changed is False


@pytest.mark.asyncio
async def test_delete_confirmed_true_surfaces_backend_failure():
    backend = _FakeBackend("routine not found")
    result = await routine_status_op(backend, {"id": 42, "operation": "delete", "confirmed": True})
    assert result == {"error": "'delete' failed for routine 42: routine not found"}
