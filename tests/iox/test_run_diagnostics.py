"""INSTEONDiagnostics' structured-return wrappers (quick_plm_sanity_check/
get_dev_links_table/get_device_to_plm_link_status) and IoXWrapper's thin
delegation to IoXDiagnostics for the diagnostics surface (the two
complaint-shaped diagnose_* methods, restart_core_service, and the standing
get_full_system_config/get_core_services_status/get_device_family tools).

See tests/iox/test_insteon_diagnose.py for the real branch-by-branch
decision-tree coverage of diagnose_not_responding/diagnose_no_status_feedback.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from iox.diagnostics.insteon_diag import INSTEONDiagnostics
from iox.diagnostics.iox_diagnostics import IoXDiagnostics
from iox.iox_wrapper import IoXWrapper


def _bare_insteon_diagnostics(*, sanity_result=None, dev_links_table=None) -> INSTEONDiagnostics:
    diag = object.__new__(INSTEONDiagnostics)
    diag._plm_op_state = None
    result = sanity_result or {"passed": True, "plm_connected": True, "report": "sane"}

    async def _quick_plm_sanity_check(**kwargs):
        return result

    async def _get_dev_links_table(device_id=None, **kwargs):
        return dev_links_table

    diag._quick_plm_sanity_check = _quick_plm_sanity_check
    diag._get_dev_links_table = _get_dev_links_table
    return diag


async def _resolved(value):
    return value


# ----------------------------------------------------------------------
# quick_plm_sanity_check -- structured return
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quick_plm_sanity_check_returns_structured_result_when_insteon_enabled():
    diag = _bare_insteon_diagnostics(sanity_result={"passed": True, "plm_connected": True, "report": "SANE"})
    diag._iox_diagnostics = SimpleNamespace(
        _get_system_options=lambda: _resolved({"insteonSupport": True}),
        get_core_services_status=lambda: _resolved({"isy": "running"}),
    )

    result = await diag.quick_plm_sanity_check()

    assert result["passed"] is True
    assert result["insteon_enabled"] is True
    assert result["plm_connected"] is True
    assert "SANE" in result["report"]


@pytest.mark.asyncio
async def test_quick_plm_sanity_check_short_circuits_when_insteon_disabled():
    diag = _bare_insteon_diagnostics()
    diag._iox_diagnostics = SimpleNamespace(
        _get_system_options=lambda: _resolved({"insteonSupport": False}),
        get_core_services_status=lambda: _resolved({"isy": "running"}),
    )
    called = {"insteon": False}

    async def fail_if_called(**kwargs):
        called["insteon"] = True

    diag._quick_plm_sanity_check = fail_if_called

    result = await diag.quick_plm_sanity_check()

    assert result["passed"] is False
    assert result["insteon_enabled"] is False
    assert result["plm_connected"] is None
    assert "not enabled" in result["report"]
    assert called["insteon"] is False  # never reached the PLM-level check


@pytest.mark.asyncio
async def test_quick_plm_sanity_check_reports_not_within_tolerance():
    diag = _bare_insteon_diagnostics(sanity_result={"passed": False, "plm_connected": True, "report": "PROBLEM"})
    diag._iox_diagnostics = SimpleNamespace(
        _get_system_options=lambda: _resolved({"insteonSupport": True}),
        get_core_services_status=lambda: _resolved({"isy": "running"}),
    )

    result = await diag.quick_plm_sanity_check()

    assert result["passed"] is False
    assert result["insteon_enabled"] is True


# ----------------------------------------------------------------------
# get_device_to_plm_link_status -- structured wrapper over get_dev_links_table
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_device_to_plm_link_status_true_when_controller_row_present():
    diag = _bare_insteon_diagnostics(
        dev_links_table="Device Links Table\n```csv\nidx,role,group,device,data\n1,controller,1,PLM,000000\n```\n"
    )

    result = await diag.get_device_to_plm_link_status("n001")

    assert result["has_device_to_plm_link"] is True


@pytest.mark.asyncio
async def test_get_device_to_plm_link_status_false_when_no_controller_row():
    diag = _bare_insteon_diagnostics(
        dev_links_table="Device Links Table\n```csv\nidx,role,group,device,data\n1,responder,1,PLM,000000\n```\n"
    )

    result = await diag.get_device_to_plm_link_status("n001")

    assert result["has_device_to_plm_link"] is False


@pytest.mark.asyncio
async def test_get_device_to_plm_link_status_none_when_table_unavailable():
    diag = _bare_insteon_diagnostics(dev_links_table="PLM not connected. Cannot retrieve device links table.")

    result = await diag.get_device_to_plm_link_status("n001")

    assert result["has_device_to_plm_link"] is None


# ----------------------------------------------------------------------
# IoXWrapper delegation -- every diagnostics-surface method just forwards to
# self.diagnostics.
# ----------------------------------------------------------------------


def _bare_wrapper_with_diagnostics() -> IoXWrapper:
    wrapper = object.__new__(IoXWrapper)
    wrapper.diagnostics = object.__new__(IoXDiagnostics)
    return wrapper


@pytest.mark.asyncio
async def test_wrapper_diagnose_not_responding_delegates():
    wrapper = _bare_wrapper_with_diagnostics()

    async def fake(protocol, device_id=None, force=False):
        return {"protocol": protocol, "device_id": device_id, "force": force}

    wrapper.diagnostics.diagnose_not_responding = fake

    result = await wrapper.diagnose_not_responding("insteon", "n001")

    assert result == {"protocol": "insteon", "device_id": "n001", "force": False}


@pytest.mark.asyncio
async def test_wrapper_diagnose_no_status_feedback_delegates():
    wrapper = _bare_wrapper_with_diagnostics()

    async def fake(protocol, device_id=None):
        return {"protocol": protocol, "device_id": device_id}

    wrapper.diagnostics.diagnose_no_status_feedback = fake

    result = await wrapper.diagnose_no_status_feedback("insteon", None)

    assert result == {"protocol": "insteon", "device_id": None}


@pytest.mark.asyncio
async def test_wrapper_restart_core_service_delegates_to_services_ops():
    wrapper = _bare_wrapper_with_diagnostics()

    received = {}

    async def fake_services_ops(service, op):
        received["service"] = service
        received["op"] = op
        return {"status": "ok"}

    wrapper.diagnostics.services_ops = fake_services_ops

    result = await wrapper.restart_core_service("udx", "restart")

    assert result == {"status": "ok"}
    assert received == {"service": "udx", "op": "restart"}


@pytest.mark.asyncio
async def test_wrapper_get_full_system_config_delegates_to_diagnostics():
    wrapper = _bare_wrapper_with_diagnostics()

    async def fake():
        return {"ok": True}

    wrapper.diagnostics.get_full_system_config = fake

    assert await wrapper.get_full_system_config() == {"ok": True}


@pytest.mark.asyncio
async def test_wrapper_get_core_services_status_delegates_to_diagnostics():
    wrapper = _bare_wrapper_with_diagnostics()

    async def fake():
        return {"isy": "running"}

    wrapper.diagnostics.get_core_services_status = fake

    assert await wrapper.get_core_services_status() == {"isy": "running"}


@pytest.mark.asyncio
async def test_wrapper_get_device_family_delegates_to_diagnostics_with_device_id():
    wrapper = _bare_wrapper_with_diagnostics()

    received = {}

    async def fake(device_id=None, **kwargs):
        received["device_id"] = device_id
        return "insteon"

    wrapper.diagnostics.get_device_family = fake

    result = await wrapper.get_device_family("n001")

    assert result == "insteon"
    assert received["device_id"] == "n001"
