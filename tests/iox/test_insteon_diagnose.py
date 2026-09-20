"""INSTEONDiagnostics.diagnose_not_responding/diagnose_no_status_feedback --
the real decision tree (formerly a model-driven, prose-enforced sequence of
run_diagnostic_step calls), now deterministic Python. Each test proves both
the diagnosis and that only the calls the branch actually needs were made --
the regression coverage for "order is now code, not prose."

The two methods used to live on IoXDiagnostics; they (and every other
INSTEON-specific method) moved onto INSTEONDiagnostics, so most tests here
construct INSTEONDiagnostics directly. A small group stays against
IoXDiagnostics -- proving its protocol-dispatch shell (bogus/non-insteon
protocols, and that it actually delegates to INSTEONDiagnostics for
"insteon") still works.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from iox.diagnostics.insteon_diag import INSTEONDiagnostics
from iox.diagnostics.iox_diagnostics import IoXDiagnostics
from nucore import Folder, Group


class _Command:
    def __init__(self, command_id="QUERY"):
        self.id = command_id


# Distinguishes "caller didn't specify a command" (default to a real
# _Command()) from "caller explicitly wants no resolvable command" (pass
# command=None) -- both would otherwise look identical as plain None.
_MISSING = object()


def _async_return(value):
    async def _fn(*args, **kwargs):
        return value

    return _fn


def _links_blob(rows: list[str]) -> str:
    """A minimal fenced-CSV links-table blob, the same shape
    get_dev_links_table/get_iox_links_table/get_all_plm_links actually
    produce -- see insteon_diag.py's LINKS_TABLE_FENCE_OPEN/HEADER."""
    lines = ["Links Table\n", "```csv\n", "idx,role,group,device,data\n"]
    lines += [f"{row}\n" for row in rows]
    lines.append("```\n")
    return "".join(lines)


class _FakeIoxWrapperNotResponding:
    def __init__(self, *, command=_MISSING, send_should_fail=False, insteon_addresses=None, protocol_enabled=True):
        self._command = _Command() if command is _MISSING else command
        self._send_should_fail = send_should_fail
        self._protocol_enabled = protocol_enabled
        self.nodes = {addr: object() for addr in (insteon_addresses or [])}
        self.resolve_command_id_calls: list[tuple] = []
        self.send_commands_calls: list[list] = []

    def resolve_command_id(self, device_id, name, direction="accepts"):
        self.resolve_command_id_calls.append((device_id, name, direction))
        return self._command

    async def send_commands(self, commands):
        self.send_commands_calls.append(commands)
        if self._send_should_fail:
            raise RuntimeError("device did not respond")
        return [SimpleNamespace(status_code=200)]

    def _is_insteon_family(self, device_id):
        return device_id in self.nodes

    def _get_node(self, node_id):
        return self.nodes.get(node_id)

    async def is_protocol_enabled(self, protocol):
        return self._protocol_enabled


# ----------------------------------------------------------------------
# IoXDiagnostics -- thin protocol-dispatch shell only (no INSTEON logic
# lives here anymore; see INSTEONDiagnostics sections below for that).
# ----------------------------------------------------------------------


def _iox_diagnostics_for_dispatch(*, protocol_enabled=True) -> IoXDiagnostics:
    diag = object.__new__(IoXDiagnostics)
    diag._insteon_diag = None
    diag._iox_wrapper = _FakeIoxWrapperNotResponding(protocol_enabled=protocol_enabled)
    return diag


@pytest.mark.asyncio
async def test_not_responding_bogus_protocol_is_a_clear_error():
    diag = _iox_diagnostics_for_dispatch()

    result = await diag.diagnose_not_responding("bogus-protocol", "n001")

    assert "error" in result
    assert diag._iox_wrapper.resolve_command_id_calls == []


@pytest.mark.asyncio
async def test_not_responding_stub_protocol_with_device_id_is_not_implemented():
    diag = _iox_diagnostics_for_dispatch()

    result = await diag.diagnose_not_responding("zwave", "n001")

    assert "error" in result
    assert diag._iox_wrapper.resolve_command_id_calls == []


@pytest.mark.asyncio
async def test_not_responding_stub_protocol_system_wide_enabled():
    diag = _iox_diagnostics_for_dispatch(protocol_enabled=True)

    result = await diag.diagnose_not_responding("matter", None)

    assert "is enabled" in result["diagnosis"]
    assert "recommended_fix" not in result


@pytest.mark.asyncio
async def test_not_responding_stub_protocol_system_wide_disabled():
    diag = _iox_diagnostics_for_dispatch(protocol_enabled=False)

    result = await diag.diagnose_not_responding("zigbee", None)

    assert "not enabled" in result["diagnosis"]
    assert "recommended_fix" in result


@pytest.mark.asyncio
async def test_no_status_feedback_unsupported_protocol():
    diag = _iox_diagnostics_for_dispatch()

    result = await diag.diagnose_no_status_feedback("zigbee", "n001")

    assert "error" in result
    assert diag._insteon_diag is None  # _init_insteon_diag was never even reached


class _FakeInsteonDiagDelegate:
    """Stands in for a fully-constructed INSTEONDiagnostics -- proves
    IoXDiagnostics's "insteon" branch actually delegates, without exercising
    the real decision tree (that's covered by the sections below)."""

    def __init__(self):
        self.not_responding_calls: list[str | None] = []
        self.no_status_feedback_calls: list[str | None] = []

    async def diagnose_not_responding(self, device_id, force=False):
        self.not_responding_calls.append(device_id)
        return {"steps_run": [], "diagnosis": "delegated"}

    async def diagnose_no_status_feedback(self, device_id):
        self.no_status_feedback_calls.append(device_id)
        return {"steps_run": [], "diagnosis": "delegated"}


@pytest.mark.asyncio
async def test_not_responding_insteon_protocol_delegates_to_insteon_diagnostics():
    diag = object.__new__(IoXDiagnostics)
    diag._iox_wrapper = _FakeIoxWrapperNotResponding(insteon_addresses=["n001"])
    diag._insteon_diag = _FakeInsteonDiagDelegate()

    result = await diag.diagnose_not_responding("insteon", "n001")

    assert diag._insteon_diag.not_responding_calls == ["n001"]
    assert result == {"steps_run": [], "diagnosis": "delegated"}


@pytest.mark.asyncio
async def test_no_status_feedback_insteon_protocol_delegates_to_insteon_diagnostics():
    diag = object.__new__(IoXDiagnostics)
    diag._iox_wrapper = _FakeIoxWrapperNotResponding(insteon_addresses=["n001"])
    diag._insteon_diag = _FakeInsteonDiagDelegate()

    result = await diag.diagnose_no_status_feedback("insteon", "n001")

    assert diag._insteon_diag.no_status_feedback_calls == ["n001"]
    assert result == {"steps_run": [], "diagnosis": "delegated"}


# ----------------------------------------------------------------------
# INSTEONDiagnostics.diagnose_not_responding
# ----------------------------------------------------------------------


def _insteon_diag_for_not_responding(
    *,
    insteon_enabled=True,
    plm_connected=True,
    sanity_passed=True,
    command=_MISSING,
    send_should_fail=False,
    insteon_addresses=None,
    iox_links_table=None,
    plm_links_table=None,
) -> INSTEONDiagnostics:
    diag = object.__new__(INSTEONDiagnostics)
    diag._plm_op_state = None
    diag._iox_diagnostics = SimpleNamespace(
        _get_system_options=_async_return({"insteonSupport": insteon_enabled}),
        get_core_services_status=_async_return("ok"),
    )
    diag._quick_plm_sanity_check = _async_return(
        {"passed": sanity_passed, "plm_connected": plm_connected, "report": "report text"}
    )
    diag.iox_links_table_calls: list[str] = []
    diag.plm_links_calls = 0

    async def _get_iox_links_table(device_id, **kwargs):
        diag.iox_links_table_calls.append(device_id)
        return iox_links_table

    async def _get_all_plm_links(**kwargs):
        diag.plm_links_calls += 1
        return plm_links_table

    diag._get_iox_links_table = _get_iox_links_table
    diag._get_all_plm_links = _get_all_plm_links
    diag._iox_wrapper = _FakeIoxWrapperNotResponding(
        command=command,
        send_should_fail=send_should_fail,
        insteon_addresses=insteon_addresses,
    )
    return diag


@pytest.mark.asyncio
async def test_not_responding_stops_when_insteon_disabled_no_further_calls():
    diag = _insteon_diag_for_not_responding(insteon_enabled=False)

    result = await diag.diagnose_not_responding("n001")

    assert "not enabled" in result["diagnosis"]
    assert "recommended_fix" in result
    assert diag._iox_wrapper.resolve_command_id_calls == []


@pytest.mark.asyncio
async def test_not_responding_stops_when_plm_not_connected():
    diag = _insteon_diag_for_not_responding(sanity_passed=False, plm_connected=False)

    result = await diag.diagnose_not_responding("n001")

    assert "not connected" in result["diagnosis"]
    assert result["recommended_fix"] == diag._KNOWN_FIXES["plm_not_connected"]
    assert diag._iox_wrapper.resolve_command_id_calls == []


@pytest.mark.asyncio
async def test_not_responding_stops_when_sanity_check_fails_but_plm_connected():
    diag = _insteon_diag_for_not_responding(sanity_passed=False, plm_connected=True)

    result = await diag.diagnose_not_responding("n001")

    assert "did not pass" in result["diagnosis"]
    assert result["recommended_fix"] == diag._KNOWN_FIXES["plm_links_missing"]
    assert "clarifying_question" in result
    assert diag._iox_wrapper.resolve_command_id_calls == []


@pytest.mark.asyncio
async def test_not_responding_no_device_id_samples_insteon_devices():
    diag = _insteon_diag_for_not_responding(insteon_addresses=["25 80 3C 1", "17 F8 44 1"])

    result = await diag.diagnose_not_responding(None)

    assert len(result["device_checks"]) == 2
    checked_ids = {c["device_id"] for c in result["device_checks"]}
    assert checked_ids == {"25 80 3C 1", "17 F8 44 1"}


@pytest.mark.asyncio
async def test_not_responding_no_device_id_no_insteon_devices_found():
    diag = _insteon_diag_for_not_responding(insteon_addresses=[])

    result = await diag.diagnose_not_responding(None)

    assert result["status"] == "no insteon device found"


@pytest.mark.asyncio
async def test_not_responding_query_succeeds():
    diag = _insteon_diag_for_not_responding(send_should_fail=False)

    result = await diag.diagnose_not_responding("n001")

    check = result["device_checks"][0]
    assert check["passed"] is True
    assert check["device_id"] == "n001"
    assert diag.iox_links_table_calls == []  # never needed -- Query worked
    assert diag.plm_links_calls == 0


@pytest.mark.asyncio
async def test_not_responding_query_succeeds_but_force_continues_to_link_checks():
    diag = _insteon_diag_for_not_responding(
        send_should_fail=False,
        iox_links_table=_links_blob(["0,controller,0,PLM,data"]),
        # PLM's table has links, but none naming this device (n001) -- a
        # real link-config problem the plain "Query succeeded" shortcut
        # would otherwise hide.
        plm_links_table=_links_blob(["0,responder,0,some-other-device,data"]),
    )

    result = await diag.diagnose_not_responding("n001", force=True)

    check = result["device_checks"][0]
    assert check["passed"] is False
    assert check["report"] == diag._KNOWN_FIXES["insteon_link_config_incorrect"]
    assert diag.plm_links_calls == 1  # force kept going instead of stopping at "Query succeeded"


@pytest.mark.asyncio
async def test_not_responding_unknown_query_command():
    diag = _insteon_diag_for_not_responding(command=None)

    result = await diag.diagnose_not_responding("n001")

    check = result["device_checks"][0]
    assert check["passed"] is False
    assert "no 'Query' command" in check["report"]
    assert diag._iox_wrapper.send_commands_calls == []


@pytest.mark.asyncio
async def test_not_responding_query_fails_missing_iox_link_table():
    diag = _insteon_diag_for_not_responding(send_should_fail=True, iox_links_table=None)

    result = await diag.diagnose_not_responding("n001")

    check = result["device_checks"][0]
    assert check["report"] == diag._KNOWN_FIXES["device_missing_iox_link_table"]
    assert diag.plm_links_calls == 0  # never reached -- stopped at the iox-table check


@pytest.mark.asyncio
async def test_not_responding_query_fails_no_plm_links_at_all():
    diag = _insteon_diag_for_not_responding(
        send_should_fail=True,
        iox_links_table=_links_blob(["0,controller,0,PLM,data"]),
        plm_links_table=None,
    )

    result = await diag.diagnose_not_responding("n001")

    check = result["device_checks"][0]
    assert check["report"] == diag._KNOWN_FIXES["plm_links_missing"]


@pytest.mark.asyncio
async def test_not_responding_query_fails_link_config_incorrect():
    diag = _insteon_diag_for_not_responding(
        send_should_fail=True,
        iox_links_table=_links_blob(["0,controller,0,PLM,data"]),
        # PLM's table has links, but none naming this device (n001).
        plm_links_table=_links_blob(["0,responder,0,some-other-device,data"]),
    )

    result = await diag.diagnose_not_responding("n001")

    check = result["device_checks"][0]
    assert check["report"] == diag._KNOWN_FIXES["insteon_link_config_incorrect"]


@pytest.mark.asyncio
async def test_not_responding_query_fails_links_check_out_signal_noise():
    diag = _insteon_diag_for_not_responding(
        send_should_fail=True,
        iox_links_table=_links_blob(["0,controller,0,PLM,data"]),
        plm_links_table=_links_blob(["0,responder,0,n001,data", "1,responder,0,n001 (Some Device),data"]),
    )

    result = await diag.diagnose_not_responding("n001")

    check = result["device_checks"][0]
    assert check["report"] == diag._KNOWN_FIXES["query_failed"]


@pytest.mark.asyncio
async def test_not_responding_full_happy_path_steps_run_order():
    diag = _insteon_diag_for_not_responding()

    result = await diag.diagnose_not_responding("n001")

    assert result["steps_run"] == [
        "quick_plm_sanity_check",
        "diagnose_insteon_device_not_responding(n001)",
    ]


# ----------------------------------------------------------------------
# INSTEONDiagnostics.diagnose_no_status_feedback
# ----------------------------------------------------------------------


def _insteon_diag_for_status_feedback(
    *,
    insteon_enabled=True,
    sanity_passed=True,
    plm_connected=True,
    plm_links_table=None,  # get_all_plm_links's raw blob (step 2, reused by 3.a)
    dev_links_table=None,  # get_dev_links_table's raw blob (3.a)
    iox_links_table=None,  # get_iox_links_table's raw blob (3.b)
    comparison_result=None,  # compare_device_links's return (3.c)
    insteon_addresses=None,  # for _sample_device_ids when device_id is omitted
) -> INSTEONDiagnostics:
    diag = object.__new__(INSTEONDiagnostics)
    diag._plm_op_state = None
    diag._iox_diagnostics = SimpleNamespace(
        _get_system_options=_async_return({"insteonSupport": insteon_enabled}),
        get_core_services_status=_async_return("ok"),
    )
    diag._quick_plm_sanity_check = _async_return(
        {"passed": sanity_passed, "plm_connected": plm_connected, "report": "report text"}
    )
    diag.dev_links_calls: list[str] = []
    diag.iox_links_calls: list[str] = []
    diag.compare_calls: list[str] = []
    diag.plm_links_calls = 0

    async def _get_all_plm_links(**kwargs):
        diag.plm_links_calls += 1
        return plm_links_table

    async def _get_dev_links_table(device_id, **kwargs):
        diag.dev_links_calls.append(device_id)
        return dev_links_table

    async def _get_iox_links_table(device_id, **kwargs):
        diag.iox_links_calls.append(device_id)
        return iox_links_table

    async def _compare_device_links(device_id, **kwargs):
        diag.compare_calls.append(device_id)
        return comparison_result

    diag._get_all_plm_links = _get_all_plm_links
    diag._get_dev_links_table = _get_dev_links_table
    diag._get_iox_links_table = _get_iox_links_table
    diag._compare_device_links = _compare_device_links
    diag._iox_wrapper = _FakeIoxWrapperNotResponding(insteon_addresses=insteon_addresses)
    return diag


@pytest.mark.asyncio
async def test_no_status_feedback_insteon_disabled():
    diag = _insteon_diag_for_status_feedback(insteon_enabled=False)

    result = await diag.diagnose_no_status_feedback(None)

    assert "not enabled" in result["diagnosis"]
    assert "recommended_fix" in result


@pytest.mark.asyncio
async def test_no_status_feedback_sanity_check_fails():
    diag = _insteon_diag_for_status_feedback(sanity_passed=False)

    result = await diag.diagnose_no_status_feedback(None)

    assert "recommended_fix" in result
    assert "clarifying_question" in result


@pytest.mark.asyncio
async def test_no_status_feedback_stops_when_plm_not_connected():
    # Shares _plm_sanity_gate with diagnose_not_responding (see
    # test_not_responding_stops_when_plm_not_connected above) -- "PLM
    # enabled but not connected" must get its own specific diagnosis/fix
    # here too, not the generic "sanity check did not pass" one.
    diag = _insteon_diag_for_status_feedback(sanity_passed=False, plm_connected=False)

    result = await diag.diagnose_no_status_feedback(None)

    assert result["diagnosis"] == "PLM is enabled but not connected."
    assert result["recommended_fix"] == diag._KNOWN_FIXES["plm_not_connected"]
    assert "clarifying_question" not in result


@pytest.mark.asyncio
async def test_no_status_feedback_group_device_id_rejected():
    diag = _insteon_diag_for_status_feedback()
    group_node = object.__new__(Group)
    group_node.name = "MyGroup"
    diag._iox_wrapper.nodes["g001"] = group_node

    result = await diag.diagnose_no_status_feedback("g001")

    assert result["steps_run"] == ["node_check"]
    assert "MyGroup" in result["diagnosis"]
    assert diag.plm_links_calls == 0


@pytest.mark.asyncio
async def test_no_status_feedback_folder_device_id_rejected():
    diag = _insteon_diag_for_status_feedback()
    folder_node = object.__new__(Folder)
    folder_node.name = "MyFolder"
    diag._iox_wrapper.nodes["f001"] = folder_node

    result = await diag.diagnose_no_status_feedback("f001")

    assert result["steps_run"] == ["node_check"]
    assert "MyFolder" in result["diagnosis"]
    assert diag.plm_links_calls == 0


@pytest.mark.asyncio
async def test_no_status_feedback_plm_has_no_links_at_all():
    diag = _insteon_diag_for_status_feedback(plm_links_table=None)

    result = await diag.diagnose_no_status_feedback("n001")

    assert result["recommended_fix"] == diag._KNOWN_FIXES["plm_links_missing"]
    assert "clarifying_question" in result
    assert diag.dev_links_calls == []


@pytest.mark.asyncio
async def test_no_status_feedback_plm_has_only_bookkeeping_links():
    diag = _insteon_diag_for_status_feedback(
        plm_links_table=_links_blob(["0,deleted,0,some-device,data", "1,end_of_table,0,,"])
    )

    result = await diag.diagnose_no_status_feedback("n001")

    assert result["recommended_fix"] == diag._KNOWN_FIXES["plm_links_missing"]
    assert diag.dev_links_calls == []


@pytest.mark.asyncio
async def test_no_status_feedback_3a_fails_missing_group_responder():
    diag = _insteon_diag_for_status_feedback(
        plm_links_table=_links_blob(["0,responder,2,some-other-device,data"]),
        dev_links_table=_links_blob(["0,controller,1,PLM,data"]),
    )

    result = await diag.diagnose_no_status_feedback("n001")

    check = result["device_checks"][0]
    assert check["passed"] is False
    assert "group(s) ['1']" in check["report"]
    assert diag.iox_links_calls == []  # short-circuited before 3.b
    assert diag.compare_calls == []  # short-circuited before 3.c


@pytest.mark.asyncio
async def test_no_status_feedback_3a_fails_no_controller_rows_at_all():
    diag = _insteon_diag_for_status_feedback(
        plm_links_table=_links_blob(["0,responder,1,n001,data"]),
        dev_links_table=_links_blob(["0,responder,1,PLM,data"]),  # device has no controller row
    )

    result = await diag.diagnose_no_status_feedback("n001")

    check = result["device_checks"][0]
    assert check["report"] == diag._KNOWN_FIXES["missing_device_to_plm_link"]
    assert diag.iox_links_calls == []


@pytest.mark.asyncio
async def test_no_status_feedback_3b_fails_iox_missing_responder():
    diag = _insteon_diag_for_status_feedback(
        plm_links_table=_links_blob(["0,responder,1,n001,data"]),
        dev_links_table=_links_blob(["0,controller,1,PLM,data"]),
        iox_links_table=_links_blob(["0,controller,1,PLM,data"]),  # no responder row
    )

    result = await diag.diagnose_no_status_feedback("n001")

    check = result["device_checks"][0]
    assert check["passed"] is False
    assert check["report"] == diag._KNOWN_FIXES["insteon_link_config_incorrect"]
    assert diag.compare_calls == []  # short-circuited before 3.c


@pytest.mark.asyncio
async def test_no_status_feedback_3c_fails_comparison_mismatch():
    diag = _insteon_diag_for_status_feedback(
        plm_links_table=_links_blob(["0,responder,1,n001,data"]),
        dev_links_table=_links_blob(["0,controller,1,PLM,data"]),
        iox_links_table=_links_blob(["0,responder,1,PLM,data"]),
        comparison_result=(
            "MISMATCH: device and iox link tables disagree -- device was likely reprogrammed outside NuCore.\n"
            "  Present on the device but NOT in NuCore's records (1):\n"
            "    role=controller, group=1, device=PLM"
        ),
    )

    result = await diag.diagnose_no_status_feedback("n001")

    check = result["device_checks"][0]
    assert check["passed"] is False
    assert check["report"] == diag._KNOWN_FIXES["insteon_link_config_incorrect"]


@pytest.mark.asyncio
async def test_no_status_feedback_3c_match_not_on_first_line_still_passes():
    # Regression test for a real footgun: _compare_links_files can prepend
    # an ANOMALIES section before the verdict line, so the verdict isn't
    # reliably the first line -- and "MISMATCH:" itself contains "MATCH:"
    # as a substring, so a naive `"MATCH:" in text` check would be fooled
    # either way. _comparison_matches must scan every line for one that
    # literally starts with "MATCH:".
    diag = _insteon_diag_for_status_feedback(
        plm_links_table=_links_blob(["0,responder,1,n001,data"]),
        dev_links_table=_links_blob(["0,controller,1,PLM,data"]),
        iox_links_table=_links_blob(["0,responder,1,PLM,data"]),
        comparison_result=(
            "ANOMALIES (unrecognized flag byte -- data integrity issue, independent of the comparison below):\n"
            "  device file, idx 3: role=unrecognized_flag(FF), group=1, device=PLM, data=x\n"
            "MATCH: device and iox link tables agree (deleted/end_of_table records excluded)."
        ),
    )

    result = await diag.diagnose_no_status_feedback("n001")

    assert result["device_checks"][0]["passed"] is True


@pytest.mark.asyncio
async def test_no_status_feedback_full_match_explicit_device_passes():
    diag = _insteon_diag_for_status_feedback(
        plm_links_table=_links_blob(["0,responder,1,n001,data"]),
        dev_links_table=_links_blob(["0,controller,1,PLM,data"]),
        iox_links_table=_links_blob(["0,responder,1,PLM,data"]),
        comparison_result="MATCH: device and iox link tables agree (deleted/end_of_table records excluded).",
    )

    result = await diag.diagnose_no_status_feedback("n001")

    assert result["device_checks"] == [
        {
            "device_id": "n001",
            "passed": True,
            "report": "n001 is correctly linked to report status (groups ['1'] all confirmed).",
        }
    ]


@pytest.mark.asyncio
async def test_no_status_feedback_full_match_sampled_devices_passes():
    diag = _insteon_diag_for_status_feedback(
        plm_links_table=_links_blob(
            ["0,responder,1,25 80 3C 1,data", "1,responder,1,17 F8 44 1,data"]
        ),
        dev_links_table=_links_blob(["0,controller,1,PLM,data"]),
        iox_links_table=_links_blob(["0,responder,1,PLM,data"]),
        comparison_result="MATCH: device and iox link tables agree (deleted/end_of_table records excluded).",
        insteon_addresses=["25 80 3C 1", "17 F8 44 1"],
    )

    result = await diag.diagnose_no_status_feedback(None)

    assert len(result["device_checks"]) == 2
    assert all(c["passed"] for c in result["device_checks"])
    checked_ids = {c["device_id"] for c in result["device_checks"]}
    assert checked_ids == {"25 80 3C 1", "17 F8 44 1"}


@pytest.mark.asyncio
async def test_no_status_feedback_no_device_found_to_sample():
    diag = _insteon_diag_for_status_feedback(
        plm_links_table=_links_blob(["0,responder,1,PLM,data"]),
        insteon_addresses=[],
    )

    result = await diag.diagnose_no_status_feedback(None)

    assert result["status"] == "no insteon device found"


@pytest.mark.asyncio
async def test_no_status_feedback_full_happy_path_steps_run_order():
    diag = _insteon_diag_for_status_feedback(
        plm_links_table=_links_blob(["0,responder,1,n001,data"]),
        dev_links_table=_links_blob(["0,controller,1,PLM,data"]),
        iox_links_table=_links_blob(["0,responder,1,PLM,data"]),
        comparison_result="MATCH: device and iox link tables agree (deleted/end_of_table records excluded).",
    )

    result = await diag.diagnose_no_status_feedback("n001")

    assert result["steps_run"] == [
        "quick_plm_sanity_check",
        "get_all_plm_links",
        "diagnose_insteon_device_no_status_feedback(n001)",
    ]


@pytest.mark.asyncio
async def test_no_status_feedback_plm_busy_refusal_propagates():
    # Simulates step 2's own get_all_plm_links() call hitting the
    # _begin_plm_op busy guard -- the real guard mechanics are already
    # covered by test_plm_exclusion.py; this only proves
    # diagnose_no_status_feedback surfaces that refusal as its own error
    # instead of silently misreading it as "no real link records."
    diag = _insteon_diag_for_status_feedback()
    diag.get_all_plm_links = _async_return(
        {"error": "a PLM operation ('get_all_plm_links') is already in progress -- try again shortly"}
    )

    result = await diag.diagnose_no_status_feedback("n001")

    assert result["error"] == "a PLM operation ('get_all_plm_links') is already in progress -- try again shortly"
    assert diag.dev_links_calls == []
