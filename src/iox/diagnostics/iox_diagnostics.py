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
from xml.sax.saxutils import escape as xml_escape
import xml.etree.ElementTree as ET
from utils import get_logger
import time


if TYPE_CHECKING:
    # Deferred: IoXWrapper imports IoXDiagnostics (see IoXWrapper.__init__),
    # so a runtime import here would be circular. Only needed for the type
    # hint below -- `from __future__ import annotations` already defers
    # evaluation of the annotation itself.
    from ..iox_wrapper import IoXWrapper

logger = get_logger(__name__)


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

class Subsystems:
    INSTEON = "_0"
    GENERIC_ZWAVE = "_21"
    ZWAVE = "_25"
    ZIGBEE = "_27"
    MATTER = "_28"

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
            Subsystems.INSTEON: {
                "name": "Insteon",
                "enabled": False, 
                "connected": False,
                "updated": False,
                "plm_info": None,
            },
            Subsystems.GENERIC_ZWAVE: {
                "name": "Generic Z-Wave",
                "enabled": False,
                "updated": False,
                "connected": False
            },
            Subsystems.ZWAVE: {
                "name": "Z-Wave",
                "enabled": False,
                "updated": False,
                "connected": False
            },
            Subsystems.ZIGBEE: {
                "name": "Zigbee",
                "enabled": False,
                "updated": False,
                "connected": False
            },
            Subsystems.MATTER: {
                "name": "Matter",
                "enabled": False,
                "updated": False,
                "connected": False
            }
        }


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

        Only one session may be open at a time -- calling this again while
        one is already in progress just re-shows the instruction/steps rather
        than starting a new one. A session left open longer than
        _DIAGNOSTICS_TIMEOUT_S is treated as abandoned and cleared
        automatically, allowing a fresh one to start.

        :param candidate_devices: Optional devices/groups/scenes the caller
                       identified as relevant (e.g. a fuzzy "master bathroom"
                       reference) -- recorded on the session and echoed back
                       in every response for it.
        :param candidate_routines: Same idea as candidate_devices, but for
                       routines/folders.
        :return: {"status": "in_progress", "instruction", "available_tools", "candidates"?}
        """
        state = self._diagnostics_state
        if state is not None:
            elapsed = time.monotonic() - state["started_at"]
            if elapsed < self._DIAGNOSTICS_TIMEOUT_S:
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

    async def run_diagnostic_step(self, step: str, **params) -> Any:
        """
        Run one step of the diagnostic session currently in progress (see
        start_diagnostics) -- the model picks which step to call and with
        what params, guided by the standing instruction, instead of the
        backend pre-scripting a fixed sequence.

        :param step: One of the step names declared in prompts/diagnose.md
                     (also returned as start_diagnostics' "available_tools").
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
        call. A stale (past-timeout) session is reported as None --
        start_diagnostics/run_diagnostic_step clear it on the next real call,
        this getter just doesn't advertise it as active in the meantime.
        """
        state = self._diagnostics_state
        if state is None:
            return None
        elapsed = time.monotonic() - state["started_at"]
        if elapsed >= self._DIAGNOSTICS_TIMEOUT_S:
            return None
        response = {"status": "in_progress", "elapsed_s": int(elapsed)}
        candidates = state.get("candidates")
        if candidates:
            response["candidates"] = candidates
        return response

    def _get_soap_envelope(self, soap_action: str, inner: str) -> str:
        """Wrap *inner* (the already-built ``<command>``/``<node>``/... element
        block) in the DeviceSpecific SOAP envelope."""
        return (
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
            "<s:Body>"
            f'<u:{soap_action} xmlns:u="urn:udi-com:service:X_Insteon_Lighting_Service:1">'
            f"{inner if inner else ''}"
            f"</u:{soap_action}>"
            "</s:Body>"
            "</s:Envelope>"
        )

    @staticmethod
    def _strip_xml_namespace(tag: str) -> str:
        """Return *tag* without the optional ``{namespace}`` prefix."""
        return tag.split("}", 1)[-1] if "}" in tag else tag

    @staticmethod
    def _coerce_xml_text(value: str | None) -> Any:
        """Convert XML text into simple Python scalar types when possible."""
        text = (value or "").strip()
        if text == "":
            return ""

        lower = text.lower()
        if lower == "true":
            return True
        if lower == "false":
            return False
        if text.isdigit():
            return int(text)
        return text

    def _element_to_dict_excluding(
        self,
        element: ET.Element,
        exclude: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Build a dict from *element* children while skipping excluded tags."""
        excluded = set(exclude or [])
        result: dict[str, Any] = {}

        for child in element:
            tag = self._strip_xml_namespace(child.tag)
            if tag in excluded:
                continue

            if list(child):
                value: Any = self._element_to_dict_excluding(child, exclude=excluded)
            else:
                value = self._coerce_xml_text(child.text)

            if tag in result:
                if not isinstance(result[tag], list):
                    result[tag] = [result[tag]]
                result[tag].append(value)
            else:
                result[tag] = value

        return result
    
    async def _submit_soap_request(self, soap_action: str, inner_body: str) -> str | None:
        """POST *inner_body* wrapped in the SOAP envelope for *soap_action*;
        returns the raw response body text, or ``None`` on a connection error
        or non-200 response (mirrors the Java method returning ``null`` when
        ``resp == null`` or ``!resp.opStat``)."""
        envelope = self._get_soap_envelope(soap_action, inner_body)
        response = self._iox_wrapper.soap_post("/services", envelope, soap_action=soap_action)
        if response is None or response.status_code != 200:
            return None
        return response.text

    async def _submit_device_specific(self, inner_body: str) -> str | None:
        """POST *inner_body* wrapped in the DeviceSpecific SOAP envelope;
        equivalent to the Java client's ``submitSOAPRequest`` (minus HMAC
        signing -- not carried over per instruction). Returns the raw
        response body text, or ``None`` on a connection error or non-200
        response (mirrors the Java method returning ``null`` when
        ``resp == null`` or ``!resp.opStat``)."""
        response = await self._submit_soap_request(IoXSOAPAction.SOAP_TYPE_DEVICE_SPECIFIC, inner_body)
        return response
        
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
    
    async def stop_long_running_diagnostic(self) -> str | None:
        return await self._send_device_specific_with_option(IoXSOAPAction.DEVICE_SPECIFIC_STOP_DEVICE_SPECIFIC, None, None, 0x01, None)

    # get system configuration
    async def _get_full_system_config(self) -> dict [str, str] | None:
        full_config = {}
        # gets a combined list of:
        # system options, system config, about, and availabe upgrades
        # First get system options and update info in subsystem state
        options = await self._submit_soap_request(IoXSOAPAction.SOAP_TYPE_GET_SYSTEM_OPTIONS, None)
        if options is None:
            logger.error(f"Failed to get system options: {options.status_code if options else 'No response'}")
        else: 
            # parse the result into a dict
            try:
                options_config = {}
                root = ET.fromstring(options)
                system_opts = root.find('.//SystemOptions')
                if system_opts is not None:
                    options_config = self._element_to_dict_excluding(system_opts)

                self._subsystem_state[Subsystems.INSTEON]["enabled"] = options_config.get("INSTEONSupport", False)
                self._subsystem_state[Subsystems.GENERIC_ZWAVE]["enabled"] = options_config.get("ZWaveSupport", False)
                self._subsystem_state[Subsystems.ZWAVE]["enabled"] = options_config.get("ZMatterZWave", False)
                self._subsystem_state[Subsystems.ZIGBEE]["enabled"] = options_config.get("ZigbeeSupport", False)
                self._subsystem_state[Subsystems.MATTER]["enabled"] = options_config.get("MatterSupport", False)

            except ET.ParseError as e:
                logger.error(f"Failed to parse system options XML: {e}")

        # second get PLM Infomation and update subsystem state
        plm_info = await self._send_device_specific_with_option(IoXSOAPAction.DEVICE_SPECIFIC_GET_PLM_INFO, None, None, 0x01, None)
        if plm_info is None: 
            logger.error(f"Failed to get PLM info: {plm_info.status_code if plm_info else 'No response'}")
        else:
            plm_info_parts = plm_info.split(" / ")
            if len(plm_info_parts) > 1:
                self._subsystem_state[Subsystems.INSTEON]["info"] = plm_info_parts[0]
                self._subsystem_state[Subsystems.INSTEON]["connected"] = plm_info_parts[1] == "Connected"
            else:
                self._subsystem_state[Subsystems.INSTEON]["info"] = plm_info
                self._subsystem_state[Subsystems.INSTEON]["connected"] = False


        # add subsystem_config
        full_config["subsystem_state"] = self._subsystem_state

        # now get the system description from /desc and parse it into a dict 
        desc = self._iox_wrapper.get("/desc")
        if desc is None or desc.status_code != 200:
            logger.error(f"Failed to get system description: {desc.status_code if desc else 'No response'}")
        else:
            # parse the result into a dict
            try:
                
                ns = {"upnp": "urn:schemas-upnp-org:device-1-0"}
                root = ET.fromstring(desc.text)
                system_opts = root.find("upnp:device", ns)
                if system_opts is not None:
                    full_config.update(self._element_to_dict_excluding(system_opts, exclude={"serviceList"}))
            except ET.ParseError as e:
                logger.error(f"Failed to parse system options XML: {e}")
            
        # now get web configuration
        web_config = self._iox_wrapper.get("/WEB/sysconfig.txt")
        if web_config is None or web_config.status_code != 200: 
            logger.error(f"Failed to get web configuration: {web_config.status_code if web_config else 'No response'}")
        else:
            web_config_lines = web_config.text.splitlines()
            upc_line = next((line for line in web_config_lines if "UPC:" in line), None)

            usb_lines = []
            in_usb_section = False

            for line in web_config_lines:
                if line.strip().startswith("*** USB Devices ***"):
                    in_usb_section = True
                    continue

                if in_usb_section and "Upgrade Status" in line:
                    break

                if in_usb_section:
                    usb_lines.append(line)

            full_config["upc"] = upc_line.strip() if upc_line else None
            full_config["usb_devices"] = usb_lines if usb_lines else None

        # now get system about
        options = self._iox_wrapper.get("/api/system/about")
        if options is None or options.status_code != 200:
            logger.error(f"Failed to get system about: {options.status_code if options else 'No response'}")
        else:
            try:
                full_config.update(options.json())
            except Exception as e:
                logger.error(f"Failed to parse system about JSON: {e}")

        # now get available upgrades
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

                full_config["packages"] = packages if packages is not None else {}
            except Exception as e:
                logger.error(f"Failed to parse available upgrades JSON: {e}")

        import json
        logger.info(f"Full system configuration retrieved: {json.dumps(full_config, indent=2)}")
        return full_config


    async def _get_all_plm_links(self) -> str | None:
        return await self._send_device_specific_with_option(IoXSOAPAction.DEVICE_SPECIFIC_GET_ALL_PLM_LINKS, None, None, 0x01, None)
    async def _get_dev_links_table(self, device_id: str = None, **kwargs) -> str | None:
        # NOTE: assumes `node` here accepts the same device address used
        # elsewhere in this system (e.g. get_property's device_id) --
        # unconfirmed against real hub behavior; flag/verify before relying
        # on this for a real customer-facing diagnosis.
        return await self._send_device_specific_with_option(
            IoXSOAPAction.DEVICE_SPECIFIC_GET_DEV_LINKS_TABLE, device_id, None, 0x01, None
        )
    async def _get_isy_links_table(self) -> str | None:
        return await self._send_device_specific_with_option(IoXSOAPAction.DEVICE_SPECIFIC_GET_ISY_LINKS_TABLE, None, None, 0x01, None)

    async def _check_subsystem_status(self, protocol: str = None, **kwargs) -> Any:
        """Per-protocol status check for the "check_subsystem_status" step --
        NOT YET IMPLEMENTED. _get_full_system_config only aggregates whole-
        system state today; there's no per-protocol query yet. Needs the real
        per-protocol data/endpoint before this can do anything meaningful."""
        raise NotImplementedError(
            "check_subsystem_status is not yet implemented -- per-protocol status data isn't wired up yet"
        )

    # add node
    async def _add_node(self) -> str | None:
        return await self._send_device_specific_with_option(IoXSOAPAction.SOAP_TYPE_ADD_NODE, None, None, 0x01, None)

    # discover nodes
    async def _discover_nodes(self) -> str | None:
        return await self._send_device_specific_with_option(IoXSOAPAction.SOAP_TYPE_DISCOVER_NODES, None, None, 0x01, None)

    # cancel nodes discovery
    async def _cancel_nodes_discovery(self) -> str | None:
        return await self._send_device_specific_with_option(IoXSOAPAction.SOAP_TYPE_CANCEL_NODES_DISCOVERY, None, None, 0x01, None)



    # get nodes config
    async def _get_nodes_config(self) -> str | None:
        return await self._send_device_specific_with_option(IoXSOAPAction.SOAP_TYPE_GET_NODES_CONFIG, None, None, 0x01, None)

    # get isy config
    async def _get_isy_config(self) -> str | None:
        return await self._submit_soap_request(IoXSOAPAction.SOAP_TYPE_GET_ISY_CONFIG, None)

    # get startup time
    async def _get_startup_time(self) -> str | None:
        return await self._send_device_specific_with_option(IoXSOAPAction.SOAP_TYPE_GET_STARTUP_TIME, None, None, 0x01, None)


    def on_device_event(self, node, control, action, eventInfo):
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
            self._subsystem_state[control]["updated"] =  True






