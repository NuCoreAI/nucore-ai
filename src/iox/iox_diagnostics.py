"""Device-specific (e.g. Insteon) SOAP diagnostics operations.

Kept separate from :class:`~iox.iox_wrapper.IoXWrapper` since these are
legacy, protocol-specific diagnostic commands built on top of
``IoXWrapper.soap_post`` -- not part of the core device/routine/variable API
surface every backend needs.
"""


from __future__ import annotations

import asyncio
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
    from .iox_wrapper import IoXWrapper

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

STOP_LONG_RUNNING_DIAGNOSTIC = "Stop Long Running Diagnostic"

_IOX_DIAGNOSTICS_PLAN_REGISTRY: dict[str, Any] = {
    # Diagnostics plans are functions that combine more than one low level diagnostics operations into a single plan.
    # For example, why am I not getting any status feedback from my devices
    # The key is what is presented to the user, the value is a dict with the following keys:
    # - "function": the function name to call in IoXDiagnostics
    # - "description": a string describing the plan
    # - "clarification": an optional string with a clarification question to ask the user
    # - "long_running": a boolean indicating if the plan is long running

    "No Device Feedback": {
        "function": "no_device_feedback",
        "description": "Check why your device(s) are not providing status feedback to IoX", 
        "long_running": True,
    },
    "No Device Communication": {
        "function": "no_device_communication",
        "description": "Check why your device(s) are not communicating with IoX",
        "clarification": [
            "Are you experiencing this issue with a single device or multiple devices?",
            "What type of device(s) are you having issues with (e.g. Zigbee, Z-Wave, Matter, INSTEON, etc.)?"
            ],
        "long_running": True,
    },
    "No Remote Connectivity": {
        "function": "no_remote_connectivity",
        "description": "Check why your IoX is not reachable remotely",
        "long_running": False
    },
    "Random Reboots": {
        "function": "random_reboots",
        "description": "Check why your IoX is randomly rebooting",
        "long_running": True,
    },
    "Random All On": {
        "function": "random_all_on",
        "description": "Check why all your IoX devices are turning on randomly",
        "clarification": [
            "Give me as least two devices that exhibited this behavior, and approximate time of the event"
        ],
        "long_running": False,
    },
    STOP_LONG_RUNNING_DIAGNOSTIC: {
        "function": "stop_long_running_diagnostic",
        "description": "Stop any long running diagnostic that is currently running",
        "long_running": False,
    }, 
}

class Subsystems:
    INSTEON = "_0"
    GENERIC_ZWAVE = "_21"
    ZWAVE = "_25"
    ZIGBEE = "_27"
    MATTER = "_28"

class IoXDiagnostics:
    """Wrapper around an :class:`IoXWrapper` instance providing
    ``DeviceSpecific`` SOAP diagnostic operations, plus the diagnostics
    registry/dispatch logic backing ``NuCoreInterface``'s
    ``get_diagnostics_map``/``run_diagnostics``/``get_running_diagnostic``
    (``IoXWrapper`` just delegates to this class for those).
    """

    # How long a long-running diagnostic can stay "running" before a stale
    # lock is cleared automatically and a new call is allowed through.
    _DIAGNOSTICS_TIMEOUT_S = 300  # 5 minutes

    def __init__(self, iox_wrapper: IoXWrapper) -> None:
        self._iox_wrapper = iox_wrapper
        # Tracks the one in-flight diagnostic (see run_diagnostics) --
        # {"function", "started_at", "status", "result"} or None.
        self._diagnostics_state: dict[str, Any] | None = None
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


    def get_diagnostics_map(self) -> list[dict[str, str]]:
        """
        Get the list of diagnostic functions this backend supports.
        :return: list of {"name", "description",  "long_running"} dicts.
        """
        return [
            {"name": name, "description": meta["description"], "long_running": meta["long_running"]}
            for name, meta in _IOX_DIAGNOSTICS_PLAN_REGISTRY.items()
        ]

    def _run_diagnostic_plan(self, function, candidates, **kwargs) -> Any:
        """Runs one DeviceSpecific SOAP call synchronously. This class's
        methods are ``async def`` in name only -- soap_post is a blocking
        ``requests`` call underneath, nothing is actually awaited -- so this
        is safe to invoke via asyncio.to_thread from _run_long_diagnostic to
        keep a multi-minute diagnostic off the event loop."""
        return asyncio.run(
            function(candidates=candidates, **kwargs)
        )

    async def _run_long_diagnostic(self, name: str, function, candidates, **kwargs) -> None:
        """Runs *name* in a worker thread and records the outcome on
        self._diagnostics_state once it finishes -- scheduled as a
        fire-and-forget task by run_diagnostics so starting a long-running
        diagnostic doesn't block the tool call (or the event loop) for its
        full duration."""
        try:
            result = await asyncio.to_thread(self._run_diagnostic_plan, function, candidates, **kwargs)
            if self._diagnostics_state is not None and self._diagnostics_state["name"] == name:
                self._diagnostics_state["status"] = "completed"
                self._diagnostics_state["result"] = result
        except Exception as ex:
            logger.error(f"long-running diagnostic '{name}' failed: {ex}")
            if self._diagnostics_state is not None and self._diagnostics_state["name"] == name:
                self._diagnostics_state["status"] = "error"
                self._diagnostics_state["result"] = str(ex)

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

    async def run_diagnostics(
        self,
        name: str,
        *,
        candidate_devices: list[dict[str, Any]] | None = None,
        candidate_routines: list[dict[str, Any]] | None = None,
        **kwargs,
    ) -> Any:
        """
        Run a diagnostic function by name -- see get_diagnostics_map() for
        the available functions.

        Only one diagnostic may be in flight at a time. Calling this while a
        long-running one is active returns an error instead of starting a
        second one (rather than silently queueing or clobbering it) -- the
        caller should relay that to the customer and ask whether to stop the
        running one, which is exactly what the "STOP" function does; it is
        always allowed through even while another diagnostic is active. A
        long-running diagnostic that's been "running" longer than
        _DIAGNOSTICS_TIMEOUT_S is treated as dead and its lock cleared
        automatically.

        :param name: One of the "name" values from get_diagnostics_map().
        :param candidate_devices: Optional devices/groups/scenes the caller
                       identified as relevant to this diagnostic (e.g. a fuzzy
                       "master bathroom" reference). Every registered
                       diagnostic is still a fixed, argument-less trigger --
                       this doesn't change which SOAP call runs -- it's
                       recorded on the run and echoed back in every response
                       for that run so it's clear what the diagnostic
                       concerns, for functions that may target a specific
                       device later.
        :param candidate_routines: Same idea as candidate_devices, but for
                       routines/folders.
        :param kwargs: Unused today. Present for interface compatibility with
                       functions that may need them later.
        :return: response from the diagnostic function, or None if failure.
        """
        if name not in _IOX_DIAGNOSTICS_PLAN_REGISTRY:
            return {"error": f"'{name}' is not a known diagnostic function; call get_diagnostics_map() first"}

        diag_plan = _IOX_DIAGNOSTICS_PLAN_REGISTRY[name]
        if not diag_plan["function"]:
            return {"error": f"Diagnostic plan for '{name}' is not properly configured"}

        function=diag_plan["function"]
        # does this function exist in our class?
        function=getattr(self, function, None)

        if (function is None) or (not callable(function)):
            return {"error": f"Diagnostic function for '{name}' is not implemented! "}

        if name == STOP_LONG_RUNNING_DIAGNOSTIC:
            result = await self.stop_long_running_diagnostic()
            self._diagnostics_state = None
            return {"diagnostics": name, "status": "stopped", "result": result}

        state = self._diagnostics_state

        # Polling the one this session is already tracking (same function) --
        # never re-starts it, whatever its status. Candidates passed on a
        # poll call are ignored; only what the run actually started with
        # (state["candidates"]) is echoed back.
        if state is not None and state["name"] == name:
            candidates = state.get("candidates")
            if state["status"] == "running":
                elapsed = time.monotonic() - state["started_at"]
                if elapsed >= self._DIAGNOSTICS_TIMEOUT_S:
                    logger.warning(f"diagnostic '{name}' exceeded {self._DIAGNOSTICS_TIMEOUT_S}s; clearing")
                    self._diagnostics_state = None
                    return {"diagnostics": state["name"], "status": "timed_out"}
                response = {"diagnostics": state["name"], "status": "running", "elapsed_s": int(elapsed)}
                if candidates:
                    response["candidates"] = candidates
                return response
            # Completed or errored -- hand back the result once, then clear
            # so a later call starts a genuinely fresh run instead of
            # re-serving a stale cached result forever.
            self._diagnostics_state = None
            response = {"diagnostics": state["name"], "status": state["status"], "result": state["result"]}
            if candidates:
                response["candidates"] = candidates
            return response

        # A *different* diagnostic is active -- true conflict.
        if state is not None:
            if state["status"] == "running":
                elapsed = time.monotonic() - state["started_at"]
                if elapsed < self._DIAGNOSTICS_TIMEOUT_S:
                    return {
                        "error": (
                            f"'{state['name']}' is already running "
                            f"(started {int(elapsed)}s ago, times out after {self._DIAGNOSTICS_TIMEOUT_S}s); "
                            f"ask the customer whether to stop it -- call run_diagnostics with function="
                            f"'{STOP_LONG_RUNNING_DIAGNOSTIC}' to stop it -- or wait for it to finish"
                        )
                    }
                logger.warning(f"diagnostic '{state['name']}' exceeded {self._DIAGNOSTICS_TIMEOUT_S}s; clearing stale lock")
            self._diagnostics_state = None

        candidates = self._candidates_payload(candidate_devices, candidate_routines)

        if not diag_plan["long_running"]:
            # now call the function
            result = await function(candidates=candidates, **kwargs)
            response = {"diagnostics": name, "status": "completed", "result": result}
            if candidates:
                response["candidates"] = candidates
            return response

        self._diagnostics_state = {
            "name": name,
            "function": function,
            "started_at": time.monotonic(),
            "status": "running",
            "result": None,
            "candidates": candidates,
        }
        asyncio.create_task(self._run_long_diagnostic(name, function, candidates, **kwargs))
        response = {
            "diagnostics": name,
            "status": "started",
            "note": (
                f"this can take up to {self._DIAGNOSTICS_TIMEOUT_S}s; call run_diagnostics again with this "
                f"same diagnostic plan to check on it, or with function="
                f"'{STOP_LONG_RUNNING_DIAGNOSTIC}' to stop it"
            ),
        }
        if candidates:
            response["candidates"] = candidates
        return response

    def get_running_diagnostic(self) -> dict[str, Any] | None:
        """
        Return info about the diagnostic currently in flight, if any -- used
        by unified.dispatch.execute_tool to block every other tool call
        while one is running. A stale (past-timeout) "running" state is
        reported as None -- run_diagnostics clears it on the next real call,
        this getter just doesn't advertise it as active in the meantime.
        """
        state = self._diagnostics_state
        if state is None or state["status"] != "running":
            return None
        elapsed = time.monotonic() - state["started_at"]
        if elapsed >= self._DIAGNOSTICS_TIMEOUT_S:
            return None
        response = {"diagnostics": state["name"], "status": "running", "elapsed_s": int(elapsed)}
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

    async def no_device_feedback(self, candidates=None, **kwargs) -> str | None:
        full_config = await self._get_full_system_config()

        return  {"diagnostics": "No Device Feedback", "status": "completed", "results": full_config}

    async def no_device_communication(self, candidates=None, **kwargs) -> str | None:
        return  {"diagnostics": "No Device Communication", "status": "completed"}

    async def no_remote_connectivity(self, candidates=None, **kwargs) -> str | None:
        return  {"diagnostics": "No Remote Connectivity", "status": "completed"}

    async def random_reboots(self, candidates=None, **kwargs) -> str | None:
        return  {"diagnostics": "Random Reboots", "status": "completed"}

    async def random_all_on(self, candidates=None, **kwargs) -> str | None:
        return  {"diagnostics": "Random All On", "status": "completed"}

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
    async def _get_dev_links_table(self) -> str | None:
        return await self._send_device_specific_with_option(IoXSOAPAction.DEVICE_SPECIFIC_GET_DEV_LINKS_TABLE, None, None, 0x01, None)
    async def _get_isy_links_table(self) -> str | None:
        return await self._send_device_specific_with_option(IoXSOAPAction.DEVICE_SPECIFIC_GET_ISY_LINKS_TABLE, None, None, 0x01, None)

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






