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
async def test_backend_failure_surfaces_as_error():
    backend = _FakeBackend("routine not found")
    result = await routine_status_op(backend, {"id": 42, "operation": "stop"})
    assert result == {"error": "'stop' failed for routine 42: routine not found"}


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
            "explicitly agreed, never speculatively."
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
async def test_delete_confirmed_true_surfaces_backend_failure():
    backend = _FakeBackend("routine not found")
    result = await routine_status_op(backend, {"id": 42, "operation": "delete", "confirmed": True})
    assert result == {"error": "'delete' failed for routine 42: routine not found"}
