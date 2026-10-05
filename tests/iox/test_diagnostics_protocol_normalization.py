"""IoXDiagnostics.diagnose_not_responding / diagnose_no_status_feedback must
accept the protocol spelling get_device_family itself returns ("Z-Wave",
"INSTEON"), not only the lowercase/no-hyphen form the tool enums use -- the
tool descriptions tell the model to pass get_device_family's answer straight
through, and "Z-Wave".lower() used to fall through to a false "isn't
implemented yet" error.
"""

from __future__ import annotations

import pytest

from iox.diagnostics.iox_diagnostics import IoXDiagnostics
from iox.iox_definitions import normalize_protocol_name


class _FakeWrapper:
    def __init__(self):
        self.enabled_calls = []

    async def is_protocol_enabled(self, protocol):
        self.enabled_calls.append(protocol)
        return True


def _bare_diagnostics() -> IoXDiagnostics:
    diag = object.__new__(IoXDiagnostics)
    diag._iox_wrapper = _FakeWrapper()
    return diag


def test_normalize_protocol_name_covers_every_spelling_in_play():
    assert normalize_protocol_name("Z-Wave") == "zwave"
    assert normalize_protocol_name("zwave") == "zwave"
    assert normalize_protocol_name("ZWave") == "zwave"
    assert normalize_protocol_name("INSTEON") == "insteon"
    assert normalize_protocol_name("Zigbee") == "zigbee"
    assert normalize_protocol_name("Matter") == "matter"
    assert normalize_protocol_name("Legacy Z-Wave") == "legacy zwave"
    assert normalize_protocol_name(None) == ""


@pytest.mark.asyncio
async def test_diagnose_not_responding_accepts_get_device_family_spelling():
    diag = _bare_diagnostics()

    result = await diag.diagnose_not_responding("Z-Wave", None)

    assert "error" not in result
    assert diag._iox_wrapper.enabled_calls == ["zwave"]


@pytest.mark.asyncio
async def test_diagnose_not_responding_still_rejects_unknown_protocol():
    diag = _bare_diagnostics()

    result = await diag.diagnose_not_responding("x10", None)

    assert "isn't implemented yet" in result["error"]


@pytest.mark.asyncio
async def test_diagnose_no_status_feedback_accepts_get_device_family_spelling():
    diag = _bare_diagnostics()
    seen = []

    class _FakeInsteon:
        async def diagnose_no_status_feedback(self, device_id):
            seen.append(device_id)
            return {"diagnosis": "ok"}

    diag._init_insteon_diag = lambda device_id: True
    diag._insteon_diag = _FakeInsteon()

    result = await diag.diagnose_no_status_feedback("INSTEON", "n001")

    assert result == {"diagnosis": "ok"}
    assert seen == ["n001"]
