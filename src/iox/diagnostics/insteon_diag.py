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


from typing import TYPE_CHECKING
from ..iox_definitions import IoXSOAPAction, DEVICE_FAMILIES


from ..iox_wrapper import IoXWrapper
from utils import get_logger
logger = get_logger(__name__)

class INSTEONDiagnostics:
    """Class for Insteon diagnostics and link management."""

    def __init__(self, iox_wrapper: IoXWrapper) -> None:
        self._iox_wrapper = iox_wrapper

    async def _get_dev_links_table(self, device_id: str = None, **kwargs) -> str | None:
        # NOTE: assumes `node` here accepts the same device address used
        # elsewhere in this system (e.g. get_property's device_id) --
        # unconfirmed against real hub behavior; flag/verify before relying
        # on this for a real customer-facing diagnosis.
        return await self._iox_wrapper._send_device_specific_with_option(
            IoXSOAPAction.DEVICE_SPECIFIC_GET_DEV_LINKS_TABLE, device_id, None, 0x01, None
        )

    async def _get_isy_links_table(self, device_id: str = None, **kwargs) -> str | None:
        # NOTE: assumes `node` here accepts the same device address used
        # elsewhere in this system (e.g. get_property's device_id) --
        # unconfirmed against real hub behavior; flag/verify before relying
        # on this for a real customer-facing diagnosis.
        # Use this method to get the ISY links table for a specific device. 
        # The ISY links table what isy/iox thinks the device link should like like. 
        return await self._iox_wrapper._send_device_specific_with_option(IoXSOAPAction.DEVICE_SPECIFIC_GET_ISY_LINKS_TABLE, device_id, None, 0x01, None)

    async def _get_all_plm_links(self, device_id: str = None, **kwargs) -> str | None:
        # Use this method to get all PLM links. This is a device specific command that returns the PLM links table.
        # we ignore device_id
        return await self._iox_wrapper._send_device_specific_with_option(IoXSOAPAction.DEVICE_SPECIFIC_GET_ALL_PLM_LINKS, None, None, 0x01, None)

    async def update_links_table(self, node, control, action, eventInfo):
        logger.info(f"update_links_table: node={node if node else 'Unknown'}, control={control if control else 'Unknown'}, action={action if action else 'Unknown'}, eventInfo={eventInfo if eventInfo else 'Unknown'}")
        if action == "1":
            logger.info("PLM Link Record")
        elif action == "2":
            logger.info("Device Link Record")
        elif action == "3":
            logger.info("ISY Link Record")