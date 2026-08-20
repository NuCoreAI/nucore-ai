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


from collections import Counter
import os
import re
import time
from typing import TYPE_CHECKING, Literal
from ..iox_definitions import IoXSOAPAction, DEVICE_FAMILIES


from ..iox_wrapper import IoXWrapper
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
# count (see _quick_plm_sanity_check).
_ROLES_EXCLUDED_FROM_COMPARISON = frozenset({"deleted", "end_of_table"})

# See _quick_plm_sanity_check.
_PLM_SANITY_CHECK_TOLERANCE_PCT = 15

# See _get_all_plm_links's cache check -- a full PLM link scan is a slow,
# real hardware operation, so a recent result is reused by default rather
# than re-scanning on every call.
_PLM_LINKS_CACHE_MAX_AGE_S = 3600  # 1 hour
# A real PLM links dump for any system with more than a handful of devices
# is comfortably larger than this -- guards against treating a truncated or
# otherwise corrupted partial write as a valid, usable cache.
_PLM_LINKS_CACHE_MIN_SIZE_BYTES = 5000


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


class INSTEONDiagnostics:
    """Class for Insteon diagnostics and link management."""

    def __init__(self, iox_wrapper: IoXWrapper) -> None:
        self._iox_wrapper = iox_wrapper
        self._is_running = False
        self._file_path = None
        self._plm_address = None
        self._plm_connected = False
        self._refresh_plm_links = False

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
        #make it into a thread so it can be stopped if needed
        rc = await self._iox_wrapper._send_device_specific_with_option(IoXSOAPAction.DEVICE_SPECIFIC_GET_DEV_LINKS_TABLE, device_id, None, 0x01, "0 -1")
        await self._add_ending_to_file()
        if rc:
            rc = await self._read_from_file(self._file_path)
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
        rc = await self._iox_wrapper._send_device_specific_with_option(IoXSOAPAction.DEVICE_SPECIFIC_GET_ISY_LINKS_TABLE, device_id, None, 0x01, None)
        await self._add_ending_to_file()
        if rc:
            rc = await self._read_from_file(self._file_path)
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

        rc = await self._iox_wrapper._send_device_specific_with_option(IoXSOAPAction.DEVICE_SPECIFIC_GET_ALL_PLM_LINKS, None, None, 0x01, None)
        await self._add_ending_to_file()
        self._refresh_plm_links = False  # satisfied -- next call can use cache again
        if rc:
            rc = await self._read_from_file(self._file_path)
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

    async def _quick_plm_sanity_check(self, **kwargs) -> str:
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
        """
        plm_result = await self._get_all_plm_links()
        # _get_all_plm_links already ran _get_plm_info as part of fetching --
        # report the connectivity it found instead of re-querying for it.
        lines = [f"PLM connected: {self._plm_connected}"]
        if not plm_result or LINKS_TABLE_FENCE_OPEN not in plm_result:
            lines.append(plm_result or "Failed to retrieve the PLM's link table.")
            return "\n".join(lines)

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
            return "\n".join(lines)

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
        return "\n".join(lines)

    async def stop_insteon_diagnostics(self, cleanup:bool=True) -> str | None:
        if self._is_running:
            logger.warning("Stopping Insteon diagnostics...")
            await self._iox_wrapper._send_device_specific_with_option(IoXSOAPAction.DEVICE_SPECIFIC_STOP_DEVICE_SPECIFIC, None, None, 0x01, None)
            if cleanup:
                await self._add_ending_to_file()
                self._is_running = False
                self._file_path = None

    async def _add_ending_to_file(self):
        if self._file_path:
            time.sleep(1)  # Ensure any pending writes are completed
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

        plm_info = await self._iox_wrapper._send_device_specific_with_option(IoXSOAPAction.DEVICE_SPECIFIC_GET_PLM_INFO, None, None, 0x01, None)
        if plm_info is None: 
            logger.error(f"Failed to get PLM info: {plm_info.status_code if plm_info else 'No response'}")
            return None, plm_info.status_code if plm_info else 'No response'
        
        plm_info_parts = plm_info.split(" / ")
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


    async def update_links_table(self, node, control, action, eventInfo):
        if not eventInfo:
            logger.warning("No eventInfo provided for update_links_table.")
            return

        file_path = None
        type = None
        if action == "1":
            file_path = self._get_file_path("plm", None)
            type = "plm"
        elif action == "2":
            file_path = self._get_file_path("device", node)
            type = "device"
        elif action == "3":
            file_path = self._get_file_path("iox", node)
            type = "iox"
        if file_path and type:
            formatted_event = await self.format_links_event(eventInfo, type)
            await self._write_to_file(file_path, formatted_event + "\n", mode="a")
            logger.info(f"update_links_table: node={node if node else 'Unknown'}, control={control if control else 'Unknown'}, action={action if action else 'Unknown'}, formatted_event={formatted_event if formatted_event else 'Unknown'}") 


        
    async def on_node_device_event(self, node, control, action, eventInfo):
        if not node or not action:
            return
        if action in ["NR", "ND", "RV", "NI", "DI", "AA", "MV", "CL", "RG", "WD","GR", "GD"]:
            self._refresh_plm_links = True
