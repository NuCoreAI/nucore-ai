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


from statistics import mode
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


class INSTEONDiagnostics:
    """Class for Insteon diagnostics and link management."""

    def __init__(self, iox_wrapper: IoXWrapper) -> None:
        self._iox_wrapper = iox_wrapper
        self._is_running = False
        self._file_path = None
        self._plm_address = None
        self._plm_connected = False

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
        rc = await self._iox_wrapper._send_device_specific_with_option(IoXSOAPAction.DEVICE_SPECIFIC_GET_DEV_LINKS_TABLE, device_id, None, 0x01, "0 100")
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

    async def _get_all_plm_links(self, **kwargs) -> str | None:
        # Get all PLM links -- system-wide, not scoped to any one device.
        if self._is_running:
            logger.warning(already_running_message)
            return already_running_message
        self._plm_connected, plm_info = await self._get_plm_info()
        self._plm_address = self._get_plm_address(plm_info)

        self._is_running = True
        self._file_path = self._get_file_path("plm", None)
        if self._plm_connected:
            await self._write_to_file(self._file_path, f"PLM Links Table for PLM address {self._plm_address}\n{LINKS_TABLE_NOTE}{LINKS_TABLE_FENCE_OPEN}{LINKS_TABLE_HEADER}", mode="w")
        else:
            self._is_running = False
            return "PLM not connected. Cannot retrieve PLM links table."

        rc = await self._iox_wrapper._send_device_specific_with_option(IoXSOAPAction.DEVICE_SPECIFIC_GET_ALL_PLM_LINKS, None, None, 0x01, None)
        await self._add_ending_to_file()
        if rc:
            rc = await self._read_from_file(self._file_path)
        self._is_running = False
        self._file_path = None
        return rc

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

        if role == "end_of_table":
            # stop device specific 
            await self.stop_insteon_diagnostics(False)
            return f"{index},{role},,,"

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