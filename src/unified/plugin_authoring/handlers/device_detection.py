"""``detect_usb_device`` (design/developers/plugin_authoring_p4_impl.md
Stage 6): a thin diffing utility over two snapshots of a USB/serial
device-enumeration command's output (e.g. ``lsusb`` / ``ls -la /dev/tty*``)
-- one taken before the customer plugs the device in, one after. The
shell commands themselves run through the reused ``run_shell_command``
tool; this tool never execs anything itself, it only diffs the two
snapshots and best-effort-extracts a vendor_id/product_id pair from
whatever's new, for ``generate_plugin_scaffold``'s ``server_entry.devd``
field.
"""

from __future__ import annotations

import re
from typing import Any

from nucore import NuCoreInterface

# The one roughly cross-platform constant in USB enumeration output --
# matches both 'lsusb'-style "ID 10c4:ea60" and a bare "10c4:ea60" pair.
_VENDOR_PRODUCT_RE = re.compile(r"\b([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\b")


def _new_lines(before: str, after: str) -> list[str]:
    before_lines = set(before.splitlines())
    return [line for line in after.splitlines() if line.strip() and line not in before_lines]


async def detect_usb_device(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    """*nucore_interface* is unused -- this tool never touches the hub."""
    before = args.get("before")
    after = args.get("after")
    if not isinstance(before, str) or not isinstance(after, str):
        return {
            "error": "both 'before' and 'after' are required -- run the same enumeration command via "
                     "run_shell_command once before and once after the customer plugs the device in"
        }

    new_lines = _new_lines(before, after)
    if not new_lines:
        return {
            "error": "no new lines between 'before' and 'after' -- the device may not have been detected yet; "
                     "confirm with the customer that it's plugged in and retry"
        }

    for line in new_lines:
        match = _VENDOR_PRODUCT_RE.search(line)
        if match:
            return {
                "vendor_id": match.group(1).lower(),
                "product_id": match.group(2).lower(),
                "matched_line": line,
                "new_lines": new_lines,
            }

    return {
        "new_lines": new_lines,
        "note": "new output appeared but no vendor_id:product_id pair could be parsed from it -- inspect "
                "'new_lines' and, if this plugin needs a devd hardware-attach rule, ask the customer to rerun "
                "with a more detailed enumeration command (e.g. 'lsusb -v' or 'usbdevs -v')",
    }
