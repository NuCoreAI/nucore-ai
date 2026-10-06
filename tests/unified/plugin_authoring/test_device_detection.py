"""detect_usb_device (design/developers/plugin_authoring_p4_impl.md Stage
6) -- pure before/after diffing, no shell exec and no hub access, so every
test here passes None for nucore_interface.
"""

from __future__ import annotations

import pytest

from unified.plugin_authoring.handlers.device_detection import detect_usb_device


@pytest.mark.asyncio
async def test_requires_both_snapshots():
    result = await detect_usb_device(None, {"before": "a"})
    assert "error" in result


@pytest.mark.asyncio
async def test_no_new_lines_is_an_error():
    result = await detect_usb_device(None, {"before": "line1\nline2", "after": "line1\nline2"})
    assert "error" in result


@pytest.mark.asyncio
async def test_extracts_vendor_product_from_lsusb_style_line():
    before = "Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub"
    after = before + "\nBus 001 Device 005: ID 10c4:ea60 Silicon Labs CP210x UART Bridge"

    result = await detect_usb_device(None, {"before": before, "after": after})

    assert result["vendor_id"] == "10c4"
    assert result["product_id"] == "ea60"
    assert "ea60" in result["matched_line"]


@pytest.mark.asyncio
async def test_new_line_without_a_parseable_pair_returns_new_lines_and_a_note():
    result = await detect_usb_device(None, {"before": "", "after": "/dev/ttyUSB0"})
    assert "error" not in result
    assert result["new_lines"] == ["/dev/ttyUSB0"]
    assert "note" in result
