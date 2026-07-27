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
from typing import TYPE_CHECKING, Any
import xml.etree.ElementTree as ET
import time
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



# One diagnostics flow, not a menu of named plans: start_diagnostics opens a
# session and hands the model an instruction plus a catalog of steps it can
# call via run_diagnostic_step, guided by that instruction and by what the
# customer actually described -- the same way Bash is one general tool
# steered by prompt convention, not a different tool per task -- instead of
# the backend pre-mapping every complaint to a canned plan name.
#
# The step catalog (name -> {"function", "description"}) lives entirely in
# prompts/diagnose.md as a fenced ```json block -- NOT duplicated as a second,
# hand-maintained registry here. IoXDiagnostics.__init__ parses that block and
# validates every declared "function" resolves to a real callable method,
# failing loudly at construction time if the prompt and the code have drifted
# apart (malformed JSON, or a function name that no longer exists) -- the
# prompt file is the single source of truth; this class only validates and
# dispatches against it.
_PROMPTS_DIR = Path(__file__).parent / "prompts"
_JSON_BLOCK_RE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)



class IoXDiagnostics:
    """Wrapper around an :class:`IoXWrapper` instance providing
    ``DeviceSpecific`` SOAP diagnostic operations, plus the diagnostics
    session/dispatch logic backing ``NuCoreInterface``'s
    ``start_diagnostics``/``run_diagnostic_step``/``get_running_diagnostic``
    (``IoXWrapper`` just delegates to this class for those).
    """

    # How long a diagnostic session can stay open (without conclude/stop)
    # before a stale lock is cleared automatically and a new one is allowed.
    _DIAGNOSTICS_TIMEOUT_S = 300  # 5 minutes

    def __init__(self, iox_wrapper: IoXWrapper) -> None:
        self._iox_wrapper = iox_wrapper
        # Tracks the one in-flight diagnostic session (see start_diagnostics)
        # -- {"started_at", "status", "candidates"} or None.
        self._diagnostics_state: dict[str, Any] | None = None
        # Parses and validates prompts/diagnose.md once -- fails loudly here,
        # at construction, rather than serving a broken diagnostics feature
        # if the prompt and this class's methods have drifted apart.
        self._diagnostic_instruction, self._diagnostic_steps = self._load_diagnostic_config()
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

    @staticmethod
    def _candidates_payload(
        candidate_devices: list[dict[str, Any]] | None,
        candidate_routines: list[dict[str, Any]] | None,
    ) -> dict[str, Any] | None:
        """Bundle the caller-supplied candidate devices/routines into a
        single ``candidates`` payload, or ``None`` when neither was given --
        so a diagnostic with no fuzzy device/scene reference (the common
        case) doesn't carry a noisy empty key through every response."""
        if not candidate_devices and not candidate_routines:
            return None
        return {"devices": candidate_devices or [], "routines": candidate_routines or []}

    def _load_diagnostic_config(self) -> tuple[str, dict[str, dict[str, Any]]]:
        """Read prompts/diagnose.md and parse/validate it -- see
        _parse_diagnostic_config."""
        text = (_PROMPTS_DIR / "diagnose.md").read_text(encoding="utf-8").strip()
        return self._parse_diagnostic_config(text)

    def _parse_diagnostic_config(self, text: str) -> tuple[str, dict[str, dict[str, Any]]]:
        """Parse *text* (diagnose.md's content) and return
        ``(full_text, step_registry)`` -- the full text (prose + the fenced
        ```json block) is shown to the model verbatim as the "instruction";
        the ```json block is additionally parsed out and validated here so
        the file stays the single source of truth for both the model-facing
        description of each step and the name -> backend-function it
        dispatches to.

        Raises ``RuntimeError`` if the ```json block is missing/malformed, or
        if any declared "function" doesn't resolve to a real callable method
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

        for name, meta in steps.items():
            function_name = meta.get("function")
            if function_name is not None and not callable(getattr(self, function_name, None)):
                raise RuntimeError(
                    f"diagnose.md declares step '{name}' -> function '{function_name}', "
                    "but IoXDiagnostics has no such method"
                )

        return text, steps

    async def start_diagnostics(
        self,
        *,
        session_id: str | None = None,
        candidate_devices: list[dict[str, Any]] | None = None,
        candidate_routines: list[dict[str, Any]] | None = None,
    ) -> Any:
        """
        Open (or re-show) the one diagnostic session -- there's a single
        diagnostics flow, not a menu of named plans. The model reads the
        returned instruction, calls whichever steps (see run_diagnostic_step)
        are relevant to whatever the customer actually described, in whatever
        order makes sense, and ends the session with the "conclude" step (or
        "stop" to abandon early without a diagnosis).

        Only one session may be open at a time, and it's owned by whichever
        *session_id* started it -- calling this again with the SAME
        session_id while it's in progress just re-shows the instruction/steps
        rather than starting a new one; calling it with a DIFFERENT session_id
        is refused, since a diagnostic is a real hub-level operation another
        conversation shouldn't be able to interrupt or restart. A session left
        open longer than _DIAGNOSTICS_TIMEOUT_S is treated as abandoned and
        cleared automatically, allowing a fresh one (from any session) to start.

        :param session_id: Identifies which conversation is starting/driving
                       this session -- unified.dispatch.execute_tool's
                       diagnostics-lock gate is the primary place this is
                       enforced; checked again here for defense-in-depth
                       against any caller that bypasses that gate.
        :param candidate_devices: Optional devices/groups/scenes the caller
                       identified as relevant (e.g. a fuzzy "master bathroom"
                       reference) -- recorded on the session and echoed back
                       in every response for it.
        :param candidate_routines: Same idea as candidate_devices, but for
                       routines/folders.
        :return: {"status": "in_progress", "instruction", "available_tools", "candidates"?}
                 or {"error": ...} if a different session already owns the
                 active one.
        """
        state = self._diagnostics_state
        if state is not None:
            elapsed = time.monotonic() - state["started_at"]
            if elapsed < self._DIAGNOSTICS_TIMEOUT_S:
                if state.get("session_id") != session_id:
                    return {
                        "error": (
                            f"a diagnostic session is already in progress "
                            f"(started {int(elapsed)}s ago, times out after {self._DIAGNOSTICS_TIMEOUT_S}s) "
                            "for a different conversation -- wait for it to finish or time out"
                        )
                    }
                candidates = state.get("candidates")
                response = {
                    "status": "in_progress",
                    "instruction": self._diagnostic_instruction,
                    "available_tools": list(self._diagnostic_steps.keys()),
                    "elapsed_s": int(elapsed),
                }
                if candidates:
                    response["candidates"] = candidates
                return response
            logger.warning(f"diagnostic session exceeded {self._DIAGNOSTICS_TIMEOUT_S}s; clearing stale lock")
            self._diagnostics_state = None

        candidates = self._candidates_payload(candidate_devices, candidate_routines)
        self._diagnostics_state = {
            "started_at": time.monotonic(),
            "status": "in_progress",
            "candidates": candidates,
            "session_id": session_id,
        }
        response = {
            "status": "in_progress",
            "instruction": self._diagnostic_instruction,
            "available_tools": list(self._diagnostic_steps.keys()),
        }
        if candidates:
            response["candidates"] = candidates
        return response

    def _run_diagnostic_step_sync(self, function, params: dict[str, Any]) -> Any:
        """Runs one step's backend call synchronously. This class's methods
        are ``async def`` in name only -- the SOAP/HTTP calls underneath are
        blocking ``requests`` calls, nothing is actually awaited -- so this is
        safe to invoke via asyncio.to_thread in run_diagnostic_step to keep a
        slow step (e.g. get_full_system_config's several sequential HTTP
        calls) off the real event loop."""
        return asyncio.run(function(**params))

    async def run_diagnostic_step(self, step: str, *, session_id: str | None = None, **params) -> Any:
        """
        Run one step of the diagnostic session currently in progress (see
        start_diagnostics) -- the model picks which step to call and with
        what params, guided by the standing instruction, instead of the
        backend pre-scripting a fixed sequence.

        :param step: One of the step names declared in prompts/diagnose.md
                     (also returned as start_diagnostics' "available_tools").
        :param session_id: Must match the session that started the current
                       session (see start_diagnostics) -- checked here for
                       defense-in-depth on top of
                       unified.dispatch.execute_tool's own gate.
        :param params: Forwarded verbatim to the step's underlying function.
        :return: {"step", "result"} on success, or {"error": ...}. The
                 dedicated "conclude"/"stop" steps instead end the session,
                 returning {"status": "completed", "summary"?} or
                 {"status": "stopped", "result"}.
        """
        state = self._diagnostics_state
        if state is None or state["status"] != "in_progress":
            return {"error": "no diagnostic session is in progress -- call start_diagnostics first"}

        elapsed = time.monotonic() - state["started_at"]
        if elapsed >= self._DIAGNOSTICS_TIMEOUT_S:
            logger.warning(f"diagnostic session exceeded {self._DIAGNOSTICS_TIMEOUT_S}s; clearing")
            self._diagnostics_state = None
            return {"status": "timed_out"}

        if state.get("session_id") != session_id:
            return {"error": "this diagnostic session belongs to a different conversation"}

        if step not in self._diagnostic_steps:
            return {"error": f"'{step}' is not a known diagnostic step; see start_diagnostics' available_tools"}

        if step == "conclude":
            self._diagnostics_state = None
            return {"status": "completed", "summary": params.get("summary")}

        if step == "stop":
            self._diagnostics_state = None
            result = await asyncio.to_thread(self._run_diagnostic_step_sync, self.stop_long_running_diagnostic, {})
            return {"status": "stopped", "result": result}

        function_name = self._diagnostic_steps[step].get("function")
        function = getattr(self, function_name, None) if function_name else None
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

    def get_running_diagnostic(self) -> dict[str, Any] | None:
        """
        Return info about the diagnostic session currently in flight, if
        any -- used by unified.dispatch.execute_tool to block every other
        tool call for the whole multi-step session, not just its initial
        call, and to check whether a given tool call's session_id matches
        the one that owns it (the "session_id" key here). A stale
        (past-timeout) session is reported as None -- start_diagnostics/
        run_diagnostic_step clear it on the next real call, this getter just
        doesn't advertise it as active in the meantime.
        """
        state = self._diagnostics_state
        if state is None:
            return None
        elapsed = time.monotonic() - state["started_at"]
        if elapsed >= self._DIAGNOSTICS_TIMEOUT_S:
            return None
        response = {"status": "in_progress", "elapsed_s": int(elapsed), "session_id": state.get("session_id")}
        candidates = state.get("candidates")
        if candidates:
            response["candidates"] = candidates
        return response

    async def stop_long_running_diagnostic(self) -> str | None:
        return await self._iox_wrapper._send_device_specific_with_option(IoXSOAPAction.DEVICE_SPECIFIC_STOP_DEVICE_SPECIFIC, None, None, 0x01, None)

    # get system configuration
    async def _get_full_system_config(self, device_id: str = None) -> dict[str, str] | None:
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
        options = await self._iox_wrapper._submit_soap_request(IoXSOAPAction.SOAP_TYPE_GET_SYSTEM_OPTIONS, None)
        if options is None:
            logger.error(f"Failed to get system options: {options.status_code if options else 'No response'}")
        else: 
            # parse the result into a dict
            try:
                options_config = {}
                root = ET.fromstring(options)
                system_opts = root.find('.//SystemOptions')
                if system_opts is not None:
                    options_config = _element_to_dict_excluding(system_opts)

                self._subsystem_state[Subsystems.INSTEON.value]["enabled"] = options_config.get("INSTEONSupport", False)
                self._subsystem_state[Subsystems.GENERIC_ZWAVE.value]["enabled"] = options_config.get("ZWaveSupport", False)
                self._subsystem_state[Subsystems.ZWAVE.value]["enabled"] = options_config.get("ZMatterZWave", False)
                self._subsystem_state[Subsystems.ZIGBEE.value]["enabled"] = options_config.get("ZigbeeSupport", False)
                self._subsystem_state[Subsystems.MATTER.value]["enabled"] = options_config.get("MatterSupport", False)

            except ET.ParseError as e:
                logger.error(f"Failed to parse system options XML: {e}")

        # second get PLM Infomation and update subsystem state
        if self._subsystem_state[Subsystems.INSTEON.value]["enabled"]:
            plm_info = await self._iox_wrapper._send_device_specific_with_option(IoXSOAPAction.DEVICE_SPECIFIC_GET_PLM_INFO, None, None, 0x01, None)
            if plm_info is None: 
                logger.error(f"Failed to get PLM info: {plm_info.status_code if plm_info else 'No response'}")
            else:
                plm_info_parts = plm_info.split(" / ")
                if len(plm_info_parts) > 1:
                    self._subsystem_state[Subsystems.INSTEON.value]["PLM info"] = plm_info_parts[0]
                    self._subsystem_state[Subsystems.INSTEON.value]["connected"] = plm_info_parts[1] == "Connected"
                else:
                    self._subsystem_state[Subsystems.INSTEON.value]["PLM info"] = plm_info
                    self._subsystem_state[Subsystems.INSTEON.value]["connected"] = False

        subsystems = {}
        for subsystem in self._subsystem_state.values():
            name = subsystem.get("name", "Unknown")
            subsystem.pop("name", None)
            subsystems[name] = subsystem

        # add subsystem_config
        full_config["Subsystem States"] = subsystems
        import json
        logger.info(f"Full system configuration retrieved: {json.dumps(full_config, indent=2)}")
        return full_config

    async def _get_device_family(self, device_id: str = None, **kwargs) -> str | None:
        family_id, family_name = await self._iox_wrapper._get_node_family(device_id)
        if not family_id:
            return "Unknown family"
        return family_name


    # add node
    async def _add_node(self) -> str | None:
        return await self._iox_wrapper._send_device_specific_with_option(IoXSOAPAction.SOAP_TYPE_ADD_NODE, None, None, 0x01, None)

    # discover nodes
    async def _discover_nodes(self) -> str | None:
        return await self._iox_wrapper._send_device_specific_with_option(IoXSOAPAction.SOAP_TYPE_DISCOVER_NODES, None, None, 0x01, None)

    # cancel nodes discovery
    async def _cancel_nodes_discovery(self) -> str | None:
        return await self._iox_wrapper._send_device_specific_with_option(IoXSOAPAction.SOAP_TYPE_CANCEL_NODES_DISCOVERY, None, None, 0x01, None)


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
        
    async def _get_dev_links_table(self, device_id: str = None, **kwargs) -> str | None:
        if self._init_insteon_diag(device_id):
            return await self._insteon_diag._get_dev_links_table(device_id, **kwargs)
        return None

    async def _get_isy_links_table(self, device_id: str = None, **kwargs) -> str | None:
        if self._init_insteon_diag(device_id):
            return await self._insteon_diag._get_isy_links_table(device_id, **kwargs)
        return None

    async def _get_all_plm_links(self, device_id: str = None, **kwargs) -> str | None:
        if self._init_insteon_diag(None):
            return await self._insteon_diag._get_all_plm_links(device_id, **kwargs)
        return None

    async def update_links_table(self, node, control, action, eventInfo):
        if self._insteon_diag is not None:
            await self._insteon_diag.update_links_table(node, control, action, eventInfo)
