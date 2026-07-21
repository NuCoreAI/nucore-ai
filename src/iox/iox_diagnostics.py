"""Device-specific (e.g. Insteon) SOAP diagnostics operations.

Kept separate from :class:`~iox.iox_wrapper.IoXWrapper` since these are
legacy, protocol-specific diagnostic commands built on top of
``IoXWrapper.soap_post`` -- not part of the core device/routine/variable API
surface every backend needs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from xml.sax.saxutils import escape as xml_escape

if TYPE_CHECKING:
    # Deferred: IoXWrapper imports IoXDiagnostics (see IoXWrapper.__init__),
    # so a runtime import here would be circular. Only needed for the type
    # hint below -- `from __future__ import annotations` already defers
    # evaluation of the annotation itself.
    from .iox_wrapper import IoXWrapper

class IoXSOAPAction:
    """SOAP action names for IoX SOAP requests.

    These are the same as the Java SDK's ``IoXSOAPActions`` constants, but
    Pythonic (uppercase with underscores) instead of camelCase.
    """

    ################### DEVICE SPECIFIC SOAP ACTIONS ####################
    # DeviceSpecific SOAP action -- device-specific (e.g. Insteon) operations
    # that don't fit a generic ISY service. Envelope shape is the real wire
    # format this hub's /services endpoint expects -- do not "fix" it.
    SOAP_TYPE_DEVICE_SPECIFIC = "DeviceSpecific"
    # The command to get PLM information
    DEVICE_SPECIFIC_GET_PLM_INFO = "G_PLM_INFO"
    # The command to get all PLM links (it's continuous and must be stopped)
    DEVICE_SPECIFIC_GET_ALL_PLM_LINKS = "G_PLM_ALL"
    # The command to get all links in an INSTEON device (it's continuous and must be stopped)
    DEVICE_SPECIFIC_GET_DEV_LINKS_TABLE = "G_DEV_ALL"
    # The command to get all links in an ISY device stored in iox
    DEVICE_SPECIFIC_GET_ISY_LINKS_TABLE = "G_ISY_ALL"
    # The command to stop any pending or in progress device specific activities
    DEVICE_SPECIFIC_STOP_DEVICE_SPECIFIC = "STOP"

    ## SOAP actions actions for other commands
    SOAP_TYPE_RENAME_NETWORK = "RenameNetwork"
    SOAP_TYPE_ADD_NODE = "AddNode"
    SOAP_TYPE_DISCOVER_NODES = "DiscoverNodes"
    SOAP_TYPE_REBOOT = "Reboot"
    SOAP_TYPE_SET_SYSTEM_DATE_TIME = "SetSystemTime"
    # SOAP_TYPE_GET_SYSTEM_DATE_TIME = "GetSystemTime" , we can get it from /rest/time
    SOAP_TYPE_SYNCH_WITH_NTS = "SynchWithNTS"
    SOAP_TYPE_SET_NTP_SETTINGS = "SetNTPOptions"
    SOAP_TYPE_SET_SYSTEM_OPTIONS = "SetSystemOptions"
    SOAP_TYPE_GET_SYSTEM_OPTIONS = "GetSystemOptions"
    SOAP_TYPE_SET_NOT_OPTIONS = "SetNotOptions"
    SOAP_TYPE_GET_SYSTEM_CONFIGURATION = "GetSysConf"
    SOAP_TYPE_GET_SYSTEM_CONFIGURATION_FILES = "GetSysConfFiles"
    SOAP_TYPE_CANCEL_NODES_DISCOVERY = "CancelNodesDiscovery"
    SOAP_TYPE_SET_DEVICE_LINKING_MODE = "SetDeviceLinkMode"
    SOAP_TYPE_GET_SYSTEM_STATUS = "GetSystemStatus"
    SOAP_TYPE_RESTORE_DEVICES_FROM_NODES = "RestoreDevicesFromNodes"
    SOAP_TYPE_RESTORE_DEVICE_FROM_NODE = "RestoreDeviceFromNode"
    SOAP_TYPE_RESTORE_NODES_FROM_DEVICE = "RestoreNodesFromDevice"
    SOAP_TYPE_RESTORE_LINK = "RestoreLink"
    SOAP_TYPE_REPLACE_MODEM = "ReplaceModem"
    SOAP_TYPE_REMOVE_MODEM = "RemoveModem"
    SOAP_TYPE_REPLACE_DEVICE = "ReplaceDevice"
    SOAP_TYPE_GET_DEBUG_LEVEL = "GetDebugLevel"
    SOAP_TYPE_SET_DEBUG_LEVEL = "SetDebugLevel"
    SOAP_TYPE_GET_NODES_CONFIG = "GetNodesConfig"
    SOAP_TYPE_GET_ISY_CONFIG = "GetISYConfig"
    SOAP_TYPE_GET_STARTUP_TIME = "GetStartupTime"
    SOAP_TYPE_GET_FS_STAT = "GetFSStat"
    SOAP_TYPE_GET_LAST_ERROR = "GetLastError"
    SOAP_TYPE_CLEAR_LAST_ERROR = "ClearLastError"
    SOAP_TYPE_GET_NETWORK_CONFIG = "GetNetworkConfig"
    SOAP_TYPE_GET_CURRENT_SYSTEM_STATUS = "GetCurrentSystemStatus"
    SOAP_TYPE_WRITE_DEVICE_UPDATES = "WriteDeviceUpdates"
    SOAP_TYPE_SET_BATTERY_DEVICE_WRITE_MODE = "SetBatteryDeviceWriteMode"
    SOAP_TYPE_SET_BATCH_MODE = "SetBatchMode"


class IoXDiagnostics:
    """Wrapper around an :class:`IoXWrapper` instance providing
    ``DeviceSpecific`` SOAP diagnostic operations.
    """

    def __init__(self, iox_wrapper: IoXWrapper) -> None:
        self._iox_wrapper = iox_wrapper

    def _device_specific_envelope(self, inner: str) -> str:
        """Wrap *inner* (the already-built ``<command>``/``<node>``/... element
        block) in the DeviceSpecific SOAP envelope."""
        return (
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
            "<s:Body>"
            f'<u:{IoXSOAPAction.SOAP_TYPE_DEVICE_SPECIFIC} xmlns:u="urn:udi-com:service:X_Insteon_Lighting_Service:1">'
            f"{inner}"
            f"</u:{IoXSOAPAction.SOAP_TYPE_DEVICE_SPECIFIC}>"
            "</s:Body>"
            "</s:Envelope>"
        )

    async def _submit_device_specific(self, inner_body: str) -> str | None:
        """POST *inner_body* wrapped in the DeviceSpecific SOAP envelope;
        equivalent to the Java client's ``submitSOAPRequest`` (minus HMAC
        signing -- not carried over per instruction). Returns the raw
        response body text, or ``None`` on a connection error or non-200
        response (mirrors the Java method returning ``null`` when
        ``resp == null`` or ``!resp.opStat``)."""
        envelope = self._device_specific_envelope(inner_body)
        response = self._iox_wrapper.soap_post(
            "/services", envelope, soap_action=IoXSOAPAction.SOAP_TYPE_DEVICE_SPECIFIC
        )
        if response is None or response.status_code != 200:
            return None
        return response.text

    async def _send_device_specific(
        self,
        command: str = None,
        node: str = None,
        param1: str = None,
        param2: str = None,
        param3: str = None,
        specs: str = None,
    ) -> str | None:
        """Device-specific (e.g. Insteon) operation that isn't a generic ISY
        service -- three free-form parameter slots (``p1``/``p2``/``p3``).

        Port of the Java SDK's ``sendDeviceSpecific(command, node, param1,
        param2, param3, specs)`` overload, via ``IoXWrapper.soap_post``.

        Args:
            command: The command to perform.
            node:    The affected node's address.
            param1:  Optional parameter 1 (``<p1>``).
            param2:  Optional parameter 2 (``<p2>``).
            param3:  Optional parameter 3 (``<p3>``).
            specs:   Optional raw XML document to embed in ``<CDATA>``,
                     unescaped exactly as the caller supplies it -- this is
                     meant to carry an XML document, not plain text.

        Returns:
            The raw response body text, or ``None`` on failure.
        """
        inner = (
            f"<command>{xml_escape(command or '')}</command>"
            f"<node>{xml_escape(node or '')}</node>"
            f"<p1>{xml_escape(param1 or '')}</p1>"
            f"<p2>{xml_escape(param2 or '')}</p2>"
            f"<p3>{xml_escape(param3 or '')}</p3>"
            "<flag>0</flag>"
            f"<CDATA>{specs or ''}</CDATA>"
        )
        return await self._submit_device_specific(inner)

    async def _send_device_specific_with_option(
        self,
        command: str = None,
        node: str = None,
        option: str = None,
        flag: int = 0,
        specs: str = None,
    ) -> str | None:
        """Device-specific (e.g. Insteon) operation taking a single ``option``
        plus a flag character, instead of three ``p1``/``p2``/``p3`` slots.

        Port of the Java SDK's ``sendDeviceSpecific(command, node, option,
        flag, specs)`` overload, via ``IoXWrapper.soap_post``.

        Args:
            command: The command to perform.
            node:    The affected node's address.
            option:  Optional parameter (``<option>``).
            flag:    Optional hex value (0-255) to send in the ``<flag>`` element; if empty, ``0`` is sent.
            specs:   Optional raw XML document to embed in ``<CDATA>``,
                     unescaped exactly as the caller supplies it.

        Returns:
            The raw response body text, or ``None`` on failure.
        """
        flag_value = int(flag) if flag else 0
        inner = (
            f"<command>{xml_escape(command or '')}</command>"
            f"<node>{xml_escape(node or '')}</node>"
            f"<option>{xml_escape(option or '')}</option>"
            f"<flag>{flag_value}</flag>"
            f"<CDATA>{specs or ''}</CDATA>"
        )
        return await self._submit_device_specific(inner)

    # DEVICE SPECIFIC SOAP ACTIONS
    async def get_plm_info(self) -> str | None:
        return await self.nucore_interface.send_device_specific_with_option(IoXSOAPAction.DEVICE_SPECIFIC_GET_PLM_INFO, None, None, 0x01, None)
    async def get_all_plm_links(self) -> str | None:
        return await self.nucore_interface.send_device_specific_with_option(IoXSOAPAction.DEVICE_SPECIFIC_GET_ALL_PLM_LINKS, None, None, 0x01, None)
    async def get_dev_links_table(self) -> str | None:
        return await self.nucore_interface.send_device_specific_with_option(IoXSOAPAction.DEVICE_SPECIFIC_GET_DEV_LINKS_TABLE, None, None, 0x01, None)
    async def get_isy_links_table(self) -> str | None:
        return await self.nucore_interface.send_device_specific_with_option(IoXSOAPAction.DEVICE_SPECIFIC_GET_ISY_LINKS_TABLE, None, None, 0x01, None)
    async def stop_device_specific(self) -> str | None:
        return await self.nucore_interface.send_device_specific_with_option(IoXSOAPAction.DEVICE_SPECIFIC_STOP_DEVICE_SPECIFIC, None, None, 0x01, None)

    # add node
    async def add_node(self) -> str | None:
        return await self.nucore_interface.send_device_specific_with_option(IoXSOAPAction.SOAP_TYPE_ADD_NODE, None, None, 0x01, None)

    # discover nodes
    async def discover_nodes(self) -> str | None:
        return await self.nucore_interface.send_device_specific_with_option(IoXSOAPAction.SOAP_TYPE_DISCOVER_NODES, None, None, 0x01, None)

    # cancel nodes discovery
    async def cancel_nodes_discovery(self) -> str | None:
        return await self.nucore_interface.send_device_specific_with_option(IoXSOAPAction.SOAP_TYPE_CANCEL_NODES_DISCOVERY, None, None, 0x01, None)

    # get system option
    async def get_system_options(self) -> str | None:
        return await self.nucore_interface.send_device_specific_with_option(IoXSOAPAction.SOAP_TYPE_GET_SYSTEM_OPTIONS, None, None, 0x01, None)

    # get system configuration
    async def get_system_configuration(self) -> str | None:
        return await self.nucore_interface.send_device_specific_with_option(IoXSOAPAction.SOAP_TYPE_GET_SYSTEM_CONFIGURATION, None, None, 0x01, None)

    # get nodes config
    async def get_nodes_config(self) -> str | None:
        return await self.nucore_interface.send_device_specific_with_option(IoXSOAPAction.SOAP_TYPE_GET_NODES_CONFIG, None, None, 0x01, None)

    # get isy config
    async def get_isy_config(self) -> str | None:
        return await self.nucore_interface.send_device_specific_with_option(IoXSOAPAction.SOAP_TYPE_GET_ISY_CONFIG, None, None, 0x01, None)

    # get startup time
    async def get_startup_time(self) -> str | None:
        return await self.nucore_interface.send_device_specific_with_option(IoXSOAPAction.SOAP_TYPE_GET_STARTUP_TIME, None, None, 0x01, None)




