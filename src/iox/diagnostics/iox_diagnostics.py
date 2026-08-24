"""Device-specific (e.g. Insteon) SOAP diagnostics operations.

Kept separate from :class:`~iox.iox_wrapper.IoXWrapper` since these are
legacy, protocol-specific diagnostic commands built on top of
``IoXWrapper.soap_post`` -- not part of the core device/routine/variable API
surface every backend needs.
"""


from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
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


# One run_diagnostic_step tool, not a menu of named tools: the model reads
# the step catalog + reasoning prose in unified/diagnostics/prompts/diagnose.md
# (fetched on demand via the get_diagnostics_prompt tool) and calls
# run_diagnostic_step with whichever step name and params fit, guided by
# that prose and by what the customer actually described -- the same way
# Bash is one general tool steered by prompt convention, not a different
# tool per task -- instead of the backend pre-mapping every complaint to a
# canned tool.
#
# The step catalog (name -> {"description"}) lives entirely in diagnose.md
# as a fenced ```json block -- NOT duplicated as a second, hand-maintained
# registry here. __init__ parses that block and validates every declared
# step resolves to a real callable method on this class, failing loudly at
# construction time if the prompt and the code have drifted apart
# (malformed JSON, or a step name that no longer exists) -- the prompt file
# is the single source of truth; this class only validates and dispatches
# against it.
#
# get_dev_links_table/compare_device_links/get_all_plm_links/
# quick_plm_sanity_check all drive the single PLM serial connection directly
# and cannot run concurrently with each other or with a second call to
# themselves -- see _begin_plm_op/_end_plm_op below. get_full_system_config/
# get_device_family/get_iox_links_table touch no PLM hardware directly and
# run freely, any time, including while one of the four is in flight.
_PROMPTS_DIR = Path(__file__).parent.parent.parent / "unified" / "diagnostics" / "prompts"
_JSON_BLOCK_RE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)


class IoXDiagnostics:
    """Wrapper around an :class:`IoXWrapper` instance providing
    ``DeviceSpecific`` SOAP diagnostic operations, plus the diagnostic
    step methods and dispatch logic backing ``NuCoreInterface``'s
    ``run_diagnostic_step`` (``IoXWrapper`` just delegates to this class
    for that).
    """

    def __init__(self, iox_wrapper: IoXWrapper) -> None:
        self._iox_wrapper = iox_wrapper
        # Parses and validates diagnose.md once -- fails loudly here, at
        # construction, rather than serving a broken diagnostics feature if
        # the prompt and this class's methods have drifted apart.
        self._diagnostic_instruction, self._diagnostic_steps = self._load_diagnostic_config()
        # Tracks whichever of the four PLM-exclusive methods (get_dev_links_table/
        # compare_device_links/get_all_plm_links/quick_plm_sanity_check) is
        # currently in flight, if any -- {"step"} or None. This is a
        # hardware-availability fact (the PLM serial connection can only run
        # one link/config operation at a time), not something scoped to a
        # conversation. See _begin_plm_op/_end_plm_op.
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

    def _load_diagnostic_config(self) -> tuple[str, dict[str, dict[str, Any]]]:
        """Read diagnose.md and parse/validate it -- see
        _parse_diagnostic_config."""
        text = (_PROMPTS_DIR / "diagnose.md").read_text(encoding="utf-8").strip()
        return self._parse_diagnostic_config(text)

    def _parse_diagnostic_config(self, text: str) -> tuple[str, dict[str, dict[str, Any]]]:
        """Parse *text* (diagnose.md's content) and return
        ``(full_text, step_registry)`` -- the full text (prose + the fenced
        ```json block) is what get_diagnostics_prompt returns verbatim; the
        ```json block is additionally parsed out and validated here so the
        file stays the single source of truth for both the model-facing
        description of each step and the name -> backend-method it
        dispatches to.

        Raises ``RuntimeError`` if the ```json block is missing/malformed, or
        if any declared step name doesn't resolve to a real callable method
        on this class -- either means the prompt and the code have drifted
        apart, and that should fail loudly rather than silently misbehave.
        """
        match = _JSON_BLOCK_RE.search(text)
        if not match:
            raise RuntimeError("diagnose.md is missing its ```json steps block")

        try:
            steps = json.loads(match.group(1))
        except json.JSONDecodeError as ex:
            raise RuntimeError(f"diagnose.md's ```json steps block is malformed: {ex}") from ex

        for name in steps:
            if not callable(getattr(self, name, None)):
                raise RuntimeError(
                    f"diagnose.md declares step '{name}', but IoXDiagnostics has no method '{name}'"
                )

        return text, steps

    def _run_diagnostic_step_sync(self, function, params: dict[str, Any]) -> Any:
        """Runs one step's backend call synchronously. This class's methods
        are ``async def`` in name only -- the SOAP/HTTP calls underneath are
        blocking ``requests`` calls, nothing is actually awaited -- so this is
        safe to invoke via asyncio.to_thread in run_diagnostic_step to keep a
        slow step (e.g. get_full_system_config's several sequential HTTP
        calls) off the real event loop."""
        return asyncio.run(function(**params))

    async def run_diagnostic_step(self, step: str, **params) -> Any:
        """
        Run one diagnostic step directly against the backend -- no session,
        always available. The model picks which step to call and with what
        params, guided by diagnose.md's prose and step catalog (see
        get_diagnostics_prompt).

        :param step: One of the step names declared in diagnose.md's step
                     catalog.
        :param params: Forwarded verbatim to the step's underlying method.
        :return: {"step", "result"} on success, or {"error": ...}.
        """
        if step not in self._diagnostic_steps:
            return {"error": f"'{step}' is not a known diagnostic step; see get_diagnostics_prompt"}

        function = getattr(self, step, None)
        if function is None or not callable(function):
            return {"error": f"diagnostic step '{step}' is not yet implemented"}

        try:
            result = await asyncio.to_thread(self._run_diagnostic_step_sync, function, params)
        except NotImplementedError as ex:
            return {"error": str(ex)}
        except Exception as ex:
            logger.error(f"diagnostic step '{step}' failed: {ex}")
            return {"error": f"diagnostic step '{step}' failed: {ex}"}

        return {"step": step, "result": result}

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

    async def get_core_services_status(self) -> dict[str, Any]:
        """
        Get the status of core services  (isy, udx, ...)
        :return: Dictionary with the status of each core service
        """
        try:
            # /rest/udx.sys.ops/services.ops/services_status
            response = self._iox_wrapper.post("/api/udx/rest/udx.sys.ops/services.ops/services_status", "e=mc2")
            if response is None or response.status_code != 200:
                logger.error(f"Failed to get core services status: {response.status_code if response else 'No response'}")
                return {"error": f"Failed to get core services status: {response.status_code if response else 'No response'}"}
            return response.json()
        except Exception as e:
            logger.error(f"Failed to get core services status: {e}")
            return {"error": f"Failed to get core services status: {e}"}

    async def get_plugin_services_status(self) -> dict[str, Any]:
        """
        Get the status of core services  (isy, udx, ...)
        :return: Dictionary with the status of each core service
        """
        try:
            # /rest/udx.sys.ops/services.ops/plugin_services_status
            response = self._iox_wrapper.post("/api/udx/rest/udx.sys.ops/services.ops/plugin_services_status", "e=mc2")
            if response is None or response.status_code != 200:
                logger.error(f"Failed to get plugin services status: {response.status_code if response else 'No response'}")
                return {"error": f"Failed to get plugin services status: {response.status_code if response else 'No response'}"}
            return response.json()
        except Exception as e:
            logger.error(f"Failed to get plugin services status: {e}")
            return {"error": f"Failed to get plugin services status: {e}"}

    async def services_ops(self, service:str, op: Literal["start", "stop", "restart"], **kwargs) -> dict[str, Any]:
        """
        An operation on a core or plugin service (start, stop, restart)
        :param service_name: The name of the service to operate on
        :param op: The operation to perform (start, stop, restart)
        :return: Dictionary with the status of each core service or failure
        """
        try:
            # /rest/udx.sys.ops/services.ops/$op
            response = self._iox_wrapper.post(f"/api/udx/rest/udx.sys.ops/services.ops/{op}_service/{service}", "e=mc2")
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

        return True

    async def get_dev_links_table(self, device_id: str = None, **kwargs) -> Any:
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

    async def get_all_plm_links(self, **kwargs) -> Any:
        busy = self._begin_plm_op("get_all_plm_links")
        if busy is not None:
            return busy
        try:
            if self._init_insteon_diag(None):
                return await self._insteon_diag._get_all_plm_links(**kwargs)
            return None
        finally:
            self._end_plm_op()

    async def compare_device_links(self, device_id: str = None, **kwargs) -> Any:
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
        """System-level checks (INSTEON enabled, core services) plus
        INSTEONDiagnostics's own PLM-connected/link-count check, merged into
        one report -- so the model never needs to separately call
        get_full_system_config/get_core_services_status for this scenario.
        """
        busy = self._begin_plm_op("quick_plm_sanity_check")
        if busy is not None:
            return busy
        try:
            if not self._init_insteon_diag(None):
                return None

            options_config = await self._get_system_options()
            insteon_enabled = bool(options_config.get("INSTEONSupport", False))

            try:
                services_status: Any = await self.get_core_services_status()
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
                return "\n".join(lines)

            insteon_report = await self._insteon_diag._quick_plm_sanity_check(**kwargs)
            return "\n".join(lines) + "\n" + insteon_report
        finally:
            self._end_plm_op()

    async def update_links_table(self, node, control, action, eventInfo):
        if self._insteon_diag is not None:
            await self._insteon_diag.update_links_table(node, control, action, eventInfo)
