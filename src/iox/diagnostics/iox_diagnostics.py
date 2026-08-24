"""Device-specific (e.g. Insteon) SOAP diagnostics operations.

Kept separate from :class:`~iox.iox_wrapper.IoXWrapper` since these are
legacy, protocol-specific diagnostic commands built on top of
``IoXWrapper.soap_post`` -- not part of the core device/routine/variable API
surface every backend needs.
"""


from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any
import xml.etree.ElementTree as ET
from ..iox_definitions import IoXSOAPAction, Subsystems, DEVICE_FAMILIES, get_subsystem_name
from .diag_utils import _element_to_dict_excluding


if TYPE_CHECKING:
    # Deferred: IoXWrapper imports IoXDiagnostics (see IoXWrapper.__init__),
    # so a runtime import here would be circular. Only needed for the type
    # hint below -- `from __future__ import annotations` already defers
    # evaluation of the annotation itself.
    from ..iox_wrapper import IoXWrapper

from utils import get_logger
logger = get_logger(__name__)


# Each diagnostic operation is its own standalone tool now (no
# start_diagnostics/run_diagnostic_step session wrapper, no json-block step
# registry parsed out of a prompt file -- the model-facing description for
# each lives directly on its own tool_*.json, the same as every other tool
# in the system). The guidance prose that used to be delivered as
# start_diagnostics' "instruction" response now lives in
# src/unified/prompt/diagnostics.md, loaded into every system prompt
# unconditionally via prompt_builder.py -- this module has no dependency on
# that file at all.
#
# get_dev_links_table/compare_device_links/get_all_plm_links/
# quick_plm_sanity_check all drive the single PLM serial connection directly
# and cannot run concurrently with each other or with a second call to
# themselves -- see _begin_plm_op/_end_plm_op below. get_full_system_config/
# get_device_family/get_iox_links_table touch no PLM hardware directly
# (get_iox_links_table reads NuCore's own stored database replica, not the
# live device) and run freely, any time, including while one of the four is
# in flight.


class IoXDiagnostics:
    """Wrapper around an :class:`IoXWrapper` instance providing
    ``DeviceSpecific`` SOAP diagnostic operations, plus the seven standalone
    diagnostic methods ``NuCoreInterface`` exposes as tools (``IoXWrapper``
    just delegates to this class for those).
    """

    # Backstop against a wedged PLM operation that never reaches `finally`
    # (see _begin_plm_op) -- not an expected duration for a normal SOAP/
    # serial round-trip, just a ceiling past which a stuck lock is cleared
    # rather than left stuck forever.
    _PLM_OP_TIMEOUT_S = 300  # 5 minutes

    def __init__(self, iox_wrapper: IoXWrapper) -> None:
        self._iox_wrapper = iox_wrapper
        # Tracks whichever of the four PLM-exclusive operations is currently
        # in flight, if any -- {"step", "started_at"} or None. Not owned by
        # any session/conversation: it's a hardware-availability fact, so
        # any caller is refused while any one of the four is running,
        # regardless of who's asking. See _begin_plm_op/_end_plm_op.
        self._plm_op_state: dict[str, Any] | None = None
        self._subsystem_state: dict[str, Any] | None = {
            Subsystems.INSTEON.value: {
                "name": "Insteon",
                "enabled": False,
                "connected": False,
                "PLM info": None,
            },
            Subsystems.GENERIC_ZWAVE.value: {
                "name": "Legacy Z-Wave",
                "enabled": False,
                "connected": False
            },
            Subsystems.ZWAVE.value: {
                "name": "Z-Wave",
                "enabled": False,
                "connected": False
            },
            Subsystems.ZIGBEE.value: {
                "name": "Zigbee",
                "enabled": False,
                "connected": False
            },
            Subsystems.MATTER.value: {
                "name": "Matter",
                "enabled": False,
                "connected": False
            }
        }
        self._insteon_diag = None

    def _begin_plm_op(self, step: str) -> dict[str, Any] | None:
        """Call at the top of each of the four PLM-exclusive methods.
        Returns an error dict if another one is already in progress and not
        yet stale; otherwise marks *step* as the current op and returns
        None. No locking/waiting -- a second caller is refused immediately,
        never blocked or queued."""
        state = self._plm_op_state
        if state is not None:
            elapsed = time.monotonic() - state["started_at"]
            if elapsed < self._PLM_OP_TIMEOUT_S:
                return {
                    "error": (
                        f"a PLM operation ('{state['step']}') is already in progress "
                        f"(started {int(elapsed)}s ago) -- try again shortly"
                    )
                }
            logger.warning(f"PLM op '{state['step']}' exceeded {self._PLM_OP_TIMEOUT_S}s; clearing stale lock")
        self._plm_op_state = {"step": step, "started_at": time.monotonic()}
        return None

    def _end_plm_op(self) -> None:
        self._plm_op_state = None

    async def _get_system_options(self) -> dict[str, Any]:
        """Fetch and parse GetSystemOptions once -- shared by
        get_full_system_config (all 5 subsystems' "enabled" flags) and
        quick_plm_sanity_check's INSTEON-enabled check, so the fetch/parse
        logic lives in exactly one place instead of being duplicated."""
        options = await self._iox_wrapper._submit_soap_request(IoXSOAPAction.SOAP_TYPE_GET_SYSTEM_OPTIONS, None)
        if options is None:
            logger.error("Failed to get system options: no response")
            return {}
        try:
            root = ET.fromstring(options)
            system_opts = root.find('.//SystemOptions')
            return _element_to_dict_excluding(system_opts) if system_opts is not None else {}
        except ET.ParseError as e:
            logger.error(f"Failed to parse system options XML: {e}")
            return {}

    # get system configuration
    async def get_full_system_config(self, **kwargs) -> dict[str, str] | None:
        full_config = {}
        usb_lines = []
        re0_lines = []
        wlan0_lines = []
        iot_provisioned = False

        # now get web configuration
        web_config = self._iox_wrapper.get("/WEB/sysconfig.txt")
        if web_config is None or web_config.status_code != 200:
            logger.error(f"Failed to get web configuration: {web_config.status_code if web_config else 'No response'}")
        else:
            web_config_lines = web_config.text.splitlines()
            os_line = next((line for line in web_config_lines if "FreeBSD" in line), None)
            upc_line = next((line for line in web_config_lines if "UPC:" in line), None)

            in_usb_section = False
            in_nic_section = False
            in_re0_section = False
            in_wlan0_section = False

            for line in web_config_lines:
                if line.strip().startswith("*** Network Interfaces ***"):
                    in_nic_section = True
                    continue

                if line.strip().startswith("*** USB Devices ***"):
                    in_nic_section = False
                    in_usb_section = True
                    continue

                if in_nic_section and (line.strip().startswith("lo0:")):
                    in_re0_section = False
                    continue

                if in_nic_section and (line.strip().startswith("re0:") or in_re0_section):
                    in_re0_section = True
                    re0_lines.append(line)
                    continue

                if in_nic_section and (line.strip().startswith("wlan0:") or in_wlan0_section):
                    in_wlan0_section = True
                    wlan0_lines.append(line)
                    continue

                if in_usb_section and "Upgrade Status" in line:
                    break

                if in_usb_section:
                    usb_lines.append(line)

            full_config["UPC"] = upc_line.strip() if upc_line else None
            full_config["OS"] = os_line.strip() if os_line else None

        interface_adapters = {}
        if re0_lines:
            for line in re0_lines:
                if "status: " in line and "active" in line:
                    interface_adapters["re0"] = re0_lines
                    break

        if wlan0_lines:
            for line in wlan0_lines:
                if "status: " in line and "active" in line:
                    interface_adapters["wlan0"] = wlan0_lines
                    break

        # now get system about
        memory_usage = self._iox_wrapper.get("/api/system/about")
        if memory_usage is None or memory_usage.status_code != 200:
            logger.error(f"Failed to get system about: {memory_usage.status_code if memory_usage else 'No response'}")
        else:
            try:
                payload = memory_usage.json().get("data", {})
                full_config["memory"] = payload.get("memory", {})
                full_config["storage"] = payload.get("storage", {})
            except Exception as e:
                logger.error(f"Failed to parse system about JSON: {e}")


        # the system description from /desc and parse it into a dict
        desc = self._iox_wrapper.get("/desc")
        if desc is None or desc.status_code != 200:
            logger.error(f"Failed to get system description: {desc.status_code if desc else 'No response'}")
        else:
            # parse the result into a dict
            try:
                ns = {"upnp": "urn:schemas-upnp-org:device-1-0"}
                root = ET.fromstring(desc.text)
                system_opts = root.find("upnp:device", ns)
                system_opt = _element_to_dict_excluding(system_opts, exclude={"serviceList"})
                if system_opt is not None:
                    full_config["Current OS Version"] = system_opt.get("currOSVersion", "")
                    full_config["Upgrade-to OS Version"] = system_opt.get("upgradeOSVersion", "")
                    full_config["Friendly Name"] = system_opt.get("friendlyName", "")
                    full_config["MAC Address"] = system_opt.get("UDN", "").replace("uuid:", "")
                    #full_config["Model Name"] = system_opt.get("modelName", "")
                    #full_config["Model Number"] = system_opt.get("modelNumber", "")
                    full_config["Network Interface IP"] = system_opt.get("interfaceIP", "")
                    if len(interface_adapters) > 0:
                        full_config["Network Interface Adapters"] = interface_adapters
                    if len(usb_lines) > 0:
                        full_config["USB Devices"] = usb_lines

                    iot_provisioned = system_opt.get("iotProvisioned", "")

            except ET.ParseError as e:
                logger.error(f"Failed to parse system options XML: {e}")

        # now system software/packages
        upgrades  = self._iox_wrapper.get("/api/system/packages")
        if upgrades is None or upgrades.status_code != 200:
            logger.error(f"Failed to get available upgrades: {upgrades.status_code if upgrades else 'No response'}")
        else:
            try:
                payload = upgrades.json()
                packages: Any = {}

                # API shape can be either {"data": {"packages": ...}} or {"packages": ...}
                if isinstance(payload, dict):
                    data = payload.get("data")
                    if isinstance(data, dict) and "packages" in data:
                        packages = data.get("packages", {})
                    elif "packages" in payload:
                        packages = payload.get("packages", {})
                    else:
                        logger.warning("Available upgrades response did not contain a 'packages' field")
                else:
                    logger.warning("Available upgrades response JSON is not an object")

                for pkg in packages:
                    if isinstance(pkg, dict):
                        pkg.pop("pkgDescr", None)

                full_config["Software Packages"] = packages if packages is not None else {}
            except Exception as e:
                logger.error(f"Failed to parse available upgrades JSON: {e}")

        # Now, system options
        full_config["IoT Provisioned"] = iot_provisioned

        # gets a combined list of:
        # system options, system config, about, and availabe upgrades
        # First get system options and update info in subsystem state
        options_config = await self._get_system_options()
        self._subsystem_state[Subsystems.INSTEON.value]["enabled"] = options_config.get("INSTEONSupport", False)
        self._subsystem_state[Subsystems.GENERIC_ZWAVE.value]["enabled"] = options_config.get("ZWaveSupport", False)
        self._subsystem_state[Subsystems.ZWAVE.value]["enabled"] = options_config.get("ZMatterZWave", False)
        self._subsystem_state[Subsystems.ZIGBEE.value]["enabled"] = options_config.get("ZigbeeSupport", False)
        self._subsystem_state[Subsystems.MATTER.value]["enabled"] = options_config.get("MatterSupport", False)

        # second get PLM Infomation and update subsystem state
        if self._subsystem_state[Subsystems.INSTEON.value]["enabled"]:
            if self._init_insteon_diag(None):
                connected, plm_info = await self._insteon_diag._get_plm_info()

            if connected is None:
                logger.error(plm_info)
            else:
                self._subsystem_state[Subsystems.INSTEON.value]["PLM info"] = plm_info
                self._subsystem_state[Subsystems.INSTEON.value]["connected"] = connected

        subsystems = {}
        for subsystem in self._subsystem_state.values():
            # Copy before popping "name" -- self._subsystem_state lives for
            # this instance's whole lifetime (reused across every call, not
            # just this one), so mutating the stored dict directly destroyed
            # "name" permanently after the first call ever made, collapsing
            # every subsystem into one bogus "Unknown" entry on every call
            # after that.
            entry = dict(subsystem)
            name = entry.pop("name", "Unknown")
            subsystems[name] = entry

        # add subsystem_config
        full_config["Subsystem States"] = subsystems
        import json
        logger.info(f"Full system configuration retrieved: {json.dumps(full_config, indent=2)}")
        return full_config

    async def get_device_family(self, device_id: str = None, **kwargs) -> str | None:
        family_id, family_name = self._iox_wrapper._get_node_family(device_id)
        if not family_id:
            return "Unknown family"
        return family_name


    # get nodes config
    async def _get_nodes_config(self) -> str | None:
        return await self._iox_wrapper._send_device_specific_with_option(IoXSOAPAction.SOAP_TYPE_GET_NODES_CONFIG, None, None, 0x01, None)

    # get isy config
    async def _get_isy_config(self) -> str | None:
        return await self._iox_wrapper._submit_soap_request(IoXSOAPAction.SOAP_TYPE_GET_ISY_CONFIG, None)

    # get startup time
    async def _get_startup_time(self) -> str | None:
        return await self._iox_wrapper._send_device_specific_with_option(IoXSOAPAction.SOAP_TYPE_GET_STARTUP_TIME, None, None, 0x01, None)


    async def on_device_event(self, node, control, action, eventInfo):
        if action == None or control == None:
            logger.error(f"Missing action or control: node={node if node else 'Unknown'}, control={control if control else 'Unknown'}, action={action if action else 'Unknown'}, eventInfo={eventInfo if eventInfo else 'Unknown'}")
            return

        #control is the subsystem that generated the event, e.g. "Insteon", "Zigbee", "Z-Wave", etc.
        #action is of the form of a.b ... where a is the subsystem property and b is the status for that property
        if not control in [ "_21" , "_25", "_27", "_28"]: # zw, zw-zwave, zw-zigbee, zw-matter
            logger.error(f"Unknown control/1: {control} for node={node if node else 'Unknown'}, action={action if action else 'Unknown'}, eventInfo={eventInfo if eventInfo else 'Unknown'}")
            return

        # split the action into the subsystem property and the status
        action_parts = action.split(".")
        if len(action_parts) != 2:
            logger.error(f"Invalid action format: {action} for node={node if node else 'Unknown'}, control={control if control else 'Unknown'}, eventInfo={eventInfo if eventInfo else 'Unknown'}")
            return
        subsystem_property, status = action_parts

        if subsystem_property != "1":  #only interested in status
            return

        if not status in ["1", "2", "3"]:
            logger.error(f"Unknown status: {status} for node={node if node else 'Unknown'}, control={control if control else 'Unknown'}, action={action if action else 'Unknown'}, eventInfo={eventInfo if eventInfo else 'Unknown'}")
            return

        if status == "1":
            self._subsystem_state[control]["enabled"] =  True
        elif status == "2":
            self._subsystem_state[control]["connected"] =  True
        elif status == "3":
            #self._subsystem_state[control]["updated"] =  True
            self._subsystem_state[control]["connected"] =  True

    async def on_node_updated_event(self, node, control, action, eventInfo):
        if not self._iox_wrapper._is_insteon_family(node):
            return
        if self._init_insteon_diag(node):
            await self._insteon_diag.on_node_device_event(node, control, action, eventInfo)

    # ---------------------------------------------------
    # INSTEON DIAGNOSTICS
    # ---------------------------------------------------

    def _init_insteon_diag(self, device_id:str = None) -> bool:
        if device_id != None and not self._iox_wrapper._is_insteon_family(device_id):
            logger.error(f"Device {device_id} is not an Insteon device, cannot initialize Insteon diagnostics.")
            return False

        if self._insteon_diag is None:
            from .insteon_diag import INSTEONDiagnostics
            self._insteon_diag = INSTEONDiagnostics(self._iox_wrapper)

        return True

    async def get_dev_links_table(self, device_id: str = None, **kwargs) -> str | None:
        busy = self._begin_plm_op("get_dev_links_table")
        if busy is not None:
            return busy
        try:
            if self._init_insteon_diag(device_id):
                return await self._insteon_diag._get_dev_links_table(device_id, **kwargs)
            return None
        finally:
            self._end_plm_op()

    async def get_iox_links_table(self, device_id: str = None, **kwargs) -> str | None:
        if self._init_insteon_diag(device_id):
            return await self._insteon_diag._get_iox_links_table(device_id, **kwargs)
        return None

    async def get_all_plm_links(self, **kwargs) -> str | None:
        busy = self._begin_plm_op("get_all_plm_links")
        if busy is not None:
            return busy
        try:
            if self._init_insteon_diag(None):
                return await self._insteon_diag._get_all_plm_links(**kwargs)
            return None
        finally:
            self._end_plm_op()

    async def compare_device_links(self, device_id: str = None, **kwargs) -> str | None:
        busy = self._begin_plm_op("compare_device_links")
        if busy is not None:
            return busy
        try:
            if self._init_insteon_diag(device_id):
                return await self._insteon_diag._compare_device_links(device_id, **kwargs)
            return None
        finally:
            self._end_plm_op()

    async def quick_plm_sanity_check(self, **kwargs) -> Any:
        """System-level INSTEON-enabled check plus INSTEONDiagnostics's own
        PLM-connected/link-count check, merged into one report -- so the
        model never needs to separately call get_full_system_config for
        this scenario. No longer includes core/plugin services status (that
        mechanism was removed -- see run_shell_command + the service
        catalog in the diagnostics prompt for that separately)."""
        busy = self._begin_plm_op("quick_plm_sanity_check")
        if busy is not None:
            return busy
        try:
            if not self._init_insteon_diag(None):
                return None

            options_config = await self._get_system_options()
            insteon_enabled = bool(options_config.get("INSTEONSupport", False))

            lines = [f"INSTEON enabled: {insteon_enabled}"]
            if not insteon_enabled:
                lines.append(
                    "INSTEON is not enabled in system config -- that alone explains no status "
                    "feedback from any Insteon device; nothing else to check until it's enabled."
                )
                return "\n".join(lines)

            insteon_report = await self._insteon_diag._quick_plm_sanity_check(**kwargs)
            return "\n".join(lines) + "\n" + insteon_report
        finally:
            self._end_plm_op()

    async def update_links_table(self, node, control, action, eventInfo):
        if self._insteon_diag is not None:
            await self._insteon_diag.update_links_table(node, control, action, eventInfo)

    async def stop_long_running_diagnostic(self) -> str | None:
        if self._insteon_diag is not None:
            return await self._insteon_diag.stop_insteon_diagnostics()
