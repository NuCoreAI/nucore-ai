# -----------------------------------------------------------------
#
# General Insteon Link information:
#
# e.g. Master Link in 7.D5.27 (button H groupNum=8) linked to Slave Link in 7.EB.6D (Button D groupNum=6)
#
#  Master Link in 7.D5.27
#    E2 08 07 EB 6D FE 1F 08
#      E2       - Flags: Active master link record
#      08       - Group number of button on this controller
#      07 EB 6D - Address of responder
#      FE 1F    - Default values (unused)
#      08       - Group number of button on this controller
#
#  Slave Link in 7.EB.6D
#    A2 08 07 D5 27 3E 1F 06
#      A2       - Flags: Active slave link record
#      08       - Group number of button on controller (Button 'H' on 7.D5.27)
#      07 D5 27 - Address of controller (7.D5.27)
#      FE 1F    - Link values, usually on-level/ramp rate
#      06       - Group number of button on this device (Button 'D' on 7.EB.6D)
#                 Note: For other devices such as thermostat, this is not a group
#                       number, it is a data value.
#
# -----------------------------------------------------------------
# Copyright (C) 2009  Universal Devices
# -----------------------------------------------------------------


# -----------------------------------------------------------------
# PLM LINKS TABLE
# Decoded record:
# ix = 189
# ad = 189
# fl = 162 = 0xA2
# gr = 6
# id = 5352589 = 0x51AC8D
# data = 142404 = 0x022C44 (bytes 02 2C 44)
# What it means:

# This is from PLM link scan output
# In your PLM sender, ad is intentionally set equal to ix (not DB address): InsteonDiag.h:33

# fl = 0xA2 means active slave/responder link
# That matches the code constants for link flag patterns: InsteonType.h:324

# Your statement about logical delete using 0x22 is correct
# 0x22 is the same base pattern as 0xA2 with the in-use bit cleared, so it is effectively a logical/inactive deleted slave entry.
# The codebase constants explicitly define active master/slave as 0xE2/0xA2: InsteonType.h:324

# Group and target
# gr = 6 is the All-Link group, and id = 0x51AC8D is the linked device id.

# Data bytes
# data = 0x02 0x2C 0x44 are device/link-specific parameters (often on-level/ramp/group context depending on device type).

# So this specific sample is an active slave/responder PLM link (not deleted), and yes, deleted entries appearing as 0x22 fits the same flag scheme.

# In this context, “slave in PLM link” means the PLM is the slave (responder), not the other way around.

# The code states this directly:

# 0x00: “IM is a responder (slave)” in InsteonType.h:319
# 0x01: “IM is a controller (master)” in InsteonType.h:320
# Here IM = Insteon Modem Interface (your PLM).

# So for your record with fl = 0xA2 (slave-style flags), interpret it as:
#
# the PLM has a responder/slave link entry
# the other device/group acts as controller/master for that relationship.


import asyncio
from collections import Counter
import json
import os
import queue
import random
import re
import time
from typing import Any, Awaitable, Callable, TYPE_CHECKING, Literal
from urllib.parse import quote

from nucore import DeviceEventListener, Group, Folder

from ..iox_definitions import IoXSOAPAction
from ..iox_wrapper import IoXWrapper

if TYPE_CHECKING:
    from .iox_diagnostics import IoXDiagnostics

from utils import get_logger
logger = get_logger(__name__)
already_running_message = "Diagnostics already running. Please wait for the current operation to finish."
no_device_id_message = "Device ID is required for getting device links table."

# Raw flag byte -> decoded role. Doing this in code (not leaving it for the
# model to map from a legend) since it's the one part of a link record we
# have a precise, confirmed mapping for.
_FLAG_ROLES = {
    "A2": "responder",
    "E2": "controller",
    "22": "deleted",
    "EA": "high_water_mark",
    "00": "end_of_table",
}


def _decode_role(flag_hex: str) -> str:
    return _FLAG_ROLES.get(flag_hex, f"unrecognized_flag({flag_hex})")


def _format_data_fields(role: str, data_int: int) -> str:
    """Label the 3 data bytes by what they mean for *role* -- inline, per
    record, instead of a legend the model would otherwise have to hold onto
    and re-apply for every row. No confirmed formula exists in this codebase
    for converting these to real units (on-level %, ramp-rate seconds), so
    values are left as raw hex2.

    ``deleted``/``unrecognized_flag`` rows get generic byte1/2/3 labels since
    their original semantic role (controller- or responder-shaped) can't be
    confirmed from the flag alone.
    """
    b1 = (data_int >> 16) & 0xFF
    b2 = (data_int >> 8) & 0xFF
    b3 = data_int & 0xFF
    if role == "controller":
        return f"button_group={b1:02X};reserved={b2:02X};group={b3:02X}"
    if role == "responder":
        return f"on_level={b1:02X};ramp_rate={b2:02X};group_or_data={b3:02X}"
    return f"byte1={b1:02X};byte2={b2:02X};byte3={b3:02X}"


def _extract_std_cleanup_ack_devices(events: list[str]) -> list[str]:
    """Literal port of the reference Java scene-test analyzer: pull the
    responding device's dotted Insteon address out of each
    "[Std-Cleanup Ack]" trace line -- it sits between the line's first
    "] " and the following "-->", e.g.
    "...[Std-Cleanup Ack] 12.34.56 --> 11.22.33..." yields "12.34.56".

    Ported as-is (searching from the start of the string, not from after
    the marker) -- only correct if "[Std-Cleanup Ack]" is the first
    bracketed token in the line, same assumption the source Java makes.
    """
    devices = []
    for event in events:
        if "[Std-Cleanup Ack]" not in event:
            continue
        bi = event.find("] ")
        if bi <= 0:
            continue
        bi += 2
        ei = event.find("-->")
        if ei <= 0:
            continue
        devices.append(event[bi:ei].strip())
    return devices


def _dotted_insteon_address_to_nucore(dotted: str) -> str:
    """"0F.18.08" -> "F 18 8" -- treat the dotted address as a 6-hex-digit
    number (one byte per dot-separated octet) and format each byte back
    without leading-zero padding, same convention as format_links_event's
    own device_address construction elsewhere in this file. No instance
    suffix here -- see _strip_instance_suffix, which strips a scene
    member's own trailing instance digit instead, so both sides of the
    comparison end up in this same unsuffixed form.
    """
    hex6 = dotted.replace(".", "")
    return " ".join(f"{int(hex6[i:i + 2], 16):X}" for i in (0, 2, 4))


def _strip_instance_suffix(address: str) -> str:
    """"F 18 8 1" -> "F 18 8" -- drop a scene member's trailing
    single-digit instance number so its address matches the unsuffixed
    form _dotted_insteon_address_to_nucore produces from the raw event
    trace (which has no instance of its own). A real node/group address
    always carries this suffix (see NodeBase.instance), so this is
    unconditional -- no fallback for one that doesn't."""
    prefix, _, _suffix = address.rpartition(" ")
    return prefix


LINKS_TABLE_NOTE = (
    "# `data` is a semicolon-separated set of labeled hex2 byte values -- the label already tells you what\n"
    "# each byte means for that row's role; no further legend lookup needed.\n"
)
# Fenced so the table's start/end is unambiguous -- everything between the
# fences is the CSV to parse, everything outside it is prose.
LINKS_TABLE_FENCE_OPEN = "```csv\n"
LINKS_TABLE_FENCE_CLOSE = "```\n"
LINKS_TABLE_HEADER = "idx,role,group,device,data\n"
_CSV_BLOCK_RE = re.compile(r"```csv\s*\n(.*?)```", re.DOTALL)

# The only roles _decode_role can ever produce -- anything else means the
# flag byte didn't match a known pattern (see _decode_role's fallback).
_KNOWN_ROLES = frozenset(_FLAG_ROLES.values())
# Not real, comparable links -- excluded from the device-vs-iox comparison
# entirely (see _compare_links_files) and from the PLM sanity check's record
# count (see _quick_plm_sanity_check). high_water_mark is a bookkeeping
# marker that can legitimately exist on one side and not the other -- never
# a real mismatch.
_ROLES_EXCLUDED_FROM_COMPARISON = frozenset({"deleted", "end_of_table", "high_water_mark"})

# See _quick_plm_sanity_check.
_PLM_SANITY_CHECK_TOLERANCE_PCT = 20

# See _get_all_plm_links's cache check -- a full PLM link scan is a slow,
# real hardware operation, so a recent result is reused by default rather
# than re-scanning on every call.
_PLM_LINKS_CACHE_MAX_AGE_S = 3600  # 1 hour
# A real PLM links dump for any system with more than a handful of devices
# is comfortably larger than this -- guards against treating a truncated or
# otherwise corrupted partial write as a valid, usable cache.
_PLM_LINKS_CACHE_MIN_SIZE_BYTES = 5000

# See _stream_links_into_file -- max time to wait for the NEXT streamed
# link-table record (or the first one, right after the triggering POST)
# before giving up. This is a rolling/inactivity timeout, not a fixed
# overall ceiling: it resets on every record received, so a device that
# streams slowly but steadily (e.g. one record every 20-30s on a large
# table -- older devices can take ~30s just for the first record) is never
# cut off just because the whole scan runs long. Only a genuine stall -- no
# record at all for a full _LINKS_STREAM_MAX_GAP_TIMEOUT_S seconds, with
# neither end_of_table nor a "system no longer busy" signal seen -- ends
# the drain.
_LINKS_STREAM_MAX_GAP_TIMEOUT_S = 35.0

# See _stream_scene_test_into_file -- unlike the links stream above, a raw
# scene-test group-off has no completion signal at all (no end_of_table, no
# "system no longer busy" event to watch for) -- the gap timeout is the only
# stop condition. 5s of silence after the last _7/"1" progress event (or
# since the trigger POST, for the first) ends the collection.
_SCENE_TEST_MAX_GAP_TIMEOUT_S = 5.0


def _is_cache_fresh(
    file_path: str,
    max_age_s: int = _PLM_LINKS_CACHE_MAX_AGE_S,
    min_size_bytes: int = _PLM_LINKS_CACHE_MIN_SIZE_BYTES,
) -> bool:
    """True if *file_path* exists, was last written less than *max_age_s*
    seconds ago, AND is at least *min_size_bytes* -- both conditions must
    hold for a cached file to be considered valid to serve."""
    try:
        stat = os.stat(file_path)
    except OSError:
        return False
    return (time.time() - stat.st_mtime) < max_age_s and stat.st_size >= min_size_bytes


def _parse_links_csv(text: str) -> list[dict[str, str]]:
    """Extract a links-table file's fenced ```csv block as a list of
    ``{"idx", "role", "group", "device", "data"}`` dicts -- the title line,
    note comments, fences, and the idx/role/group/device/data header row are
    all stripped. Duplicate rows (if any) are preserved, in file order --
    deduplication is the caller's job, since whether duplicates matter
    depends on what's being asked (see _compare_links_files)."""
    match = _CSV_BLOCK_RE.search(text)
    if not match:
        raise ValueError("no fenced ```csv block found in links file")

    lines = [line for line in match.group(1).splitlines() if line.strip()]
    if not lines:
        return []

    rows = []
    for line in lines[1:]:  # lines[0] is the "idx,role,group,device,data" header
        parts = line.split(",")
        if len(parts) != 5:
            continue  # malformed row -- skip rather than crash the whole comparison
        idx, role, group, device, data = parts
        rows.append({"idx": idx, "role": role, "group": group, "device": device, "data": data})
    return rows


def _has_role_row(raw_links_text: str | None, role: str) -> bool | None:
    """Whether a rendered links-table blob has a row with the given *role*
    ("controller" or "responder"). Generalized out of what used to be
    _has_controller_role_row-only logic, now that
    diagnose_no_status_feedback's step 3.b needs the identical scan for
    "responder" instead of "controller" -- see _has_controller_role_row/
    _iox_table_has_responder_link for what each role means on a device's
    own table.

    :return: None when *raw_links_text* couldn't be retrieved/parsed at
        all (PLM not connected, device not found, busy-refusal dict, etc.),
        else a real bool.
    """
    if not raw_links_text or LINKS_TABLE_FENCE_OPEN not in raw_links_text:
        return None
    rows = _parse_links_csv(raw_links_text)
    return any(r["role"] == role for r in rows if r["role"] not in _ROLES_EXCLUDED_FROM_COMPARISON)


def _has_controller_role_row(raw_links_text: str | None) -> bool | None:
    """Whether a rendered links-table blob (get_dev_links_table's or
    get_iox_links_table's fenced-CSV output) has a "controller" role row --
    on an ordinary end device's own table, a controller-role row always
    represents that device's link to the PLM (see the module docstring's
    general Insteon link notes above), so no address match against the PLM
    is needed here, unlike _plm_table_has_responder_for below (the PLM's own
    table lists many devices, so that one does need to match by address).
    Thin wrapper over _has_role_row -- kept under its own name since
    get_device_to_plm_link_status and _iox_table_has_controller_link
    already call it by this name.

    :return: None when *raw_links_text* couldn't be retrieved/parsed at all
        (PLM not connected, device not found, etc.), else a real bool.
    """
    return _has_role_row(raw_links_text, "controller")


def _device_controller_groups(raw_dev_links_text: str | None) -> set[str] | None:
    """The distinct group numbers found on a device's own controller-role
    rows (its device->PLM status-feedback links, per the module docstring's
    general Insteon link notes) -- i.e. every group this device can report
    an unsolicited status change for. A device can have more than one such
    group (e.g. a multi-button KeypadLinc, or an independent sensor group)
    -- diagnose_no_status_feedback's step 3.a must confirm the PLM has a
    matching responder link for EACH of these, not just any single one
    (contrast _plm_table_has_responder_for, which intentionally checks
    only "any responder row for this address at all", ignoring group, for
    diagnose_not_responding's different control-direction need -- left
    unchanged).

    Group numbers are rendered as either "N" or "N (Name)" -- the same
    optional-annotation shape as get_all_plm_links's device column -- so
    any "(Name)" suffix is stripped before returning, the same
    strip-and-compare convention _plm_table_has_responder_for already uses
    for its device-address field.

    :return: None when *raw_dev_links_text* couldn't be retrieved/parsed
        at all; else the (possibly empty) set of raw group-number strings.
    """
    if not raw_dev_links_text or LINKS_TABLE_FENCE_OPEN not in raw_dev_links_text:
        return None
    rows = _parse_links_csv(raw_dev_links_text)
    return {r["group"].split(" (")[0].strip() for r in rows if r["role"] == "controller"}


def _plm_responder_groups_for(device_id: str, plm_links_raw: str | None) -> set[str] | None:
    """The set of group numbers for which *plm_links_raw* (an
    already-fetched get_all_plm_links blob) has a responder-role row
    naming *device_id* -- i.e. every group the PLM currently has a working
    device->PLM feedback link for, from the PLM's own link database.
    Paired with _device_controller_groups by diagnose_no_status_feedback's
    step 3.a to find groups the device expects to report on that the PLM
    doesn't actually have a responder link for.

    Unlike _plm_table_has_responder_for (address-only, explicitly ignores
    group -- see its docstring), this keys on (address, group) together,
    since a multi-group device can have a working link for one group and
    a broken one for another.

    :return: None if *plm_links_raw* couldn't be parsed at all (shouldn't
        normally happen here -- diagnose_no_status_feedback's own step 2
        already validated it before any device is checked).
    """
    if not plm_links_raw or LINKS_TABLE_FENCE_OPEN not in plm_links_raw:
        return None
    rows = _parse_links_csv(plm_links_raw)
    return {
        r["group"].split(" (")[0].strip()
        for r in rows
        if r["role"] == "responder" and r["device"].split(" (")[0].strip() == device_id
    }


def _comparison_matches(comparison: Any) -> bool:
    """Interpret compare_device_links's return value as pass/fail.

    compare_device_links can return a busy-refusal dict (``{"error": ...}``),
    a plain failure string ("Failed to retrieve...", "PLM not connected...",
    etc.), or its real plain-text report. Only a line that literally
    **starts with** "MATCH:" counts as a pass -- two footguns this guards
    against: (1) _compare_links_files can prepend an "ANOMALIES..." and/or
    "DUPLICATE ROWS..." section *before* the verdict line, so the verdict
    is not reliably the first line -- every line must be checked, not just
    line 0; (2) a naive ``"MATCH:" in text`` substring check is unsafe even
    then, since the string "MISMATCH:" itself contains "MATCH:" as a
    substring (mis-**MATCH:**) -- ``.startswith`` on each line avoids that.
    Anything not a plain str (a busy-refusal dict, a bare failure string,
    no recognizable verdict line) counts as a fail.
    """
    if not isinstance(comparison, str):
        return False
    return any(line.startswith("MATCH:") for line in comparison.splitlines())


class _LinksTableWaiter(DeviceEventListener):
    """Passive notify() target, scoped to one links-streaming operation's
    expected action ("1"=plm, "2"=device, "3"=iox). Mirrors
    ``src/unified/handlers/_event_wait.py``'s ``_RegisteredWaiter``: never
    started as a Thread -- ``process()`` is required by the base class but
    never actually invoked (``.start()``/``.run()`` are never called). Only
    ever constructed inside ``INSTEONDiagnostics._stream_links_into_file`` --
    nothing else touches it directly, same as ``_RegisteredWaiter`` is
    private to ``_event_wait.py``.

    ``_stream_links_into_file`` additionally registers this same instance a
    second time, directly via ``register_listener`` (bypassing
    ``__init__``), for ("_5", "0") -- the hub's "system no longer busy"
    signal -- so both event streams land on the one shared ``_queue``."""

    def process(self):
        return None


class _SceneTestListener(DeviceEventListener):
    """Passive notify() target for one scene_test operation's raw group-off
    responses (control "_7", action "1" -- UD_PROGRESS_EVENT_UPDATE, see
    design/iox_apis/subscription_events.md). Same shape as
    _LinksTableWaiter: never started as a Thread, process() unused. Only
    ever constructed inside INSTEONDiagnostics._stream_scene_test_into_file.

    Unlike _LinksTableWaiter, there's no second registration for a
    completion signal -- a raw group-off has no end_of_table/"system no
    longer busy" equivalent, so the gap timeout is the only stop
    condition."""

    def process(self):
        return None


class INSTEONDiagnostics:
    """Class for Insteon diagnostics and link management."""

    def __init__(self, iox_wrapper: IoXWrapper) -> None:
        self._iox_wrapper = iox_wrapper
        self._is_running = False
        self._file_path = None
        self._plm_address = None
        self._plm_connected = False
        self._refresh_plm_links = False
        # Tracks whichever of the four PLM-exclusive methods (get_dev_links_table/
        # compare_device_links/get_all_plm_links/quick_plm_sanity_check) is
        # currently in flight, if any -- {"step"} or None. This is a
        # hardware-availability fact (the PLM serial connection can only run
        # one link/config operation at a time), not something scoped to a
        # conversation. See _begin_plm_op/_end_plm_op below -- independent of
        # self._is_running above, which guards the lower-level streaming
        # primitives (_stream_links_into_file); the two are separate guards
        # for separate concerns and are not merged.
        self._plm_op_state: dict[str, Any] | None = None
        # Set once, by IoXDiagnostics._init_insteon_diag, right after
        # constructing this instance -- lets quick_plm_sanity_check (the
        # only reader) reach the two genuinely multi-protocol calls it still
        # needs (_get_system_options/get_core_services_status) without
        # duplicating their fetch/parse logic here. Stays None for a caller
        # that constructs this class directly, never through IoXDiagnostics.
        self._iox_diagnostics: "IoXDiagnostics | None" = None

    async def _get_dev_links_table(self, device_id: str = None, **kwargs) -> str | None:
        # NOTE: assumes `node` here accepts the same device address used
        # elsewhere in this system (e.g. get_property's device_id) --
        # unconfirmed against real hub behavior; flag/verify before relying
        # on this for a real customer-facing diagnosis.
        if device_id is None:
            logger.warning(no_device_id_message)
            return no_device_id_message
        if self._is_running:
            logger.warning(already_running_message)
            return already_running_message
        self._plm_connected, plm_info = await self._get_plm_info()
        self._plm_address = self._get_plm_address(plm_info)

        self._is_running = True
        self._file_path = self._get_file_path("device", device_id)
        if self._plm_connected:
            await self._write_to_file(self._file_path, f"Device Links Table for {device_id} using PLM address {self._plm_address}\n{LINKS_TABLE_NOTE}{LINKS_TABLE_FENCE_OPEN}{LINKS_TABLE_HEADER}", mode="w")
        else:
            self._is_running = False
            return "PLM not connected. Cannot retrieve device links table."

        async def _trigger() -> None:
            # Fire-and-forget -- this backend's response to this POST is
            # immaterial (see _stream_links_into_file); the event stream is
            # the only thing that matters.
            await self._iox_wrapper.post(
                self._iox_wrapper._family_api_path(f"node/{quote(device_id, safe='')}/links/device"), ""
            )

        try:
            completed = await self._stream_links_into_file("2", self._file_path, "device", _trigger)
            if not completed:
                logger.warning(
                    f"get_dev_links_table for {device_id}: timed out waiting for end_of_table -- table may be incomplete."
                )
            await self._add_ending_to_file()
            rc = await self._read_from_file(self._file_path)
        finally:
            self._is_running = False
            self._file_path = None
        return rc

    async def _get_iox_links_table(self, device_id: str = None, **kwargs) -> str | None:
        # NOTE: assumes `node` here accepts the same device address used
        # elsewhere in this system (e.g. get_property's device_id) --
        # unconfirmed against real hub behavior; flag/verify before relying
        # on this for a real customer-facing diagnosis.
        # Use this method to get the ISY/IoX links table for a specific device. 
        # The ISY links table shows what isy/iox thinks the device link should look like.
        if device_id is None:
            logger.warning(no_device_id_message)
            return no_device_id_message
        if self._is_running:
            logger.warning(already_running_message)
            return already_running_message
        self._plm_connected, plm_info = await self._get_plm_info()
        self._plm_address = self._get_plm_address(plm_info)
        self._is_running = True
        self._file_path = self._get_file_path("iox", device_id)
        if self._plm_connected:
            await self._write_to_file(self._file_path, f"IoX Links Table for {device_id} using PLM address {self._plm_address}\n{LINKS_TABLE_NOTE}{LINKS_TABLE_FENCE_OPEN}{LINKS_TABLE_HEADER}", mode="w")
        else:
            await self._write_to_file(self._file_path, f"IoX Links Table for {device_id} (PLM not connected)\n{LINKS_TABLE_NOTE}{LINKS_TABLE_FENCE_OPEN}{LINKS_TABLE_HEADER}", mode="w")

        async def _trigger() -> None:
            await self._iox_wrapper.post(
                self._iox_wrapper._family_api_path(f"node/{quote(device_id, safe='')}/links/iox"), ""
            )

        try:
            completed = await self._stream_links_into_file("3", self._file_path, "iox", _trigger)
            if not completed:
                logger.warning(
                    f"get_iox_links_table for {device_id}: timed out waiting for end_of_table -- table may be incomplete."
                )
            await self._add_ending_to_file()
            rc = await self._read_from_file(self._file_path)
        finally:
            self._is_running = False
            self._file_path = None
        return rc

    async def _get_all_plm_links(self, refresh_plm_links: bool = False, **kwargs) -> str | None:
        # Get all PLM links -- system-wide, not scoped to any one device. A
        # full PLM scan is slow, real hardware I/O and the PLM's own link
        # database rarely changes minute-to-minute, so a recent-enough
        # result is served from disk instead of re-scanning every call --
        # unless refresh_plm_links is set (the model sets this when the
        # customer explicitly asks for a fresh scan).
        if self._is_running:
            logger.warning(already_running_message)
            return already_running_message

        # PLM connectivity is a single cheap SOAP call -- always check it
        # live, even on a cache hit, so callers reporting self._plm_connected
        # (e.g. quick_plm_sanity_check) never see stale connectivity from
        # before the cache was populated. Only the expensive full link-table
        # scan below is what gets cached.
        self._plm_connected, plm_info = await self._get_plm_info()
        self._plm_address = self._get_plm_address(plm_info)
        if not self._plm_connected:
            return "PLM not connected. Cannot retrieve PLM links table."

        cache_path = self._get_file_path("plm", None)
        force_refresh = refresh_plm_links or self._refresh_plm_links
        if not force_refresh and _is_cache_fresh(cache_path):
            return await self._read_from_file(cache_path)

        self._is_running = True
        self._file_path = cache_path
        await self._write_to_file(self._file_path, f"PLM Links Table for PLM address {self._plm_address}\n{LINKS_TABLE_NOTE}{LINKS_TABLE_FENCE_OPEN}{LINKS_TABLE_HEADER}", mode="w")

        async def _trigger() -> None:
            await self._iox_wrapper.post(self._iox_wrapper._family_api_path("plm-links"), "")

        try:
            completed = await self._stream_links_into_file("1", self._file_path, "plm", _trigger)
            if not completed:
                logger.warning("get_all_plm_links: timed out waiting for end_of_table -- table may be incomplete.")
            await self._add_ending_to_file()
            self._refresh_plm_links = False  # satisfied -- next call can use cache again
            rc = await self._read_from_file(self._file_path)
        finally:
            self._is_running = False
            self._file_path = None
        return rc

    def _compare_links_files(self, device_file_path: str, iox_file_path: str) -> str:
        """Compare a device's live link table (get_dev_links_table's output
        file) against NuCore's own replica of it (get_iox_links_table's
        output file), and return a plain-text report of whether they agree.

        - ``deleted``/``end_of_table`` records are excluded from the
          comparison entirely -- they aren't real, comparable links.
        - Records whose flag byte didn't decode to a known role (role ==
          "unrecognized_flag(XX)") are always flagged as data-integrity
          anomalies, wherever they appear, independent of whether the two
          files otherwise agree.
        - A link's identity for comparison is (role, group, device) -- if
          that identity exists on both sides but with different ``data``,
          it's reported as reprogrammed with different parameters, not as
          missing.
        - Duplicate rows (identical role/group/device/data appearing more
          than once) within a single file are reported separately, since
          that's its own data-integrity concern independent of whether the
          two files agree with each other -- naively diffing line-by-line
          without deduplicating first would otherwise report every
          duplicated row as a spurious mismatch.
        """
        with open(device_file_path, "r") as f:
            device_rows = _parse_links_csv(f.read())
        with open(iox_file_path, "r") as f:
            iox_rows = _parse_links_csv(f.read())

        report: list[str] = []

        def _anomalies(rows: list[dict[str, str]]) -> list[dict[str, str]]:
            return [r for r in rows if r["role"] not in _KNOWN_ROLES]

        device_anomalies = _anomalies(device_rows)
        iox_anomalies = _anomalies(iox_rows)
        if device_anomalies or iox_anomalies:
            report.append(
                "ANOMALIES (unrecognized flag byte -- data integrity issue, independent of the comparison below):"
            )
            for label, anomalies in (("device", device_anomalies), ("iox", iox_anomalies)):
                for r in anomalies:
                    report.append(
                        f"  {label} file, idx {r['idx']}: role={r['role']}, group={r['group']}, "
                        f"device={r['device']}, data={r['data']}"
                    )

        def _comparable(rows: list[dict[str, str]]) -> list[dict[str, str]]:
            return [r for r in rows if r["role"] not in _ROLES_EXCLUDED_FROM_COMPARISON]

        device_comparable = _comparable(device_rows)
        iox_comparable = _comparable(iox_rows)

        def _duplicates(rows: list[dict[str, str]]) -> list[tuple[str, str, str, str]]:
            counts = Counter((r["role"], r["group"], r["device"], r["data"]) for r in rows)
            return [key for key, count in counts.items() if count > 1]

        for label, rows in (("device", device_comparable), ("iox", iox_comparable)):
            dupes = _duplicates(rows)
            if dupes:
                report.append(f"DUPLICATE ROWS in {label} file ({len(dupes)} distinct record(s) repeated):")
                for role, group, dev_id, data in dupes:
                    report.append(f"  role={role}, group={group}, device={dev_id}, data={data}")

        def _key_to_data(rows: list[dict[str, str]]) -> dict[tuple[str, str, str], set[str]]:
            mapping: dict[tuple[str, str, str], set[str]] = {}
            for r in rows:
                key = (r["role"], r["group"], r["device"])
                mapping.setdefault(key, set()).add(r["data"])
            return mapping

        device_map = _key_to_data(device_comparable)
        iox_map = _key_to_data(iox_comparable)

        only_in_device = sorted(set(device_map) - set(iox_map))
        only_in_iox = sorted(set(iox_map) - set(device_map))
        data_mismatches = sorted(key for key in set(device_map) & set(iox_map) if device_map[key] != iox_map[key])

        if not only_in_device and not only_in_iox and not data_mismatches:
            report.append("MATCH: device and iox link tables agree (deleted/end_of_table records excluded).")
        else:
            report.append("MISMATCH: device and iox link tables disagree -- device was likely reprogrammed outside NuCore.")
            if only_in_device:
                report.append(f"  Present on the device but NOT in NuCore's records ({len(only_in_device)}):")
                for role, group, dev_id in only_in_device:
                    report.append(f"    role={role}, group={group}, device={dev_id}")
            if only_in_iox:
                report.append(f"  Expected by NuCore but NOT present on the device ({len(only_in_iox)}):")
                for role, group, dev_id in only_in_iox:
                    report.append(f"    role={role}, group={group}, device={dev_id}")
            if data_mismatches:
                report.append(f"  Present on both sides but with different data ({len(data_mismatches)}):")
                for role, group, dev_id in data_mismatches:
                    key = (role, group, dev_id)
                    report.append(f"    role={role}, group={group}, device={dev_id}: device={device_map[key]} vs iox={iox_map[key]}")

        return "\n".join(report)

    async def _compare_device_links(self, device_id: str = None, **kwargs) -> str:
        """Fetch a device's live link table and NuCore's own replica of it,
        then compare them (see _compare_links_files) -- one step instead of
        the model manually calling get_dev_links_table/get_iox_links_table
        itself and eyeballing the difference, which is exactly the kind of
        mechanical row-matching a fast model is prone to hallucinate over.

        The two fetches happen sequentially (never in parallel), same as
        every other Insteon step, since they drive the same real hardware.
        """
        if device_id is None:
            logger.warning(no_device_id_message)
            return no_device_id_message

        device_result = await self._get_dev_links_table(device_id)
        if not device_result or LINKS_TABLE_FENCE_OPEN not in device_result:
            return device_result or "Failed to retrieve the device's live link table."

        iox_result = await self._get_iox_links_table(device_id)
        if not iox_result or LINKS_TABLE_FENCE_OPEN not in iox_result:
            return iox_result or "Failed to retrieve NuCore's replica of the link table."

        device_path = self._get_file_path("device", device_id)
        iox_path = self._get_file_path("iox", device_id)
        return self._compare_links_files(device_path, iox_path)

    async def _quick_plm_sanity_check(self, **kwargs) -> dict[str, Any]:
        """Fast, system-wide first pass for "none of my devices report status
        back to the PLM" -- compares the PLM's actual link record count
        against a rough expected count derived from NuCore's own node/group
        database (nodes + groups + group memberships), instead of checking
        every device's own links one at a time.

        Not a replacement for get_all_plm_links/compare_device_links once you
        suspect a specific device -- this just tells "PLM's link database
        looks broadly healthy" from "badly out of sync" before committing to
        a deeper per-device dive.

        Records with role in _ROLES_EXCLUDED_FROM_COMPARISON (deleted/
        end_of_table) aren't real, current links, so they're excluded from
        the actual count the same way they're excluded from
        _compare_links_files -- one definition of "a real link record" for
        both.

        :return: ``{"passed": bool | None, "plm_connected": bool, "report": str}``
            -- ``passed`` is ``None`` when the check couldn't run at all (PLM
            link fetch failed, or 0 nodes/groups to compare against); the
            plain-language ``report`` is unchanged from what this used to
            return bare, callers that just want to relay it still can.
        """
        plm_result = await self._get_all_plm_links()
        # _get_all_plm_links already ran _get_plm_info as part of fetching --
        # report the connectivity it found instead of re-querying for it.
        plm_connected = self._plm_connected
        lines = [f"PLM connected: {plm_connected}"]
        if not plm_result or LINKS_TABLE_FENCE_OPEN not in plm_result:
            lines.append(plm_result or "Failed to retrieve the PLM's link table.")
            return {"passed": None, "plm_connected": plm_connected, "report": "\n".join(lines)}

        rows = _parse_links_csv(plm_result)
        actual = sum(1 for r in rows if r["role"] not in _ROLES_EXCLUDED_FROM_COMPARISON)

        nodes = self._iox_wrapper.nodes
        groups = self._iox_wrapper.groups
        num_members = sum(len(g.members) for g in groups.values())
        expected = len(nodes) + len(groups) + num_members

        if expected == 0:
            lines.append(
                f"Cannot run the record-count check -- NuCore reports 0 nodes/groups, nothing to "
                f"compare the PLM's {actual} link record(s) against."
            )
            return {"passed": None, "plm_connected": plm_connected, "report": "\n".join(lines)}

        diff_pct = abs(actual - expected) / expected * 100
        within_tolerance = diff_pct <= _PLM_SANITY_CHECK_TOLERANCE_PCT

        lines += [
            f"PLM link records (excluding deleted/end_of_table): {actual}",
            f"Expected from NuCore's database (nodes={len(nodes)} + groups={len(groups)} "
            f"+ group memberships={num_members}): {expected}",
            f"Difference: {diff_pct:.1f}% "
            f"({'within' if within_tolerance else 'OUTSIDE'} the {_PLM_SANITY_CHECK_TOLERANCE_PCT:.0f}% tolerance)",
        ]
        if within_tolerance:
            lines.append("SANE: the PLM's link count is in line with what NuCore expects.")
        elif actual < expected:
            lines.append(
                "PROBLEM: the PLM has far fewer link records than expected -- consistent with devices "
                "not reporting status back to the PLM (missing device->PLM responder links). The PLM's "
                "link database is likely stale or was never fully restored."
            )
        else:
            lines.append(
                "NOTE: the PLM has more link records than expected -- possible stale/duplicate links "
                "rather than a missing-status-feedback issue; worth checking specific devices."
            )
        return {"passed": within_tolerance, "plm_connected": plm_connected, "report": "\n".join(lines)}

    async def _stream_links_into_file(
        self,
        action: str,
        file_path: str,
        type_: Literal["iox", "device", "plm"],
        trigger: Callable[[], Awaitable[Any]],
        max_gap_timeout: float = _LINKS_STREAM_MAX_GAP_TIMEOUT_S,
    ) -> bool:
        """Register a scoped listener for ("_2", *action*), fire *trigger*
        (the POST that starts the hub's link-table stream) as a background
        task, and ignore its result entirely -- on this backend that POST
        blocks until the *entire* scan completes (as long as the scan
        itself takes, potentially minutes for a large PLM), and its own
        outcome is immaterial: the event stream is the only thing that
        matters, whether it ever returns or not.

        Drains streamed records into *file_path* (via the existing
        format_links_event/_write_to_file) until one of three stop
        conditions is hit:
          1. a record's role is "end_of_table" (real completion signal --
             see _decode_role's "00" mapping);
          2. the hub reports it's no longer busy (control "_5", action
             "0" -- DEVINTIX_SYSTEM_IS_NOT_BUSY_ACTION, see
             IoXWrapper._on_device_event) -- some scans (e.g. an empty
             table) never emit an end_of_table row at all, so this is
             also treated as a real completion signal, not a timeout;
          3. *max_gap_timeout* elapses since the last record (or since
             draining started, for the first) with nothing new arriving --
             a rolling/inactivity timeout, not an overall ceiling, so a
             slow-but-steady scan is never cut off just because it runs
             long.
        This is the sole timing mechanism; trigger is never awaited,
        raced, or otherwise consulted.

        Returns whether the drain ended via (1) or (2) above, as opposed to
        giving up on the gap timeout. Always unregisters the listener
        before returning, on every exit path.
        """
        waiter = _LinksTableWaiter(self._iox_wrapper, "_2", action)
        self._iox_wrapper.register_listener(waiter._listener_id, "_5", "0", waiter)
        trigger_task = asyncio.ensure_future(trigger())
        # Fire-and-forget: consume whatever this eventually resolves to
        # (result or exception) so it never produces an "exception was
        # never retrieved" warning -- nothing else ever looks at it.
        trigger_task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
        try:
            completed = False
            while True:
                try:
                    _node, control, _action, eventInfo = await asyncio.to_thread(
                        waiter._queue.get, timeout=max_gap_timeout
                    )
                except queue.Empty:
                    break
                if control == "_5":
                    # "System no longer busy" -- the hub itself says the
                    # scan is done. Not a links-table row, so nothing to
                    # write to the file.
                    completed = True
                    break
                formatted_event = await self.format_links_event(eventInfo, type_)
                await self._write_to_file(file_path, formatted_event + "\n", mode="a")
                # role is the 2nd CSV field -- reuse it instead of
                # re-deriving _decode_role here. Guard against
                # format_links_event's non-CSV "No event information
                # provided." fallback string.
                role = formatted_event.split(",", 2)[1] if formatted_event.count(",") >= 2 else ""
                if role == "end_of_table":
                    completed = True
                    break
            return completed
        finally:
            self._iox_wrapper.unregister_listener(waiter._listener_id, "_2", action)
            self._iox_wrapper.unregister_listener(waiter._listener_id, "_5", "0")

    async def _stream_scene_test_into_file(
        self,
        file_path: str,
        trigger: Callable[[], Awaitable[Any]],
        max_gap_timeout: float = _SCENE_TEST_MAX_GAP_TIMEOUT_S,
    ) -> int:
        """Register a scoped listener for ("_7", "1"), fire *trigger* (the
        raw group-off POST) as a background task, and ignore its result
        entirely -- same fire-and-forget rationale as
        _stream_links_into_file. Drains progress events into *file_path*
        until *max_gap_timeout* elapses with nothing new arriving -- the
        only stop condition; unlike link-table streaming, a raw group-off
        has no end_of_table/"system no longer busy" completion signal to
        watch for instead.

        Always unregisters the listener before returning, on every exit
        path. Returns the number of events written to the file.
        """
        waiter = _SceneTestListener(self._iox_wrapper, "_7", "1")
        trigger_task = asyncio.ensure_future(trigger())
        # Fire-and-forget: consume whatever this eventually resolves to
        # (result or exception) so it never produces an "exception was
        # never retrieved" warning -- nothing else ever looks at it.
        trigger_task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
        try:
            event_count = 0
            while True:
                try:
                    node, _control, _action, eventInfo = await asyncio.to_thread(
                        waiter._queue.get, timeout=max_gap_timeout
                    )
                except queue.Empty:
                    break
                formatted_event = self.format_scene_test_event(node, eventInfo)
                await self._write_to_file(file_path, formatted_event + "\n", mode="a")
                event_count += 1
            return event_count
        finally:
            self._iox_wrapper.unregister_listener(waiter._listener_id, "_7", "1")

    def format_scene_test_event(self, node, eventInfo) -> str:
        """Format one _7/"1" (UD_PROGRESS_EVENT_UPDATE) event for the
        scene_test file -- one JSON object per line, ``{"node": ..., "eventInfo": ...}``.

        No further decoding of eventInfo itself here (it's a free-form
        Insteon traffic-trace string, e.g. containing "INST-SRX"/"GRP-RX"/
        "CLEAN-UP-RPT"/"Std-Cleanup Ack") -- keeping the raw line means
        nothing is lost even if _process_scene_test_file's keyword filter
        below needs to change. node is kept too even though it isn't
        currently used downstream, in case it turns out to matter later.
        """
        return json.dumps({"node": node, "eventInfo": eventInfo})

    # Only lines whose eventInfo contains one of these are kept in
    # _process_scene_test_file's "details" -- everything else is progress
    # noise from control "_7" that isn't an actual scene response.
    _SCENE_TEST_KEYWORDS = ("Std-Cleanup Ack", "INST-SRX", "GRP-RX", "CLEAN-UP-RPT")

    _SCENE_TEST_NO_RESPONSE_NOTE = (
        "One or more members above have status=\"failure\" (no response seen "
        "during the scene test). For each failed member, look at its name for "
        "anything that sounds like a sensor (e.g. \"sensor\", \"motion\", "
        "\"leak\", \"trigger\", \"occupancy\", or similar) -- if so, say that "
        "member might be a battery-powered/wireless sensor, which wouldn't be "
        "expected to respond to commands at all, and do not suggest "
        "diagnostics_not_responding for it, since that tool checks control and "
        "a sensor that can't be commanded can't be diagnosed that way. For any "
        "other failed member, it can be investigated further with "
        "diagnostics_not_responding(protocol=\"insteon\", device_id=..., "
        "force=true) -- force=true is required here, since this failed scene "
        "test is exactly the case that justifies it (a plain Query success "
        "alone would otherwise be reported as \"responding\", contradicting "
        "what the scene test just showed). Ask the customer for permission "
        "separately before running it on each such member, one at a time -- "
        "never back-to-back without asking first -- since each check can take "
        "a long time."
    )

    async def _process_scene_test_file(self, file_path: str, event_count: int, group: Group) -> dict[str, Any]:
        """Turn the raw events collected in *file_path* into the result
        scene_test hands back to the caller.

        Two views over the same collected events:
        - "details": every line whose eventInfo contains one of
          _SCENE_TEST_KEYWORDS, verbatim -- the raw evidence.
        - "summary": per-scene-member pass/fail, derived from just the
          "[Std-Cleanup Ack]" lines among those -- see
          _extract_std_cleanup_ack_devices/_dotted_insteon_address_to_nucore
          (which normalize each responding device down to a bare, unsuffixed
          address) and _strip_instance_suffix (which drops each member's own
          trailing instance digit so both sides compare equal). A member is
          a "success" if its (unsuffixed) address is among the devices that
          sent a Std-Cleanup Ack, "failure" otherwise.

          The real scene members are group.members[group.address].links, NOT
          group.members itself: a scene where the group's own address is the
          controller (the standard shape -- the group directly links to N
          responders) enriches that one pre-seeded container GroupMember's
          .links in place rather than adding new group.members entries (see
          Group.add_links), so group.members stays at just the container
          even though it has real responders. group.members can hold
          additional entries for devices that are themselves separately
          cross-linked to each other, but that's a different relationship
          than "what does this scene/group broadcast reach."

        _SCENE_TEST_NO_RESPONSE_NOTE is identical caveat/next-steps guidance
        regardless of which or how many members failed, so it's returned
        once as a top-level "note" (present only if at least one member
        failed) instead of repeated on every failing entry -- attaching the
        same paragraph per failure just bloats the result for no benefit
        once more than one member fails.

        event_count (the raw, unfiltered count of events received) is
        passed through unchanged -- it's a fact about the collection itself,
        not something this filtering should affect.
        """
        raw = await self._read_from_file(file_path)
        events: list[str] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue  # the file's own header line, or any other non-JSON row
            event_info = event.get("eventInfo") if isinstance(event, dict) else None
            if isinstance(event_info, str):
                events.append(event_info)

        details = [e for e in events if any(keyword in e for keyword in self._SCENE_TEST_KEYWORDS)]

        responding = set()
        for addr in _extract_std_cleanup_ack_devices(events):
            try:
                responding.add(_dotted_insteon_address_to_nucore(addr))
            except ValueError:
                continue  # not a well-formed 6-hex-digit dotted address -- skip it

        summary = []
        any_failures = False
        container = group.members.get(group.address)
        links = container.links.values() if container is not None else []
        for link in links:
            node = link.node
            status = "success" if _strip_instance_suffix(node.address) in responding else "failure"
            any_failures = any_failures or status == "failure"
            summary.append({"name": node.name, "address": node.address, "status": status})

        result = {"summary": summary, "details": details, "event_count": event_count}
        if any_failures:
            result["note"] = self._SCENE_TEST_NO_RESPONSE_NOTE
        return result

    def _get_scene_test_file_path(self, device_id: str) -> str:
        return f"/tmp/scene_test_{device_id.replace(' ', '_')}.txt"

    async def scene_test(self, device_id: str, physical_group_num: int, group: Group) -> dict[str, Any]:
        """Send a raw PLM group-off (see IoXDiagnostics.scene_test for the
        family/group validation done before this is called) and collect
        whatever _7/"1" progress events it triggers into a file, stopping
        once the stream goes quiet for _SCENE_TEST_MAX_GAP_TIMEOUT_S.
        """
        file_path = self._get_scene_test_file_path(device_id)
        await self._write_to_file(file_path, f"Scene Test for {device_id} (physical group {physical_group_num})\n", mode="w")

        async def _trigger() -> None:
            body = {"physicalGroupNum": physical_group_num}
            await self._iox_wrapper.post(
                self._iox_wrapper._family_api_path("scene-test/raw-off"),
                json.dumps(body),
                {"Content-Type": "application/json"},
            )

        event_count = await self._stream_scene_test_into_file(file_path, _trigger)
        result = await self._process_scene_test_file(file_path, event_count, group)
        return {"successful": True, "file_path": file_path, **result}

    async def stop_insteon_diagnostics(self, cleanup:bool=True) -> str | None:
        if self._is_running:
            logger.warning("Stopping Insteon diagnostics...")
            await self._iox_wrapper.post(self._iox_wrapper._family_api_path("links/stop"), "")
            if cleanup:
                await self._add_ending_to_file()
                self._is_running = False
                self._file_path = None

    async def _add_ending_to_file(self):
        if self._file_path:
            await self._write_to_file(self._file_path, LINKS_TABLE_FENCE_CLOSE, mode="a")

    async def format_links_event(self, eventInfo: dict, type: Literal["iox", "device", "plm"]) -> str:
        """
        Format the links event information into one CSV row matching
        LINKS_TABLE_HEADER's shape: idx,role,group,device,data

        The raw flag byte is decoded into ``role`` here (see _decode_role),
        and ``data``'s bytes are labeled per-role (see _format_data_fields) --
        so the model never has to cross-reference a legend for either. For
        the end_of_table sentinel, group/device/data are blanked since
        they're not meaningful data.

        :param eventInfo: Dictionary containing the event information.
        :return: One CSV row (no trailing newline) representing the record.
        """
        if not eventInfo:
            return "No event information provided."

        index = eventInfo.get('ix', 'Unknown')

        raw_flag = eventInfo.get('fl', None)
        try:
            flag_hex = f"{int(raw_flag):02X}"
        except Exception as e:
            logger.error(f"Error formatting flag: {e}")
            flag_hex = "Unknown"
        role = _decode_role(flag_hex)

        data = eventInfo.get('data', 'Unknown')
        try:
            data_fields = _format_data_fields(role, int(data))
        except Exception as e:
            logger.error(f"Error formatting data: {e}")
            data_fields = "Unknown"

        group = eventInfo.get('gr', 'Unknown')
        button_group = group
        device_id = eventInfo.get('id', 'Unknown')
        is_plm = False
        try:
            device_id = f"{int(device_id):06X}"  # hex6, no separators
            # convert it to xx yy zz string without preceding 0s, e.g. 7.D5.27
            if role == "controller":
                button_group = (int(data) >> 16) & 0xF
            device_address = f"{int(device_id[0:2],16):X} {int(device_id[2:4],16):X} {int(device_id[4:6],16):X} {button_group}"
            device_name = self._iox_wrapper.get_device_name(device_address)
            if self._plm_connected and self._plm_address:
                plm_id=f"{int(self._plm_address, 16):06X}"  # hex6, no separators
                if device_id == plm_id:
                    device_id = f"{device_id} (PLM)"
                    is_plm = True
                elif device_name:
                    device_id = f"{device_address} ({device_name})"
        except Exception as e:
            logger.error(f"Error formatting device_id: {e}")
            device_id = "Unknown"

        group_name = None
        if group:
            try:
                if role == "controller":
                    group_node = self._iox_wrapper._get_group_by_device_group_id(group)
                    if group_node:
                        group_name = group_node.name
                elif role == "responder":
                    if is_plm and type == "plm":
                        group_name = f"button/node #{group} (PLM)"
                    else:
                        group_node = self._iox_wrapper._get_group_by_device_group_id(group)
                        if group_node:
                            group_name = group_node.name
            except Exception as e:
                logger.error(f"Error getting group name: {e}")


        if group_name:
            group = f"{group} ({group_name})"
        return f"{index},{role},{group},{device_id},{data_fields}"

    def _get_plm_address(self, plm_info: str) -> str:
        # Extract the PLM address from the PLM info string
        if plm_info and self._plm_connected:
            parts = plm_info.split(" ")
            if len(parts) > 1:
                return parts[0].strip().replace(".", "")  # Replace spaces with underscores for file naming
        return "Unknown"

    # retrieve PLM info
    # returns:
    # Connected / Disconnected (Boolean), PLM Address and Version information
    async def _get_plm_info(self) -> tuple[bool | None, str]:
        if self._is_running:
            logger.warning(already_running_message)
            return None, already_running_message

        response = await self._iox_wrapper.post(self._iox_wrapper._family_api_path("plm-info"), "")
        if response is None or response.status_code != 200:
            status = response.status_code if response else "No response"
            logger.error(f"Failed to get PLM info: {status}")
            return None, status

        try:
            plm_info = response.json().get("data")
        except ValueError:
            plm_info = response.text

        plm_info_parts = plm_info.split(" / ") if plm_info else []
        if len(plm_info_parts) > 1:
            return plm_info_parts[1].strip() == "Connected", plm_info_parts[0]

        return False, plm_info  # Default to disconnected if format is unexpected

    async def _write_to_file(self, file_path: str, content: str, mode: Literal["w", "a"] = "a") -> None:
        try:
            with open(file_path, mode) as f:
                f.write(content)
        except Exception as e:
            logger.error(f"Failed to write links table to {file_path}: {e}") 

    async def _read_from_file(self, file_path: str) -> str:
        try:
            with open(file_path, "r") as f:
                return f.read()
        except Exception as e:
            logger.error(f"Failed to read links table from {file_path}: {e}")
            return ""

    def _get_file_path(self, type: Literal["iox", "device", "plm"], device_id: str) -> None:
        if type not in ["iox", "device", "plm"]:
            logger.warning(f"Invalid type '{type}' for file name. Must be 'iox', 'device', or 'plm'.")
            return None
        if device_id is None and type != "plm":
            logger.warning("Device ID is required for getting device or iox links table.")
            return None
        device_id= device_id.replace(" ", "_") if device_id else "all" 
        return f"/tmp/{type}_links_table_{device_id}.txt"

    async def on_node_device_event(self, node, control, action, eventInfo):
        if not node or not action:
            return
        if action in ["NR", "ND", "RV", "NI", "DI", "AA", "MV", "CL", "RG", "WD","GR", "GD"]:
            self._refresh_plm_links = True

    # ---------------------------------------------------
    # Complaint-shaped diagnostics -- diagnose_not_responding/
    # diagnose_no_status_feedback (see NuCoreInterface for the model-facing
    # contract; IoXDiagnostics just routes by protocol and delegates to
    # these two).
    # ---------------------------------------------------

    # get_dev_links_table/compare_device_links/get_all_plm_links/
    # quick_plm_sanity_check all drive the single PLM serial connection
    # directly and cannot run concurrently with each other or with a second
    # call to themselves -- see _begin_plm_op/_end_plm_op below.
    # get_iox_links_table touches no PLM hardware directly and runs freely,
    # any time, including while one of the four is in flight.

    def _begin_plm_op(self, step: str) -> dict[str, Any] | None:
        """Call at the top of each of the four PLM-exclusive methods
        (get_dev_links_table/compare_device_links/get_all_plm_links/
        quick_plm_sanity_check). Returns an error dict if another one of the
        four is already in progress; otherwise marks *step* as the current op
        and returns None. No locking/waiting -- a second caller (including a
        second call to the same step) is refused immediately, never blocked
        or queued."""
        state = self._plm_op_state
        if state is not None:
            return {
                "error": (
                    f"a PLM operation ('{state['step']}') is already in progress -- try again shortly"
                )
            }
        self._plm_op_state = {"step": step}
        return None

    def _end_plm_op(self) -> None:
        self._plm_op_state = None

    # Lifted once, as code, from diagnose.md's old "Known fixes, in order of
    # likelihood" section -- not parsed from a prompt file at runtime.
    _KNOWN_FIXES = {
        "insteon_disabled": "Enable INSTEON in system configuration.",
        "plm_not_connected": (
            "Confirm the PLM is on a USB serial port and the udx service is running. If udx is "
            "running and it's still not connected, the PLM hardware has likely failed -- the "
            "customer needs a new one, and must restore it after."
        ),
        "plm_links_missing": (
            "Ask whether this is a new, never-restored PLM before concluding it 'lost' its links "
            "-- either way the fix is to restore the PLM, but the framing for the customer differs."
        ),
        "query_failed": (
            "Most likely signal/noise related -- have the customer move the PLM to an outlet not "
            "shared with other transformers/power supplies before assuming hardware failure; this "
            "resolves the majority of intermittent cases. Only if that doesn't help, recommend a "
            "new PLM + restore."
        ),
        "missing_device_to_plm_link": (
            "This device is missing its device->PLM controller link, so it can't report unsolicited "
            "status changes. Ask whether this is a new PLM (needs a full restore) or an existing one "
            "(this device may never have been linked with NuCore in the first place)."
        ),
        "device_missing_iox_link_table": (
            "This device has no link records in NuCore's own database -- remove it from the system "
            "and link it back again."
        ),
        "insteon_link_config_incorrect": (
            "PLM and device link configuration is incorrect -- most probably a new PLM or a new "
            "device that needs to be linked properly. If a new PLM, restore your backup and then do "
            "Restore PLM. If not a new PLM, try restoring the device or remove the device and try "
            "linking it back. If it fails again, the device might be faulty."
        ),
    }

    async def get_dev_links_table(self, device_id: str = None, **kwargs) -> Any:
        """Raw device link table (prose/CSV), unchanged. Only used internally
        now (by diagnose_no_status_feedback's per-device group check, and by
        compare_device_links) and by whatever future tool needs the raw
        table -- no longer reachable from the model directly."""
        busy = self._begin_plm_op("get_dev_links_table")
        if busy is not None:
            return busy
        try:
            return await self._get_dev_links_table(device_id, **kwargs)
        finally:
            self._end_plm_op()

    async def get_device_to_plm_link_status(self, device_id: str) -> dict[str, Any]:
        """Structured counterpart of get_dev_links_table -- whether *device_id*
        has the device->PLM controller link needed to report unsolicited
        status changes (see the module docstring's general Insteon link
        notes above: a "controller" role row in a device's own link table is
        that device's link to the PLM). No longer called by
        diagnose_no_status_feedback -- a device can have more than one
        status-feedback group (multi-button devices), and this only ever
        answered "any at all"; superseded there by the per-group check (see
        _device_controller_groups/_plm_responder_groups_for). Kept as an
        independently-useful, independently-tested public building block.

        :return: {"has_device_to_plm_link": bool | None, "report": str} --
            has_device_to_plm_link is None when the table couldn't be
            retrieved at all (PLM not connected, device not found, etc.).
        """
        raw = await self.get_dev_links_table(device_id)
        if not raw:
            return {"has_device_to_plm_link": None, "report": "Failed to retrieve the device's link table."}
        return {"has_device_to_plm_link": _has_controller_role_row(raw), "report": raw}

    async def _iox_table_has_controller_link(self, device_id: str) -> bool | None:
        """Same check as get_device_to_plm_link_status, but sourced from
        NuCore's own stored (iox) replica instead of the device's live
        table -- needed for diagnose_not_responding, where the device is, by
        definition, not answering live queries at all (get_dev_links_table
        would just fail the same way Query already did)."""
        raw = await self.get_iox_links_table(device_id)
        return _has_controller_role_row(raw)

    async def _iox_table_has_responder_link(self, device_id: str) -> bool | None:
        """Whether NuCore's own iox-stored replica of *device_id*'s link
        table has a "responder" role row -- i.e. NuCore's records show the
        PLM->device control link (PLM as controller, device as responder),
        the OPPOSITE direction from _iox_table_has_controller_link
        (device->PLM, status-feedback). A device can be missing either link
        independently of the other, so this is a genuinely separate check
        -- used by diagnose_no_status_feedback's step 3.b as a completeness
        check alongside step 3.a's status-feedback-direction check."""
        raw = await self.get_iox_links_table(device_id)
        return _has_role_row(raw, "responder")

    async def get_iox_links_table(self, device_id: str = None, **kwargs) -> str | None:
        return await self._get_iox_links_table(device_id, **kwargs)

    async def get_all_plm_links(self, **kwargs) -> Any:
        busy = self._begin_plm_op("get_all_plm_links")
        if busy is not None:
            return busy
        try:
            return await self._get_all_plm_links(**kwargs)
        finally:
            self._end_plm_op()

    async def compare_device_links(self, device_id: str = None, **kwargs) -> Any:
        busy = self._begin_plm_op("compare_device_links")
        if busy is not None:
            return busy
        try:
            return await self._compare_device_links(device_id, **kwargs)
        finally:
            self._end_plm_op()

    async def quick_plm_sanity_check(self, **kwargs) -> Any:
        """System-level checks (INSTEON enabled, core services) plus this
        class's own PLM-connected/link-count check (_quick_plm_sanity_check),
        merged into one report -- so callers never need to separately call
        IoXDiagnostics.get_full_system_config/get_core_services_status for
        this scenario.

        :return: ``{"passed": bool | None, "insteon_enabled": bool,
            "plm_connected": bool | None, "report": str}`` on success, or
            the busy-refusal ``{"error": ...}`` from _begin_plm_op.
        """
        busy = self._begin_plm_op("quick_plm_sanity_check")
        if busy is not None:
            return busy
        try:
            options_config = await self._iox_diagnostics._get_system_options()
            insteon_enabled = bool(options_config.get("insteonSupport", False))

            try:
                services_status: Any = await self._iox_diagnostics.get_core_services_status()
            except NotImplementedError as ex:
                services_status = f"not available yet ({ex})"

            lines = [
                f"INSTEON enabled: {insteon_enabled}",
                f"Core services status: {services_status}",
            ]
            if not insteon_enabled:
                lines.append(
                    "INSTEON is not enabled in system config -- that alone explains no status "
                    "feedback from any Insteon device; nothing else to check until it's enabled."
                )
                return {
                    "passed": False,
                    "insteon_enabled": False,
                    "plm_connected": None,
                    "report": "\n".join(lines),
                }

            insteon_result = await self._quick_plm_sanity_check(**kwargs)
            return {
                "passed": insteon_result["passed"],
                "insteon_enabled": True,
                "plm_connected": insteon_result["plm_connected"],
                "report": "\n".join(lines) + "\n" + insteon_result["report"],
            }
        finally:
            self._end_plm_op()

    async def _plm_sanity_gate(self) -> tuple[list[str], dict[str, Any], dict[str, Any] | None]:
        """Run quick_plm_sanity_check() and build the early-return diagnosis
        dict when it doesn't pass -- shared by diagnose_not_responding and
        diagnose_no_status_feedback, which both start with this exact check
        and must react to failure identically (distinguishing "PLM enabled
        but not connected" from a generic failed sanity check matters for
        which _KNOWN_FIXES entry -- and clarifying question -- the customer
        gets).

        :return: ``(steps_run, sanity, early_result)``. Callers must return
            ``early_result`` immediately when it isn't ``None``; ``None``
            means the sanity check passed and the caller should continue its
            own per-device logic, using the returned ``steps_run``/``sanity``.
        """
        steps_run = ["quick_plm_sanity_check"]
        sanity = await self.quick_plm_sanity_check()
        if "error" in (sanity or {}):
            return steps_run, sanity, {"steps_run": steps_run, "error": sanity["error"]}

        if not sanity["insteon_enabled"]:
            return steps_run, sanity, {
                "steps_run": steps_run,
                "plm_sanity_check": sanity,
                "diagnosis": "INSTEON is not enabled in system configuration.",
                "recommended_fix": self._KNOWN_FIXES["insteon_disabled"],
            }
        if not sanity["passed"]:
            if not sanity["plm_connected"]:
                return steps_run, sanity, {
                    "steps_run": steps_run,
                    "plm_sanity_check": sanity,
                    "diagnosis": "PLM is enabled but not connected.",
                    "recommended_fix": self._KNOWN_FIXES["plm_not_connected"],
                }
            return steps_run, sanity, {
                "steps_run": steps_run,
                "plm_sanity_check": sanity,
                "diagnosis": "PLM sanity check did not pass -- new/never-restored PLM, or one that lost its links.",
                "recommended_fix": self._KNOWN_FIXES["plm_links_missing"],
                "clarifying_question": "Is this a new PLM, or one that's been in service before?",
            }

        return steps_run, sanity, None

    async def diagnose_not_responding(self, device_id: str | None, force: bool = False) -> dict[str, Any]:
        """The real orchestrator: always runs the full PLM sanity check
        first (regardless of whether device_id was given), the same
        sequential, separately-guarded-step pattern diagnose_no_status_feedback
        already uses -- never a new outer PLM-op guard here, since
        quick_plm_sanity_check/get_iox_links_table/get_all_plm_links (called
        from the per-device check below) are already each self-guarded, and
        nesting a guard inside another would self-deadlock (see
        compare_device_links for why that method calls the private, unguarded
        primitives directly instead -- it IS the outer guard for its call).

        :param force: Forwarded to _diagnose_device_not_responding -- see
            its docstring. Only True when the customer explicitly insists on
            a re-check despite an earlier "responding" result, or right
            after a scene_test already showed this device failing a group
            command.
        """
        if device_id:
            node = self._iox_wrapper._get_node(device_id)
            if node is not None:
                if isinstance(node, Group) or isinstance(node, Folder):
                    return {"steps_run": ["node_check"], "diagnosis": f"Cannot run diagnostics because {node.name} is a Group or Folder, not an individual device."}

        steps_run, sanity, early_result = await self._plm_sanity_gate()
        if early_result is not None:
            return early_result

        device_ids = [device_id] if device_id else self._sample_device_ids(2)
        if not device_ids:
            return {"steps_run": steps_run, "plm_sanity_check": sanity, "status": "no insteon device found"}

        device_checks = []
        for one_device_id in device_ids:
            device_checks.append(await self._diagnose_device_not_responding(one_device_id, force=force))
            steps_run.append(f"diagnose_insteon_device_not_responding({one_device_id})")

        return {"steps_run": steps_run, "plm_sanity_check": sanity, "device_checks": device_checks}

    def _sample_device_ids(self, n: int) -> list[str]:
        """Up to *n* representative INSTEON device ids -- used when a
        not-responding complaint is general (no device_id given) rather
        than asking the caller to pick one themselves."""
        candidates = [addr for addr in self._iox_wrapper.nodes if self._iox_wrapper._is_insteon_family(addr)]
        return random.sample(candidates, min(n, len(candidates)))

    async def _diagnose_device_not_responding(self, device_id: str, force: bool = False) -> dict[str, Any]:
        """Deep single-device diagnosis, called once per device_id by
        diagnose_not_responding above. Sends Query directly (same
        two primitives command_control_status.send_command itself uses) --
        on failure, root-causes it via link tables instead of guessing.
        Never reads the device's own live table (get_dev_links_table) for
        this -- a device that isn't responding to Query won't respond to a
        links-table request either; NuCore's own stored (iox) replica and
        the PLM's live table are used instead, since both are reachable
        without the device's cooperation.

        :param force: When True, a successful Query is NOT treated as
            conclusive -- it's only logged, and the link-table checks below
            still run. A bare on-demand Query can succeed even though the
            device fails to respond to a *group*/scene command (a different
            link entirely), so short-circuiting on it there would contradict
            a complaint the caller already has independent evidence for.
            Only set True when the customer explicitly insists this device
            isn't responding despite an earlier "responding" result, or
            right after a scene_test already showed this device failing a
            group command -- never as the default, since a plain Query
            success is otherwise a perfectly good, cheap answer.
        :return: {"passed": bool, "device_id": str, "report": str}
        """
        command = self._iox_wrapper.resolve_command_id(device_id, "Query", direction="accepts")
        if command is None:
            return {
                "passed": False,
                "device_id": device_id,
                "report": f"'{device_id}' has no 'Query' command -- not an INSTEON device?",
            }
        try:
            response = await self._iox_wrapper.send_commands([{"device": device_id, "command": command.id, "parameters": []}])
            if response and response[0].status_code == 200:
                if not force:
                    return {
                        "passed": True,
                        "device_id": device_id,
                        "report": f"Query succeeded on {device_id} -- the device is responding.",
                    }
                logger.info(
                    f"diagnose_not_responding: Query succeeded on {device_id}, but force=True -- "
                    "continuing to check its link tables instead of stopping here."
                )
        except Exception as ex:
            logger.error(f"diagnose_not_responding: Query failed on {device_id}: {ex}")

        if not await self._iox_table_has_controller_link(device_id):
            return {
                "passed": False,
                "device_id": device_id,
                "report": self._KNOWN_FIXES["device_missing_iox_link_table"],
            }

        plm_has_responder_for_device = await self._plm_table_has_responder_for(device_id)
        if plm_has_responder_for_device is None:
            return {"passed": False, "device_id": device_id, "report": self._KNOWN_FIXES["plm_links_missing"]}
        if not plm_has_responder_for_device:
            return {
                "passed": False,
                "device_id": device_id,
                "report": self._KNOWN_FIXES["insteon_link_config_incorrect"],
            }

        # Both sides of the PLM<->device link check out -- Query still
        # failed anyway, so this isn't a link-configuration problem.
        return {"passed": False, "device_id": device_id, "report": self._KNOWN_FIXES["query_failed"]}

    async def _plm_table_has_responder_for(self, device_id: str) -> bool | None:
        """Whether the PLM's own link table has a "responder" role row
        naming *device_id* -- i.e. the PLM recognizes this device as one it
        controls. The PLM's table covers every device, so (unlike
        _has_controller_role_row/_iox_table_has_controller_link, which only
        ever need to know whether ANY controller row exists) this one does
        need to match by address: get_all_plm_links's device column is a
        human-formatted display string, but it's produced by the same
        format_links_event used for every table type, so an ordinary
        device's address renders identically wherever it appears -- strip
        the optional trailing " (Name)" annotation and compare directly.

        :return: None when the PLM's table itself couldn't be retrieved at
            all (distinct from False -- reachable, but no row for this
            device).
        """
        raw = await self.get_all_plm_links()
        if not raw or LINKS_TABLE_FENCE_OPEN not in raw:
            return None
        rows = _parse_links_csv(raw)
        for row in rows:
            if row["role"] in _ROLES_EXCLUDED_FROM_COMPARISON:
                continue
            if row["role"] != "responder":
                continue
            if row["device"].split(" (")[0].strip() == device_id:
                return True
        return False

    async def _diagnose_device_no_status_feedback(self, device_id: str, plm_links_raw: str) -> dict[str, Any]:
        """Deep single-device diagnosis for a "no status feedback" complaint
        -- called once per device_id, either the one explicit device_id
        given to diagnose_no_status_feedback, or once per device sampled
        when none was given. Runs design/diagnose_not_responding.md's three
        sub-checks (step 3.a a/b/c) in sequence, stopping at the FIRST
        failure -- each has its own different customer-facing
        recommendation, and running all three unconditionally would blur
        which one actually fired (mirrors _diagnose_device_not_responding's
        same sequential-early-return shape):

          3.a  Does the PLM have a responder link for EVERY one of this
               device's own controller-role (status-feedback) groups?
               Sourced from the device's own LIVE table
               (get_dev_links_table), not NuCore's iox-stored replica --
               unlike diagnose_not_responding's per-device check, a "no
               status feedback" complaint doesn't mean the device is
               unreachable, so the live table is the more authoritative,
               ground-truth source here.

               *plm_links_raw* is get_all_plm_links's raw blob, already
               fetched once by diagnose_no_status_feedback's own step 2 and
               passed straight through here -- never re-fetched, since
               it's the same system-wide data for every device checked in
               one diagnose_no_status_feedback call.

          3.b  Does NuCore's own iox-stored replica show the opposite-
               direction (control: PLM as controller, device as responder)
               link too? (_iox_table_has_responder_link)

          3.c  Do the device's live table and NuCore's iox-stored replica
               agree in full? (compare_device_links -- re-fetches both
               tables a second time internally, a known, accepted
               redundancy given the design doc calls out compare_device_links
               as its own distinct step.)

        :return: {"passed": bool, "device_id": str, "report": str}
        """
        raw_dev = await self.get_dev_links_table(device_id)
        if isinstance(raw_dev, dict) and "error" in raw_dev:
            return {"passed": False, "device_id": device_id, "report": raw_dev["error"]}

        device_groups = _device_controller_groups(raw_dev) or set()
        if not device_groups:
            # No controller-role row at all -- same "vacuous" case the old,
            # single-check implementation covered; keep its exact fix text.
            return {
                "passed": False,
                "device_id": device_id,
                "report": self._KNOWN_FIXES["missing_device_to_plm_link"],
            }

        missing_groups = device_groups - (_plm_responder_groups_for(device_id, plm_links_raw) or set())
        if missing_groups:
            ordered = sorted(missing_groups, key=lambda g: (int(g) if g.isdigit() else float("inf"), g))
            return {
                "passed": False,
                "device_id": device_id,
                "report": (
                    f"PLM is missing a responder link for {device_id} group(s) {ordered}. "
                    f"{self._KNOWN_FIXES['missing_device_to_plm_link']}"
                ),
            }

        if not await self._iox_table_has_responder_link(device_id):
            return {
                "passed": False,
                "device_id": device_id,
                "report": self._KNOWN_FIXES["insteon_link_config_incorrect"],
            }

        comparison = await self.compare_device_links(device_id)
        if isinstance(comparison, dict) and "error" in comparison:
            return {"passed": False, "device_id": device_id, "report": comparison["error"]}
        if not _comparison_matches(comparison):
            return {
                "passed": False,
                "device_id": device_id,
                "report": self._KNOWN_FIXES["insteon_link_config_incorrect"],
            }

        return {
            "passed": True,
            "device_id": device_id,
            "report": f"{device_id} is correctly linked to report status (groups {sorted(device_groups)} all confirmed).",
        }

    async def diagnose_no_status_feedback(self, device_id: str | None = None) -> dict[str, Any]:
        """Customer operated a device physically/locally and NuCore didn't
        show the new status (the device -> NuCore direction). See
        NuCoreInterface.diagnose_no_status_feedback for the model-facing
        contract. Follows design/diagnose_not_responding.md's "Diagnosing
        no status feedback" section, step for step:
          0. reject a group/folder device_id -- diagnostics only run
             against an individual device.
          1. PLM sanity gate (_plm_sanity_gate).
          2. PLM has ANY real link records at all (get_all_plm_links) --
             its own explicit call, separate from the one buried three
             calls deep inside _plm_sanity_gate (quick_plm_sanity_check ->
             _quick_plm_sanity_check -> self._get_all_plm_links()); the raw
             blob fetched here is reused for every device checked below,
             never re-fetched.
          3. per device (the one given, or 2 sampled when none is given):
             does the PLM have a responder link for every one of the
             device's own status-feedback groups, does NuCore's iox-stored
             table show the opposite-direction link too, and does a full
             device-vs-iox comparison agree -- see
             _diagnose_device_no_status_feedback.

        :return: {"steps_run": [...], "diagnosis"?: str, "recommended_fix"?:
            str, "clarifying_question"?: str, "plm_sanity_check"?: {...},
            "device_checks"?: [{"device_id", "passed", "report"}, ...],
            "status"?: str, "error"?: str}
        """
        if device_id:
            node = self._iox_wrapper._get_node(device_id)
            if node is not None and (isinstance(node, Group) or isinstance(node, Folder)):
                return {
                    "steps_run": ["node_check"],
                    "diagnosis": f"Cannot run diagnostics because {node.name} is a Group or Folder, not an individual device.",
                }

        steps_run, sanity, early_result = await self._plm_sanity_gate()
        if early_result is not None:
            return early_result

        plm_links_raw = await self.get_all_plm_links()
        steps_run.append("get_all_plm_links")
        if isinstance(plm_links_raw, dict) and "error" in plm_links_raw:
            return {"steps_run": steps_run, "plm_sanity_check": sanity, "error": plm_links_raw["error"]}

        real_plm_rows = []
        if plm_links_raw and LINKS_TABLE_FENCE_OPEN in plm_links_raw:
            real_plm_rows = [r for r in _parse_links_csv(plm_links_raw) if r["role"] not in _ROLES_EXCLUDED_FROM_COMPARISON]
        if not real_plm_rows:
            return {
                "steps_run": steps_run,
                "plm_sanity_check": sanity,
                "diagnosis": "PLM has no real link records -- it may have lost its links, be defective, or never have been restored.",
                "recommended_fix": self._KNOWN_FIXES["plm_links_missing"],
                "clarifying_question": "Is this a new PLM, or one that's been in service before?",
            }

        device_ids = [device_id] if device_id else self._sample_device_ids(2)
        if not device_ids:
            return {"steps_run": steps_run, "plm_sanity_check": sanity, "status": "no insteon device found"}

        device_checks = []
        for one_device_id in device_ids:
            device_checks.append(await self._diagnose_device_no_status_feedback(one_device_id, plm_links_raw))
            steps_run.append(f"diagnose_insteon_device_no_status_feedback({one_device_id})")

        return {"steps_run": steps_run, "plm_sanity_check": sanity, "device_checks": device_checks}

    # get nodes config
    async def _get_nodes_config(self) -> str | None:
        return await self._iox_wrapper._send_device_specific_with_option(IoXSOAPAction.SOAP_TYPE_GET_NODES_CONFIG, None, None, 0x01, None)

    # get isy config
    async def _get_isy_config(self) -> str | None:
        return await self._iox_wrapper._submit_soap_request(IoXSOAPAction.SOAP_TYPE_GET_ISY_CONFIG, None)

    # get startup time
    async def _get_startup_time(self) -> str | None:
        return await self._iox_wrapper._send_device_specific_with_option(IoXSOAPAction.SOAP_TYPE_GET_STARTUP_TIME, None, None, 0x01, None)
