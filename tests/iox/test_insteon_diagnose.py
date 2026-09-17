"""IoXDiagnostics.diagnose_not_responding/diagnose_no_status_feedback -- the
real decision tree (formerly a model-driven, prose-enforced sequence of
run_diagnostic_step calls), now deterministic Python. Each test proves both
the diagnosis and that only the calls the branch actually needs were made --
the regression coverage for "order is now code, not prose."
"""

from __future__ import annotations

import pytest

from iox.diagnostics.iox_diagnostics import IoXDiagnostics


class _FakeInsteonDiag:
    def __init__(self, *, plm_connected=True):
        self.plm_connected = plm_connected
        self.get_plm_info_calls = 0

    async def _get_plm_info(self):
        self.get_plm_info_calls += 1
        return self.plm_connected, {"info": "fake"}


class _FakeIoxWrapper:
    def __init__(self, *, command=None, send_should_fail=False):
        self._command = command
        self._send_should_fail = send_should_fail
        self.resolve_command_id_calls: list[tuple] = []
        self.send_commands_calls: list[list] = []

    def resolve_command_id(self, device_id, name, direction="accepts"):
        self.resolve_command_id_calls.append((device_id, name, direction))
        return self._command

    async def send_commands(self, commands):
        self.send_commands_calls.append(commands)
        if self._send_should_fail:
            raise RuntimeError("device did not respond")


class _Command:
    def __init__(self, command_id="QUERY"):
        self.id = command_id


def _diag(
    *,
    insteon_enabled=True,
    plm_connected=True,
    core_services_status="ok",
    command=_Command(),
    send_should_fail=False,
) -> IoXDiagnostics:
    diag = object.__new__(IoXDiagnostics)
    diag._plm_op_state = None
    diag._get_system_options = _async_return({"insteonSupport": insteon_enabled})
    diag.get_core_services_status = _async_return(core_services_status)
    diag._init_insteon_diag = lambda device_id=None, **kw: True
    diag._insteon_diag = _FakeInsteonDiag(plm_connected=plm_connected)
    diag._iox_wrapper = _FakeIoxWrapper(command=command, send_should_fail=send_should_fail)
    return diag


def _async_return(value):
    async def _fn(*args, **kwargs):
        return value

    return _fn


# ----------------------------------------------------------------------
# diagnose_not_responding
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_not_responding_unsupported_protocol():
    diag = _diag()

    result = await diag.diagnose_not_responding("zwave", "n001")

    assert "error" in result
    assert diag._iox_wrapper.resolve_command_id_calls == []


@pytest.mark.asyncio
async def test_not_responding_stops_when_insteon_disabled_no_further_calls():
    diag = _diag(insteon_enabled=False)

    result = await diag.diagnose_not_responding("insteon", "n001")

    assert "not enabled" in result["diagnosis"]
    assert "recommended_fix" in result
    assert diag._insteon_diag.get_plm_info_calls == 0  # never got past Step 1
    assert diag._iox_wrapper.resolve_command_id_calls == []


@pytest.mark.asyncio
async def test_not_responding_stops_when_plm_not_connected():
    diag = _diag(plm_connected=False)

    result = await diag.diagnose_not_responding("insteon", "n001")

    assert "not connected" in result["diagnosis"]
    assert "recommended_fix" in result
    assert diag._iox_wrapper.resolve_command_id_calls == []


@pytest.mark.asyncio
async def test_not_responding_requires_device_id_once_system_checks_pass():
    diag = _diag()

    result = await diag.diagnose_not_responding("insteon", None)

    assert "error" in result
    assert diag._iox_wrapper.resolve_command_id_calls == []


@pytest.mark.asyncio
async def test_not_responding_query_succeeds():
    diag = _diag(send_should_fail=False)

    result = await diag.diagnose_not_responding("insteon", "n001")

    assert result["query_check"] == {"device_id": "n001", "successful": True}
    assert "succeeded" in result["diagnosis"]
    assert "recommended_fix" not in result


@pytest.mark.asyncio
async def test_not_responding_query_fails():
    diag = _diag(send_should_fail=True)

    result = await diag.diagnose_not_responding("insteon", "n001")

    assert result["query_check"] == {"device_id": "n001", "successful": False}
    assert "recommended_fix" in result


@pytest.mark.asyncio
async def test_not_responding_unknown_query_command():
    diag = _diag(command=None)

    result = await diag.diagnose_not_responding("insteon", "n001")

    assert "error" in result
    assert diag._iox_wrapper.send_commands_calls == []


@pytest.mark.asyncio
async def test_not_responding_full_happy_path_steps_run_order():
    diag = _diag()

    result = await diag.diagnose_not_responding("insteon", "n001")

    assert result["steps_run"] == [
        "get_system_options",
        "get_plm_info",
        "get_core_services_status",
        "send Query to n001",
    ]


# ----------------------------------------------------------------------
# diagnose_no_status_feedback
# ----------------------------------------------------------------------


def _diag_for_status_feedback(*, insteon_enabled=True, sanity_passed=True, has_link=None) -> IoXDiagnostics:
    diag = object.__new__(IoXDiagnostics)
    diag._plm_op_state = None
    diag._get_system_options = _async_return({"insteonSupport": insteon_enabled})
    diag.get_core_services_status = _async_return("ok")
    diag._init_insteon_diag = lambda device_id=None, **kw: True
    diag._insteon_diag = _FakeInsteonDiagSanity(passed=sanity_passed)
    diag.get_device_to_plm_link_status = _async_return({"has_device_to_plm_link": has_link, "report": "x"})
    return diag


class _FakeInsteonDiagSanity:
    def __init__(self, *, passed=True):
        self._passed = passed
        self.calls = 0

    async def _quick_plm_sanity_check(self, **kwargs):
        self.calls += 1
        return {"passed": self._passed, "plm_connected": True, "report": "report text"}


@pytest.mark.asyncio
async def test_no_status_feedback_unsupported_protocol():
    diag = _diag_for_status_feedback()

    result = await diag.diagnose_no_status_feedback("zigbee", "n001")

    assert "error" in result
    assert diag._insteon_diag.calls == 0


@pytest.mark.asyncio
async def test_no_status_feedback_insteon_disabled():
    diag = _diag_for_status_feedback(insteon_enabled=False)

    result = await diag.diagnose_no_status_feedback("insteon", None)

    assert "not enabled" in result["diagnosis"]
    assert "recommended_fix" in result


@pytest.mark.asyncio
async def test_no_status_feedback_sanity_check_fails():
    diag = _diag_for_status_feedback(sanity_passed=False)

    result = await diag.diagnose_no_status_feedback("insteon", None)

    assert "recommended_fix" in result
    assert "clarifying_question" in result


@pytest.mark.asyncio
async def test_no_status_feedback_passes_no_device_id_asks_for_one():
    diag = _diag_for_status_feedback(sanity_passed=True)

    result = await diag.diagnose_no_status_feedback("insteon", None)

    assert "needs_device_id" in result
    assert "diagnosis" not in result


@pytest.mark.asyncio
async def test_no_status_feedback_passes_with_device_id_has_link():
    diag = _diag_for_status_feedback(sanity_passed=True, has_link=True)

    result = await diag.diagnose_no_status_feedback("insteon", "n001")

    assert "correctly linked" in result["diagnosis"]
    assert "recommended_fix" not in result


@pytest.mark.asyncio
async def test_no_status_feedback_passes_with_device_id_missing_link():
    diag = _diag_for_status_feedback(sanity_passed=True, has_link=False)

    result = await diag.diagnose_no_status_feedback("insteon", "n001")

    assert "missing" in result["diagnosis"]
    assert "recommended_fix" in result
    assert "clarifying_question" in result


@pytest.mark.asyncio
async def test_no_status_feedback_full_happy_path_steps_run_order():
    diag = _diag_for_status_feedback(sanity_passed=True, has_link=True)

    result = await diag.diagnose_no_status_feedback("insteon", "n001")

    assert result["steps_run"] == ["quick_plm_sanity_check", "get_device_to_plm_link_status(n001)"]
