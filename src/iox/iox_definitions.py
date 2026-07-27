# Common Definitions across IoX

from enum import Enum

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


class Subsystems(Enum):
    INSTEON = "_0"
    GENERIC_ZWAVE = "_21"
    ZWAVE = "_25"
    ZIGBEE = "_27"
    MATTER = "_28"

get_subsystem_name = lambda subsystem: subsystem.name.replace("_", " ").title()

DEVICE_FAMILY_INSTEON = "1"
DEVICE_FAMILY_LEGACY_Z_WAVE = "4"
DEVICE_FAMILY_PLUGIN = "10"
DEVICE_FAMILY_Z_WAVE = "12"
DEVICE_FAMILY_ZIGBEE = "14"
DEVICE_FAMILY_MATTER = "15"

DEVICE_FAMILIES: dict[str, str] = {
    DEVICE_FAMILY_INSTEON: "INSTEON",
    DEVICE_FAMILY_LEGACY_Z_WAVE: "Legacy Z-Wave",
    DEVICE_FAMILY_PLUGIN: "Plugin",
    DEVICE_FAMILY_Z_WAVE: "Z-Wave",
    DEVICE_FAMILY_ZIGBEE: "Zigbee",
    DEVICE_FAMILY_MATTER: "Matter",
}