"""Multi-protocol system/device diagnostics, shared across INSTEON, Z-Wave,
Zigbee, and Matter.

INSTEON-specific diagnostic logic lives on
:class:`~iox.diagnostics.insteon_diag.INSTEONDiagnostics` instead (this
class composes one lazily, via ``self._insteon_diag``) -- this class only
keeps what's genuinely protocol-agnostic (system config, core services,
per-protocol dispatch shells) or shallow enough that no real per-protocol
implementation exists yet.
"""


from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal
import xml.etree.ElementTree as ET
from nucore import DeviceEventListener
from ..iox_definitions import Subsystems, DEVICE_FAMILIES, get_subsystem_name
from .diag_utils import _element_to_dict_excluding


if TYPE_CHECKING:
    # Deferred: IoXWrapper imports IoXDiagnostics (see IoXWrapper.__init__),
    # so a runtime import here would be circular. Only needed for the type
    # hint below -- `from __future__ import annotations` already defers
    # evaluation of the annotation itself.
    from ..iox_wrapper import IoXWrapper
    from nucore import NuCoreInterface

from utils import get_logger
logger = get_logger(__name__)


# Complaint-shaped tools (diagnose_not_responding/diagnose_no_status_feedback,
# below), not a generic run_diagnostic_step(step, **params) menu -- the model
# picks the tool matching the complaint from its own description, the same
# way it already picks send_command vs. get_property, instead of the backend
# exposing raw mechanism (link tables, PLM sanity checks) the model has to be
# taught to sequence itself. This replaced an earlier design where those
# steps were reachable individually via a generic dispatcher whose valid step
# names were parsed out of a prompt file at construction time -- removed.


class _SubsystemStatusListener(DeviceEventListener):
    """Persistent notify() target for non-INSTEON subsystem connectivity
    events (control "_21"/"_25"/"_27"/"_28" -- generic Z-Wave/Z-Wave/
    Zigbee/Matter, per ``Subsystems``). Unlike insteon_diag.py's
    ``_LinksTableWaiter`` (scoped to a single operation, unregistered when
    it's done), this listener is constructed and started exactly once and
    ``process()`` never returns -- it's meant to live for the whole
    process's lifetime, same as the hardcoded dispatch branch it replaces
    in ``IoXWrapper._on_device_event``. Being a daemon thread (see the base
    class), it never blocks interpreter shutdown even though it never
    exits on its own.

    Registers for all four controls up front (the constructor only
    registers the first; the other three are added directly via
    ``register_listener``, reusing the same ``listener_id`` -- see
    ``NuCoreInterface.register_listener``'s note that several independent
    *(control, action)* keys can share one caller-chosen id), with
    ``action=None`` (wildcard) since ``_apply_subsystem_status_event``
    itself further filters on the action's own encoded status field.
    """

    def __init__(self, nucore_interface: "NuCoreInterface", diagnostics: "IoXDiagnostics") -> None:
        self._diagnostics = diagnostics
        super().__init__(nucore_interface, Subsystems.GENERIC_ZWAVE.value, None)
        for control in (Subsystems.ZWAVE.value, Subsystems.ZIGBEE.value, Subsystems.MATTER.value):
            nucore_interface.register_listener(self._listener_id, control, None, self)

    def process(self) -> None:
        while True:
            node, control, action, eventInfo = self._queue.get()
            try:
                self._diagnostics._apply_subsystem_status_event(node, control, action, eventInfo)
            except Exception as ex:
                # A single bad event must never silently end this listener
                # -- there'd be nothing left to report future subsystem
                # status changes for the remainder of the process's life.
                logger.error(f"subsystem status listener failed to process event: {ex}")


class IoXDiagnostics:
    """Wrapper around an :class:`IoXWrapper` instance providing
    protocol-agnostic system/core-service diagnostics, plus the two coarse,
    complaint-shaped diagnostic methods backing ``NuCoreInterface``'s
    ``diagnose_not_responding``/``diagnose_no_status_feedback`` (``IoXWrapper``
    just delegates to this class for those; the actual INSTEON logic lives
    on ``INSTEONDiagnostics``, composed lazily via ``self._insteon_diag``).
    """

    def __init__(self, iox_wrapper: IoXWrapper) -> None:
        self._iox_wrapper = iox_wrapper
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
        # Not started here -- see start_subsystem_status_listener. Some
        # callers construct IoXDiagnostics around a not-yet-fully-initialized
        # NuCoreInterface (e.g. tests using object.__new__(IoXWrapper) for a
        # lightweight fake with no real hub connection), which has no
        # register_listener machinery yet.
        self._subsystem_listener: _SubsystemStatusListener | None = None

    def start_subsystem_status_listener(self) -> None:
        """Start the persistent listener that turns non-INSTEON subsystem
        connectivity events (see _SubsystemStatusListener) into updates to
        self._subsystem_state. Idempotent -- a second call is a no-op.
        Called explicitly by IoXWrapper.__init__ right after constructing
        this instance, once self._iox_wrapper is a fully-initialized
        NuCoreInterface.
        """
        if self._subsystem_listener is not None:
            return
        self._subsystem_listener = _SubsystemStatusListener(self._iox_wrapper, self)
        self._subsystem_listener.start()

    async def _get_system_options(self) -> dict[str, Any]:
        """Fetch subsystem-enabled flags via the plain JSON ``/api/sys``
        endpoint -- confirmed against a live hub to return the same facts
        the old SOAP ``GetSystemOptions`` call did (``insteonSupport``,
        ``zwaveSupport``, ``zMatterZwave``, ``zigbeeSupport``,
        ``matterSupport``), just camelCase instead of the SOAP response's
        PascalCase, and as a plain REST GET instead of a SOAP request --
        simpler to parse, and the same call the node-property-history
        feature already needs for `nodePropertyHistory` (see
        design/history_impl.md), so that feature can reuse this fetch
        instead of adding a second one. Shared by get_full_system_config
        (all 5 subsystems' "enabled" flags) and quick_plm_sanity_check's
        INSTEON-enabled check, so the fetch/parse logic lives in exactly
        one place instead of being duplicated."""
        response = await self._iox_wrapper.get("/api/sys")
        if response is None or response.status_code != 200:
            logger.error(f"Failed to get system options: {response.status_code if response else 'No response'}")
            return {}
        try:
            return response.json().get("data", {}) or {}
        except Exception as e:
            logger.error(f"Failed to parse system options JSON: {e}")
            return {}

    # get system configuration
    async def get_full_system_config(self, **kwargs) -> dict[str, str] | None:
        full_config = {}
        usb_lines = []
        re0_lines = []
        wlan0_lines = []
        iot_provisioned = False

        # now get web configuration
        web_config = await self._iox_wrapper.get("/WEB/sysconfig.txt")
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
        memory_usage = await self._iox_wrapper.get("/api/system/about")
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
        desc = await self._iox_wrapper.get("/desc")
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
        upgrades  = await self._iox_wrapper.get("/api/system/packages")
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
        self._subsystem_state[Subsystems.INSTEON.value]["enabled"] = options_config.get("insteonSupport", False)
        self._subsystem_state[Subsystems.GENERIC_ZWAVE.value]["enabled"] = options_config.get("zwaveSupport", False)
        self._subsystem_state[Subsystems.ZWAVE.value]["enabled"] = options_config.get("zMatterZwave", False)
        self._subsystem_state[Subsystems.ZIGBEE.value]["enabled"] = options_config.get("zigbeeSupport", False)
        self._subsystem_state[Subsystems.MATTER.value]["enabled"] = options_config.get("matterSupport", False)
        full_config["Node Property History Enabled"] = options_config.get("nodePropertyHistory", False)

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
            # this instance's whole lifetime (reused across every diagnostic
            # session, not just this call), so mutating the stored dict
            # directly destroyed "name" permanently after the first call
            # ever made, collapsing every subsystem into one bogus "Unknown"
            # entry on every call after that.
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


    def _apply_subsystem_status_event(self, node, control, action, eventInfo):
        """Handler behind _SubsystemStatusListener.process() -- runs on that
        listener's own persistent thread, not the asyncio event loop, so
        this is plain sync code (nothing here ever awaited anything).
        """
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

    async def get_core_services_status(self) -> dict[str, Any]:
        """
        Get the status of core services  (isy, udx, ...)
        :return: Dictionary with the status of each core service
        """
        try:
            # /rest/udx.sys.ops/services.ops/services_status
            response = await self._iox_wrapper.post("/api/udx/rest/udx.sys.ops/services.ops/services_status", "e=mc2")
            if response is None or response.status_code != 200:
                logger.error(f"Failed to get core services status: {response.status_code if response else 'No response'}")
                return {"error": f"Failed to get core services status: {response.status_code if response else 'No response'}"}
            return response.json()
        except Exception as e:
            logger.error(f"Failed to get core services status: {e}")
            return {"error": f"Failed to get core services status: {e}"}

    async def get_plugin_services_status(self) -> dict[str, Any]:
        """
        Get the status of plugin services. Not model-facing --
        list_installed_plugins' `state` field is the model-facing way to
        check plugin status. Left callable in case something else needs the
        raw per-service response.
        :return: Dictionary with the status of each plugin service
        """
        try:
            # /rest/udx.sys.ops/services.ops/plugin_services_status
            response = await self._iox_wrapper.post("/api/udx/rest/udx.sys.ops/services.ops/plugin_services_status", "e=mc2")
            if response is None or response.status_code != 200:
                logger.error(f"Failed to get plugin services status: {response.status_code if response else 'No response'}")
                return {"error": f"Failed to get plugin services status: {response.status_code if response else 'No response'}"}
            return response.json()
        except Exception as e:
            logger.error(f"Failed to get plugin services status: {e}")
            return {"error": f"Failed to get plugin services status: {e}"}

    async def services_ops(self, service:str, op: Literal["start", "stop", "restart"], **kwargs) -> dict[str, Any]:
        """
        An operation on a core service (start, stop, restart). Not for plugin
        services -- use the ``plugin_ops`` tool for those.
        :param service_name: The name of the service to operate on
        :param op: The operation to perform (start, stop, restart)
        :return: Dictionary with the status of each core service or failure
        """
        try:
            # /rest/udx.sys.ops/services.ops/$op
            response = await self._iox_wrapper.post(f"/api/udx/rest/udx.sys.ops/services.ops/{op}_service/{service}", "e=mc2")
            if response is None or response.status_code != 200:
                logger.error(f"Failed to {op} service {service}: {response.status_code if response else 'No response'}")
                return {"error": f"Failed to {op} service {service}: {response.status_code if response else 'No response'}"}
            try:
                return response.json()
            except Exception as e:
                return f"{service} {op} successful"
        except Exception as e:
            logger.error(f"Failed to {op} service {service}: {e}")
            return {"error": f"Failed to {op} service {service}: {e}"}

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
            self._insteon_diag._iox_diagnostics = self

        return True

    # ---------------------------------------------------
    # Complaint-shaped diagnostics -- diagnose_not_responding/
    # diagnose_no_status_feedback (see NuCoreInterface for the model-facing
    # contract; IoXWrapper just delegates to these two). Only INSTEON has
    # real logic, on INSTEONDiagnostics -- this class just routes by
    # protocol and delegates.
    # ---------------------------------------------------

    async def diagnose_not_responding(self, protocol: str, device_id: str | None = None) -> dict[str, Any]:
        """Customer can't control/reach a device, or nothing happens when
        they try to (the NuCore -> device direction). See
        NuCoreInterface.diagnose_not_responding for the model-facing
        contract. Only INSTEON has real per-device logic so far -- the
        other protocols get a shallow "is the subsystem enabled" check.
        """
        protocol = (protocol or "insteon").lower()
        if protocol == "insteon":
            if self._init_insteon_diag(device_id):
                return await self._insteon_diag.diagnose_not_responding(device_id)
            return None
        if protocol in ("matter", "zwave", "zigbee"):
            return await self._diagnose_protocol_not_responding_stub(protocol, device_id)
        return {"error": f"automated diagnosis for protocol '{protocol}' isn't implemented yet"}

    async def _diagnose_protocol_not_responding_stub(self, protocol: str, device_id: str | None) -> dict[str, Any]:
        """Shallow diagnosis for a protocol without real per-device logic
        yet (matter/zwave/zigbee) -- system-wide only: is the subsystem
        even enabled. Reuses IoXWrapper.is_protocol_enabled rather than
        duplicating its system-options parsing here."""
        if device_id is not None:
            return {"error": f"automated per-device diagnosis for protocol '{protocol}' isn't implemented yet"}
        enabled = await self._iox_wrapper.is_protocol_enabled(protocol)
        if not enabled:
            return {
                "diagnosis": f"{protocol} is not enabled in system configuration.",
                "recommended_fix": f"Enable {protocol} in system configuration.",
            }
        return {
            "diagnosis": (
                f"{protocol} is enabled. Automated per-device diagnosis for {protocol} isn't "
                "implemented yet -- check the specific device/routine driving it manually."
            )
        }

    async def diagnose_no_status_feedback(self, protocol: str, device_id: str | None = None) -> dict[str, Any]:
        """Customer operated a device physically/locally and NuCore didn't
        show the new status (the device -> NuCore direction). Only INSTEON
        has real logic so far -- see NuCoreInterface.diagnose_no_status_feedback.
        """
        if protocol.lower() != "insteon":
            return {"error": f"automated diagnosis for protocol '{protocol}' isn't implemented yet"}
        if self._init_insteon_diag(device_id):
            return await self._insteon_diag.diagnose_no_status_feedback(device_id)
        return None
