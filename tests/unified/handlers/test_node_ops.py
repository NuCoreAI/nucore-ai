"""unified.handlers.node_ops.node_op -- confirms _op_ok/_op_error correctly
treat a plain error string returned by NuCoreInterface.node_ops (e.g. the
Z-Wave/Zigbee/Matter/plugin delete-family guard added in iox_wrapper.py) as a
failure surfaced via {"error": ...}, not mistaken for a successful response.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from unified.handlers.node_ops import node_op


class _FakeBackend:
    def __init__(self, node_ops_result):
        self._node_ops_result = node_ops_result
        self.calls: list = []

    async def node_ops(self, node_id, operation, **kwargs):
        self.calls.append((node_id, operation, kwargs))
        return self._node_ops_result


@pytest.mark.asyncio
async def test_delete_rejected_by_backend_guard_surfaces_as_error():
    redirect_message = (
        "Cannot delete a zwave device via node_op -- the hub must be put into "
        "removal mode instead. Use pair_device(protocol=\"zwave\", "
        "action=\"start_exclusion\"), have the customer activate the device to "
        "remove, then call finish_exclusion to commit."
    )
    backend = _FakeBackend(redirect_message)
    result = await node_op(backend, {"operation": "delete", "node_id": "n1"})
    assert result == {"error": f"'delete' failed: {redirect_message}"}


@pytest.mark.asyncio
async def test_delete_success_still_reports_ok():
    backend = _FakeBackend(SimpleNamespace(status_code=200))
    result = await node_op(backend, {"operation": "delete", "node_id": "n1"})
    assert result == {"node_id": "n1", "operation": "delete", "status": "ok"}
