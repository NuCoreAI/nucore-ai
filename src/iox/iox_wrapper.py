import json
import sys
import os
import base64
import xml.etree.ElementTree as ET

import requests
import websockets
import urllib3

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from nucore.nucore_interface import NuCoreInterface, PromptFormatTypes
from nucore.nodedef import Property
from nucore.node import Node
from nucore.uom import PREDEFINED_UOMS, UNKNOWN_UOM
from nucore.nucore_error import NuCoreError
from rag import ProfileRagFormatter, MinimalRagFormatter
from typing import Literal
from utils import get_logger

logger = get_logger(__name__)


def debug(msg: str) -> None:
    """Log a debug-level message prefixed with ``[PROFILE FORMAT ERROR]``."""
    logger.debug(f"[PROFILE FORMAT ERROR] {msg}")

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class IoXWrapper(NuCoreInterface):
    """Direct HTTP/WebSocket wrapper for the Universal Devices IoX (ISY) controller.

    Implements the :class:`~nucore.NuCoreInterface` contract so the rest of the
    system can talk to a physical ISY hub without knowing the underlying REST
    protocol.

    **Authentication** — All REST calls use HTTP Basic Auth with the ISY
    credentials.  SSL certificate verification is disabled because IoX hubs
    typically use self-signed certificates.

    **Initialisation modes**

    * *Polyglot mode*: pass a ``poly`` interface instance; the ISY connection
      details are fetched asynchronously via the ``ISY`` subscription event.
    * *Direct mode*: pass ``base_url``, ``username``, and ``password``
      explicitly.

    .. note::
        IoX direct access requires explicit customer permission — it bypasses
        the standard Polyglot data channel.
    """

    def __init__(self, json_output: bool, prompt_format_type: str, poly=None, base_url: str = None, username: str = None, password: str = None) -> None:
        """Initialise the IoXWrapper.

        Args:
            json_output:        When ``True`` the RAG formatter emits JSON;
                                ``False`` produces plain-text markdown.
            prompt_format_type: One of the :class:`~nucore.PromptFormatTypes`
                                constants controlling the prompt layout.
            poly:               Polyglot UDI interface instance.  When
                                provided, ISY credentials are retrieved via the
                                ``ISY`` subscription event instead of being
                                passed directly.
            base_url:           Base URL of the ISY hub
                                (e.g. ``"http://192.168.1.10:80"``).  Required
                                in direct mode.
            username:           ISY username.  Required in direct mode.
            password:           ISY password.  Required in direct mode.

        Raises:
            ValueError: When neither ``poly`` nor all three of ``base_url``,
                        ``username``, and ``password`` are supplied.
        """
        super().__init__(json_output, prompt_format_type)  # Initialize parent with no parameters
        if poly:
            # import only in case we are running in polglot context since 
            # udi_interface redirects standard input/output to polyglot LOGGER
            from udi_interface import udi_interface, unload_interface
            from udi_interface import LOGGER
            self.poly = poly
            self.poly.subscribe(self.poly.ISY, self.__info__)
            message = {'getIsyInfo': {}}
            self.poly.send(message, 'system')
        elif base_url and username and password:
            self.base_url = base_url.rstrip('/')
            self.username= username
            self.password= password
        else:
            logger.error("Either poly or base_url, username, and password must be provided")
            raise ValueError("Either poly or base_url, username, and password must be provided")
        
        self.unauthorized = False

    def __info__(self, info) -> None:
        """Polyglot ISY-info subscription callback.

        Invoked by the Polyglot framework when the ISY connection details
        become available.  Populates ``base_url``, ``username``, and
        ``password`` from the event payload, or sets ``unauthorized = True``
        when the payload is ``None`` (hub not accessible).

        Args:
            info: Dict containing ISY connection metadata, or ``None`` when
                  the hub cannot be reached.
        """
        if info is not None:
            isy_ip = info['isy_ip_address']
            isy_port = info['isy_port']
            if 'isy_https' in info:
                isy_https = info['isy_https'] == 1
            else:
                isy_https = False
            self.base_url = f"{'https' if isy_https else 'http'}://{isy_ip}:{isy_port}"
            self.username = info['isy_username']
            self.password = info['isy_password']
        else:
            self.unauthorized = True
    
    def delete(self, path: str):
        """Send an authenticated HTTP DELETE request to the ISY hub.

        Args:
            path: API path (with or without a leading ``/``).

        Returns:
            :class:`requests.Response`, or ``None`` on connection error.
        """
        try:
            path = path if path.startswith("/") else f"/{path}"
            url=f"{self.base_url}{path}" 
            # Method 1a: Using auth parameter (simplest)
            response = requests.delete(
            url,
            auth=(self.username, self.password),
            verify=False
            )
            if response.status_code != 200:
                logger.error(f"invalid url status code = {response.status_code}")
            return response
        except Exception as ex:
            logger.error (f"failed connection {ex}")
            return None

    def get(self, path: str):
        """Send an authenticated HTTP GET request to the ISY hub.

        Args:
            path: API path (with or without a leading ``/``).

        Returns:
            :class:`requests.Response`, or ``None`` on connection error.
        """
        try:
            path = path if path.startswith("/") else f"/{path}"
            url=f"{self.base_url}{path}" 
            # Method 1a: Using auth parameter (simplest)
            response = requests.get(
            url,
            auth=(self.username, self.password),
            verify=False
            )
            if response.status_code != 200:
                logger.error(f"invalid url status code = {response.status_code}")
            return response
        except Exception as ex:
            logger.error(f"failed connection {ex}")
            return None
    
    def put(self, path: str, body: str, headers: dict):
        """Send an authenticated HTTP PUT request to the ISY hub.

        Args:
            path:    API path.
            body:    Request body string (e.g. JSON-encoded payload).
            headers: HTTP headers dict (e.g. ``{"Content-Type": "application/json"}``).

        Returns:
            :class:`requests.Response`, or ``None`` on connection error.
        """
        try:
            url=f"{self.base_url}{path}"
            response = requests.put(url, auth=(self.username, self.password), data=body, headers=headers,  verify=False)
            if response.status_code != 200:
                logger.error(f"invalid url status code = {response.status_code}")
            return response
        except Exception as ex:
            logger.error(f"failed put: {ex}")
            return None

    def post(self, path: str, body: str, headers: dict):
        """Send an authenticated HTTP POST request to the ISY hub.

        Args:
            path:    API path.
            body:    Request body string.
            headers: HTTP headers dict.

        Returns:
            :class:`requests.Response`, or ``None`` on connection error.
        """
        try:
            url=f"{self.base_url}{path}"
            response = requests.post(url, auth=(self.username, self.password), data=body, headers=headers,  verify=False)
            if response.status_code != 200:
                logger.error(f"invalid url status code = {response.status_code}")
            return response
        except Exception as ex:
            logger.error(f"failed post: {ex}")
            return None
        
    # ------------------------------------------------------------------
    # IoX REST helpers
    # ------------------------------------------------------------------

    def get_profiles(self):
        """Fetch all device/node profiles from the IoX hub.

        Returns:
            Parsed JSON response dict, or ``None`` on failure.
        """
        response = self.get("/rest/profiles")
        if response == None or response.status_code != 200:
            return None
        return response.json()

    def get_nodes(self):
        """Fetch the full node list from the IoX hub as raw XML.

        Returns:
            XML response text string, or ``None`` on failure.
        """
        response = self.get("/rest/nodes")
        if response == None or response.status_code != 200:
            return None
        return response.text

    def get_group_links(self):
        """Fetch all groups, links, and scenes from the IoX hub.

        Returns:
            Parsed JSON response dict, or ``None`` on failure or parse error.
        """
        response = self.get("/api/groups")
        if response == None or response.status_code != 200:
            return None
        try:
            return response.json()
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing JSON response for group links: {e}")
            return None

    async def get_properties(self, device_id: str) -> dict[str, Property]:
        """Fetch the current property values for a device.

        Device IDs stored in the RAG database are Base-64 encoded; this method
        decodes them before issuing the REST call.

        Args:
            device_id: Base-64 encoded IoX node address.

        Returns:
            Dict mapping property ID strings to :class:`~nucore.nodedef.Property`
            instances, or ``None`` on parse/network failure.

        Raises:
            ValueError: When ``device_id`` is empty.
        """
        if not device_id:
            logger.error("Device ID is empty.")
            raise ValueError("Device ID is empty.")
        
        device_id = ProfileRagFormatter.decode_id(device_id)
        response = self.get(f"/rest/nodes/{device_id}")
        if response == None:
            return None
        try:
            root = ET.fromstring(response.text)
            property_elems = root.findall(".//property")
            properties = {}
            
            for p_elem in property_elems:
                prop = Property(
                    id=p_elem.get("id"),
                    value=p_elem.get("value"),
                    formatted=p_elem.get("formatted"),
                    uom=p_elem.get("uom"),
                    prec=int(p_elem.get("prec")) if p_elem.get("prec") else None,
                    name=p_elem.get("name"),
                )
                properties[prop.id] = prop 
        except ET.ParseError as e:
            logger.error(f"Error parsing XML response: {e}")
            return None
        except Exception as e:
            logger.error(f"Error processing properties: {e}")
            return None

        return properties
    
    def get_device_name(self, device_id: str) -> str | None:
        """Resolve a device ID to its human-readable display name.

        Searches nodes, then groups, then folders in that order.  The input
        ``device_id`` is expected to be Base-64 encoded (as stored in the RAG
        database) and is decoded before lookup.

        Args:
            device_id: Base-64 encoded IoX node address.

        Returns:
            Device display name, or ``None`` when not found.

        Raises:
            :class:`~nucore.nucore_error.NuCoreError`: When the node list has
                not been loaded yet.
        """
        if not self.nodes:
            logger.error("No nodes loaded.")
            raise NuCoreError("No nodes loaded.")
        #device id is base64 encoded, decode it
        device_id = ProfileRagFormatter.decode_id(device_id)
        node = self.nodes.get(device_id, None)  # Return None if device_id not found
        if not node:
            node = self.groups.get(device_id, None)
        if not node:
            node = self.folders.get(device_id, None)
        if not node:
            return None
        return node.name if node.name else device_id

    def get_device_id(self, device_str: str) -> str | None:
        """Resolve a device identifier string to its IoX node address.

        Checks for an exact ID match first; falls back to a linear name scan
        across all loaded nodes.

        Args:
            device_str: Raw IoX node address **or** device display name.

        Returns:
            IoX node address string (``node.address``), or ``None`` when not
            found.

        Raises:
            :class:`~nucore.nucore_error.NuCoreError`: When the node list has
                not been loaded yet.
        """
        if not self.nodes:
            logger.error("No nodes loaded.")
            raise NuCoreError("No nodes loaded.")
        #device id is base64 encoded, decode it
        node = self.nodes.get(device_str, None)  # Return None if device_id not found
        if node:
            return node.address

        for node in self.nodes.values():
            if node.name == device_str:
                return node.address
        logger.error(f"Device not found: {device_str}")
        return None
    
    async def send_commands(self, commands: list):
        """Decode device IDs and dispatch commands to the IoX hub.

        Acts as a public-facing wrapper around :meth:`_send_commands` that
        first decodes any Base-64 encoded ``"device"`` values in the command
        list so that the REST URL contains the raw IoX node address.

        Args:
            commands: List of command dicts, each with at minimum a ``"device"``
                      key (Base-64 encoded) and a ``"command"`` key.

        Returns:
            List of :class:`requests.Response` objects from the hub.

        Raises:
            :class:`~nucore.nucore_error.NuCoreError`: When the underlying call
                returns ``None``.
        """
        for cmd in commands:
            if "device" in cmd:
                #device ids are in base64 encoded, decode it
                device_id = cmd["device"]
                cmd["device"] = ProfileRagFormatter.decode_id(device_id)
        response = await self._send_commands(commands)
        if response is None:
            raise NuCoreError("Failed to send commands.")
        return response

    def _get_uom(self, uom) -> int:
        """Normalise a unit-of-measure value to its numeric IoX UOM ID.

        Accepts both integer IDs and string labels/names.  Falls back to
        :data:`~nucore.uom.UNKNOWN_UOM` when no matching entry is found.

        Special cases handled:
        * ``"ENUM"`` / ``"INDEX"`` strings map to UOM ID ``25`` (index).
        * Integer inputs are cast to string before the predefined-UOM lookup.

        Args:
            uom: Integer UOM ID or string label/name (case-insensitive).

        Returns:
            Integer UOM ID, or :data:`~nucore.uom.UNKNOWN_UOM` when unresolved.
        """
        try:
            if isinstance(uom, int):
                # If uom is an integer, check if it is in the predefined UOMs
                uom = str(uom)
            if uom in PREDEFINED_UOMS.keys():
                return int(uom)
            else:
                for _, uom_entry in PREDEFINED_UOMS.items(): 
                    if uom_entry.label.upper() == uom.upper() or uom_entry.name.upper() == uom.upper():
                        return int(uom_entry.id)

                logger.error(f"UOM {uom} is not a known UOM")
                return UNKNOWN_UOM 
        except ValueError:
            if isinstance(uom, str):
                if uom.upper() == "ENUM" or uom.upper() == "INDEX":
                    return 25 #index
                else:
                    for uom_id, uom_entry in PREDEFINED_UOMS.items():
                        if uom_entry.label.upper() == uom.upper() or uom_entry.name.upper() == uom.upper():
                            return int (uom_entry.id)

        return  UNKNOWN_UOM
    
    async def _send_commands(self, commands: list):
        """Build and issue ISY REST command URLs from a normalised command list.

        The IoX REST API for device commands takes the form::

            /rest/nodes/<device_id>/cmd/<command_id>[/<value>[/<uom>]]
                                                   [?<param_id>[.<uom>]=<value>...]

        This method handles all three parameter layouts:

        * **No params** — URL ends at ``/cmd/<command_id>``.
        * **Single param** — value appended as path segments (unnamed) or as a
          query string (named).
        * **Multiple params** — unnamed params appended as path segments first;
          named params appended as query-string key/value pairs.

        The input list can be in two forms:

        * A flat list of command dicts.
        * A list whose first element is ``{"commands": [...]}`` (the LLM
          sometimes wraps the list in an outer object).

        Args:
            commands: List of command dicts, each requiring at minimum
                      ``"device"`` (or ``"device_id"``)
                      and ``"command"`` (or ``"command_id"``).  Optional
                      ``"command_params"`` / ``"parameters"`` list can contain
                      ``{"id", "value", "uom"}`` dicts.

        Returns:
            List of :class:`requests.Response` objects (one per command), or
            ``None`` when the input list is empty.
        """
        responses = []
        if not commands or len(commands) == 0:
            logger.warning("No commands to send")
            return None

        try:
            if isinstance(commands, list) and 'commands' in commands[0]:
                commands = commands[0].get("commands", commands)
            elif isinstance(commands[0], list):
                commands = commands[0] 
        except Exception as ex:
            logger.error(f"Error processing commands: {ex}")
            pass
        
        for command in commands:
            if not isinstance(command, dict):
                logger.error(f"Invalid command format: {command}")
                continue

            device_id = command.get("device") or command.get("device_id")
            if not device_id:
                raise ValueError("No device ID found in command")
            command_id = command.get("command") or command.get("command_id")
            if not command_id:
                raise ValueError("No command ID found in command")
            command_params = command.get("command_params", []) or command.get("parameters", [])
            
            # Construct the url: /rest/nodes/<device_id>/cmd/<command_id>/<params[value]>
            url = f"/rest/nodes/{device_id}/cmd/{command_id}"
            if len(command_params) == 1:
                param = command_params[0]
                id = param.get("id", None) or param.get("name", None)
                uom = param.get("uom", None)
                value = param.get("value", None)
                if value is not None:
                    if id is None or id == '' or id == "n/a" or id == "N/A":
                        url += f"/{value}"
                        if uom is not None and uom != '':
                            url += f"/{self._get_uom(uom)}"
                    else:
                        url += f"?{id}"
                        if uom is not None and uom != '':
                            url += f".{self._get_uom(uom)}"
                        url += f"={value}"
            elif len(command_params) > 1:
                unamed_params = [p for p in command_params if not (p.get("id") or p.get("name"))]
                named_params = [p for p in command_params if (p.get("id") or p.get("name"))]

                for param in unamed_params:
                    value = param.get("value", None)
                    if value is None:
                        logger.error(f"No value found for unnamed parameter in command {command_id}")
                        continue
                    url += f"/{value}"
                    uom = param.get("uom", None)
                    if uom is not None and uom != '':
                        url += f"/{self._get_uom(uom)}"

                no_name_param1 = False
                if len(named_params) > 0:
                    i = 0
                    for param in named_params:
                        the_rest_of_the_url = ""
                        id = param.get("id", None) or param.get("name", None)
                        value = param.get("value", None)
                        if value is None:
                            logger.error(f"No value found for named parameter {id} in command {command_id}")
                            continue
                        if id is None or id == '' or id == "n/a" or id == "N/A":
                            if i == 0:
                                no_name_param1 = True
                                url+= f"/{value}/"
                                i+= 1
                                continue

                            logger.error(f"No id found for named parameter in command {command_id}")
                            continue

                        the_rest_of_the_url = f"?{id}" if i == 0 else f"?{id}" if no_name_param1 else f"&{id}"
                        uom = param.get("uom", None)
                        if uom is not None and uom != '':
                            the_rest_of_the_url += f".{self._get_uom(uom)}"
                        the_rest_of_the_url += f"={value}"
                        url += the_rest_of_the_url
                        i += 1
            responses.append(self.get(url))
        return responses
    
    async def create_automation_routine(self, routine: dict):
        """Translate an LLM-generated routine definition and submit it to IoX.

        The LLM produces device names and symbolic UOM/value pairs.  This
        method:

        1. Resolves device names → raw IoX node addresses (Base-64 decoded).
        2. Scales numeric values by ``10 ** precision`` where required by the
           IoX API (precision scaling does *not* apply to UOM 25 / INDEX).
        3. Builds a normalised ``{name, parent, enabled, if, then, else}``
           dict and passes it to :meth:`_create_routine`.

        Args:
            routine: Dict with keys ``name``, ``parent``, ``enabled``,
                     ``if`` (list of condition dicts), ``then`` (list of action
                     dicts), and ``else`` (list of else-action dicts).

        Returns:
            :class:`requests.Response` from :meth:`_create_routine`, or
            ``None`` when processing fails.

        Raises:
            :class:`~nucore.nucore_error.NuCoreError`: When the routine dict
                is empty or ``None``.
        """
        if not routine:
            raise NuCoreError ("No valid routine provided.")
        try: 
            out_routine={
                "name": f"{routine['name']}",
                "parent": routine['parent'],
                "enabled": routine['enabled'] ,
                "if": [],
                "then": [],
                "else": []
            }
            ifs = routine.get("if", None)
            if ifs is not None and len (ifs) > 0:
                for if_ in ifs:
                    keys = list(if_.keys())
                    if "comp" in keys or "eq" in keys:
                        condition = if_
                        if not isinstance(condition, dict):
                            continue
                        if not "device" in condition or not "precision" in condition or not "value" in condition or not "uom" in condition:
                            continue
                        device_id = condition.get("device", None)
                        if device_id is None:
                            continue
                        device_id = self.get_device_id(device_id)
                        if device_id is None: 
                            continue
                        # device ids are in base64 encoded, decode it
                        device_id = ProfileRagFormatter.decode_id(device_id)
                        condition["device"] = device_id
                        uom_id = condition.get("uom", None)
                        precision = condition.get("precision", None)
                        value = condition.get("value", None)
                        if uom_id is None or int(uom_id) == 25 or precision is None or value is None:
                            continue
                        value = value * (10 ** precision)
                        condition["value"] = int(value)
                    out_routine['if'].append(if_)
            
            thens = routine.get("then", None)
            if thens is not None and len (thens) > 0:
                for then in thens:
                    device_id = then.get("device", None)
                    if device_id is not None:
                        device_id = self.get_device_id(device_id)
                        if device_id is None: 
                            continue
                        # device ids are in base64 encoded, decode it
                        device_id = ProfileRagFormatter.decode_id(device_id)
                        then["device"] = device_id
                    parameters = then.get("parameters", None)
                    if parameters is not None:
                        for param in parameters:
                            uom_id = param.get("uom", None)
                            precision = param.get("precision", None)
                            value = param.get("value", None)
                            if precision is not None:
                                prec = int(precision)
                                if uom_id is not None and int(uom_id) != 25: 
                                    value = value * (10 ** prec)
                                    param["value"] = value 
                    out_routine['then'].append(then)
            elses = routine.get("else", None)
            if elses is not None and len (elses) > 0:
                for else_ in elses:
                    device_id = else_.get("device", None)
                    if device_id is not None:
                        device_id = self.get_device_id(device_id)
                        if device_id is None:
                            #remove this else from elses
                            logger.error(f"Device not found for else condition: {else_}")
                            continue
                        # device ids are in base64 encoded, decode it
                        device_id = ProfileRagFormatter.decode_id(device_id)
                        else_["device"] = device_id
                    parameters = else_.get("parameters", None)
                    if parameters is not None:
                        for param in parameters:
                            uom_id = param.get("uom", None)
                            precision = param.get("precision", None)
                            value = param.get("value", None)
                            if precision is not None:
                                prec = int(precision)
                                if uom_id is not None and int(uom_id) != 25: 
                                    value = value * (10 ** prec)
                                    param["value"] = value
                    out_routine['else'].append(else_)

        except Exception as e:
            logger.error(f"Failed to process routine: {str(e)}")
            return None

        logger.info("****Routine after processing:") 
        logger.info(json.dumps(out_routine, indent=4))
        response=self._create_routine(out_routine)
        return response

    def _create_routine(self, program: dict):
        """Submit a processed routine definition to the IoX hub via PUT.

        Args:
            program: Normalised routine dict with ``name``, ``parent``,
                     ``enabled``, ``if``, ``then``, and ``else`` keys.

        Returns:
            :class:`requests.Response`, or ``False`` when ``program`` is
            empty.
        """
        response=None
        try:
            program_content = {
                'routine': program
            }
            headers = {
                "Content-Type": "application/json"
            }
            response = self.put(f'/api/ai/trigger', body=json.dumps(program_content), headers=headers)
        except Exception as ex:
            logger.error(f"Error creating routine: {ex}")
        
        return response

    
    async def get_all_routines_summary(self):
        """Fetch lightweight summary records for all routines from the hub.

        Returns the ``data`` array from ``/api/ai/programs``.  Each entry
        contains runtime state (enabled, last-run, etc.) but not the full
        trigger/action logic.

        Returns:
            List of routine summary dicts, or the raw response / ``None`` on
            failure.
        """
        response = self.get("/api/ai/programs")
        if response == None or response.status_code != 200:
            return response if response else None
        return response.json()['data']

    async def get_routine_summary(self, program_id: int):
        """Fetch the runtime summary for a single routine.

        Accepts integer or hex-string IDs and normalises to ``int`` before
        calling the API.  Note that the API response includes folder entries
        as well as the target program.

        Args:
            program_id: Integer routine ID or hex string (e.g. ``"1a2b"``).

        Returns:
            ``data`` list from the API response, or ``None`` on failure.
        """
        if program_id is None or program_id == '':
            logger.error("Program ID cannot be empty")
            return None
        #if it's a hex string, convert it to int since the API expects int ids
        if not isinstance(program_id, int):
            try:            
                program_id = int(program_id)
            except ValueError:
                if isinstance(program_id, str):
                    try:
                        program_id = int(program_id)
                    except ValueError:
                        #probaby hex, convert using hex
                        try:
                            program_id = int(program_id, 16)
                        except ValueError:
                            logger.error(f"Invalid program ID format: {program_id}. It should be an integer or a hex string.")
                            return None

        response = self.get(f"/api/ai/program/{program_id}")
        if response == None or response.status_code != 200:
            return response if response else None
        try:
            return response.json()['data']
        except Exception as ex:
            logger.error(f"Error retrieving routine summary: {ex}")
            return None

    async def get_all_routines(self):
        """Fetch complete trigger/action definitions for all routines.

        Returns the ``data`` array from ``/api/ai/triggers`` which includes
        the full ``if``/``then``/``else`` logic for every routine.

        Returns:
            List of full routine dicts, or the raw response / ``None`` on
            failure.
        """
        response = self.get("/api/ai/triggers")
        if response == None or response.status_code != 200:
            return response if response else None
        try:
            return response.json()['data']
        except Exception as ex:
            logger.error(f"Error retrieving all routines: {ex}")
            return None
    
    async def get_routine(self, program_id: str):
        """Fetch the complete trigger/action definition for a single routine.

        Args:
            program_id: Routine ID (integer or string form accepted by the
                        ``/api/ai/trigger/<id>`` endpoint).

        Returns:
            ``data`` value from the API response, or ``None`` on failure.
        """
        response = self.get(f"/api/ai/trigger/{program_id}")
        if response == None or response.status_code != 200:
            return response if response else None
        try:
            return response.json()['data']
        except Exception as ex:
            logger.error(f"Error retrieving routine: {ex}")
            return None

    def update_routine(self, program: dict):
        """Update an existing routine on the hub via POST.

        Args:
            program: Updated routine dict (same schema as :meth:`_create_routine`).

        Returns:
            :class:`requests.Response`, or ``False`` when ``program`` is empty.
        """
        if not program:
            return False
        response=None
        try:
            program_content = {
                'routine': program
            }
            headers = {
                "Content-Type": "application/json"
            }
            response = self.post(f'/api/ai/trigger', body=json.dumps(program_content), headers=headers)
        except Exception as ex:
            logger.error(f"Error updating routine: {ex}")
        
        return response 

    def delete_routine(self, program_id: str):
        """Delete a routine by its ID.

        Args:
            program_id: Routine ID string.

        Returns:
            :class:`requests.Response`, or ``None`` when ``program_id`` is
            falsy or on error.
        """
        if not program_id:
            return None
        try:
            response = self.delete(f'/api/ai/trigger/{program_id}')
        except Exception as ex:
            logger.error(f"Error deleting routine: {ex}")
        
        return response 

    async def routine_ops(
        self,
        routine_id: int,
        operation: Literal["runIf", "runThen", "runElse", "stop", "enable", "disable", "enableRunAtStartup", "disableRunAtStartup"],
    ):
        """Perform a lifecycle operation on an IoX routine.

        For all operations except ``"delete"``, the routine ID is converted
        to a zero-padded 4-digit hex string (e.g. ``"001a"``) because that is
        the format expected by the ``/rest/programs`` endpoint.

        Args:
            routine_id: Integer or hex-string routine ID.
            operation:  One of the valid IoX program operations.

        Returns:
            :class:`requests.Response` from the hub, or ``None`` when the
            routine ID is falsy or the operation is unrecognised.
        """
        if not routine_id:
            return None
        if operation not in ["delete", "runIf", "runThen", "runElse", "stop", "enable", "disable", "enableRunAtStartup", "disableRunAtStartup"]:
            logger.error(f"Invalid operation: {operation}")
            return None
        try:
            if operation == "delete":
                response = self.delete(f'/api/ai/trigger/{routine_id}')
            else:
                if isinstance(routine_id, str):
                    try:
                        routine_id = int(routine_id)
                        #convert it to 4 digit hex string without 0x prefix since that's what the API expects
                        routine_id = format(routine_id, '04x')
                    except ValueError:
                        #already in hex
                        pass
                response = self.get(f'/rest/programs/{routine_id}/{operation}')
        except Exception as ex:
            logger.error(f"Error performing routine operation: {ex}")
        
        return response
    
    # ------------------------------------------------------------------
    # WebSocket event subscription
    # ------------------------------------------------------------------

    async def _subscribe_events(
        self,
        on_message_callback,
        on_connect_callback=None,
        on_disconnect_callback=None,
    ):
        """Open a WebSocket subscription to the ISY event stream.

        Connects to ``/rest/subscribe`` over ``ws://`` or ``wss://`` (SSL
        certificate verification is disabled for self-signed certs).  Parses
        each incoming XML message into a normalised event dict and forwards it
        to ``on_message_callback``.

        Event dict schema::

            {
                'seqnum':    str | None,
                'sid':       str | None,
                'timestamp': str | None,
                'control':   str | None,
                'action': {
                    'value': str | None,
                    'uom':   str | None,
                    'prec':  str | None,
                },
                'node':      str | None,
                'fmtAct':    str | None,
                'fmtName':   str | None,
                'eventInfo': bytes | None,  # raw XML bytes when present
            }

        Args:
            on_message_callback:    Async callable receiving the event dict.
            on_connect_callback:    Optional async callable invoked on
                                    successful WebSocket connection.
            on_disconnect_callback: Optional async callable invoked when the
                                    WebSocket closes.

        Returns:
            ``True`` when the loop ends cleanly; ``False`` on connection
            failure.
        """

        try:
            import ssl
            if self.base_url.startswith("https"):
                ws_url = self.base_url.replace("https", "wss") + "/rest/subscribe"
                ssl_context= ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
            else:
                ws_url = self.base_url.replace("http", "ws") + "/rest/subscribe"
                ssl_context=None
            #make base64 authorization header
            credentials = f"{self.username}:{self.password}"
            encoded_credentials = base64.b64encode(credentials.encode()).decode()
            headers = {
                "Authorization": f"Basic {encoded_credentials}"
            }
            async with websockets.connect(ws_url, ssl=ssl_context, additional_headers=headers) as websocket:
                if on_connect_callback:
                    await on_connect_callback()
                try:
                    async for message in websocket:
                        if on_message_callback:
                            try:
                                #parse the xml message
                                root = ET.fromstring(message)
                                control = root.find('control')
                                action = root.find('action')
                                node = root.find('node')
                                fmtAct = root.find('fmtAct')
                                fmtName = root.find('fmtName')
                                eventInfo = root.find('eventInfo')
                                event_data = {
                                    'seqnum': root.get('seqnum', None ),
                                    'sid': root.get('sid', None),
                                    'timestamp': root.get('timestamp', None),
                                    'control': control.text if control is not None else None,
                                    'action': {
                                        'value': action.text if action is not None else None,
                                        'uom': action.get('uom', None) if action is not None else None,
                                        'prec': action.get('prec', None) if action is not None else None
                                    },
                                    'node': node.text if node is not None else None,
                                    'fmtAct': fmtAct.text if fmtAct is not None else None,
                                    'fmtName': fmtName.text if fmtName is not None else None,
                                    'eventInfo': ET.tostring(eventInfo) if eventInfo is not None else None
                                }
                                if on_message_callback:
                                    await on_message_callback(event_data)
                            except Exception as ex:
                                logger.error(f"Failed to process incoming message: {str(ex)}: {message}")
                                continue
                #except websockets.ConnectionClosed:
                except websockets.ConnectionClosed :
                    logger.error("WebSocket connection closed")
                    if on_disconnect_callback:
                        await on_disconnect_callback()
        except Exception as ex:
            logger.error(f"Failed to subscribe to events: {str(ex)}")
            return False
        return True

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    async def _load(self, **kwargs):
        """Load devices, profiles, and build the RAG stores.

        Keyword Args:
            include_profiles (bool): Include profile data in the load.
                Defaults to ``True``.
            profile_path (str | None): Path to a local profile JSON file.
                When omitted, profiles are fetched from the hub.
            nodes_path (str | None): Path to a local nodes XML file.
                When omitted, nodes are fetched from the hub.

        Returns:
            ``True`` on success.

        Raises:
            :class:`~nucore.nucore_error.NuCoreError`: When no valid profile
                or nodes source is provided, or when the RAG processor is not
                initialised.
        """
        include_profiles = kwargs.get("include_profiles", True)

        self._load_devices(include_profiles=include_profiles, profile_path=kwargs.get("profile_path"), nodes_path=kwargs.get("nodes_path"))
        self.rags= self._format_nodes() 
        if not self.rags:
            logger.warning(f"No RAG documents found for node {self.nuCore.url}. Skipping.")
        self.summary_rags = self.format_nodes_summary(False)
        return True

    # Load only devices to get the latest live state.
    def _load_devices(
        self,
        include_profiles: bool = True,
        profile_path: str = None,
        nodes_path: str = None,
        groups_path: str = None,
    ):
        """Load nodes, groups, folders (and optionally profiles) into memory.

        Args:
            include_profiles: When ``True`` (default) the profile mapping is
                loaded before nodes.
            profile_path:     Optional path to a local profile JSON file.
            nodes_path:       Optional path to a local nodes XML file.
            groups_path:      Optional path to a local group-links JSON file.

        Returns:
            The loaded nodes dict, or ``None`` when loading fails.
        """
        if include_profiles:
            if not self.__load_profile__(profile_path):
                return None
        
        root = self.__load_nodes__(nodes_path)
        if root == None:
            return None

        glinks_root = self.__load_groups_links__(groups_path) 
        self.runtime_profiles, self.nodes, self.groups, self.folders = self.profile.map_nodes(root, glinks_root) 

        return self.nodes
        
    def __load_profile__(self, profile_path: str = None) -> bool:
        """Load the device/node profile from a file or the hub.

        Args:
            profile_path: Path to a local profile JSON file.  When omitted,
                          the profile is fetched from the hub via
                          :meth:`get_profiles`.

        Returns:
            ``True`` on success.

        Raises:
            :class:`~nucore.nucore_error.NuCoreError`: On any load failure.
        """
        try:
            if profile_path:
                self.profile.load_from_file(profile_path)
            else:
                response = self.get_profiles()
                if response is None:
                    raise NuCoreError("Failed to fetch profile from URL.")
                self.profile.load_from_json(response)
                return True
        except Exception as e:
            raise NuCoreError(f"Failed to load profile: {str(e)}")

        return False 
        
    def __load_nodes__(self, nodes_path: str = None):
        """Load the node list from a file or the hub.

        Args:
            nodes_path: Path to a local nodes XML file.  When omitted, nodes
                        are fetched from the hub via :meth:`get_nodes`.

        Returns:
            Parsed XML root element.

        Raises:
            :class:`~nucore.nucore_error.NuCoreError`: When the source is
                unavailable or returns ``None``.
        """
        if nodes_path:
            return Node.load_from_file(nodes_path)
        
        response = self.get_nodes()
        if response is None:
            raise NuCoreError("Failed to fetch nodes from URL.")
        return Node.load_from_xml(response)
        
        raise NuCoreError("No valid nodes source provided.")

    def __load_groups_links__(self, groups_path: str = None):
        """Load group/scene link definitions from a file or the hub.

        Args:
            groups_path: Path to a local groups JSON file.  When omitted,
                         group links are fetched from the hub via
                         :meth:`get_group_links`.

        Returns:
            Parsed groups JSON object.

        Raises:
            :class:`~nucore.nucore_error.NuCoreError`: When the source is
                unavailable or returns ``None``.
        """
        if groups_path:
            return Node.load_from_json(groups_path)
        
        response = self.get_group_links()
        if response is None:
            raise NuCoreError("Failed to fetch group links from URL.")
        return Node.load_from_json(response)

    # ------------------------------------------------------------------
    # RAG formatting
    # ------------------------------------------------------------------

    def _formatter_format_nodes(self, device_rag_formatter: ProfileRagFormatter = None):
        """Format loaded nodes using the provided formatter instance.

        Selects the format call signature based on ``self.formatter_type``:
        ``PROFILE`` mode passes runtime profiles; ``DEVICE`` mode passes only
        nodes/groups/folders.

        Args:
            device_rag_formatter: Formatter instance to use.

        Returns:
            List of formatted RAG document strings.

        Raises:
            :class:`~nucore.nucore_error.NuCoreError`: When nodes are not
                loaded or the formatter is ``None``.
        """
        if not self.nodes or device_rag_formatter is None:
            raise NuCoreError("No nodes loaded.")
        
        if self.formatter_type == PromptFormatTypes.PROFILE:
            return device_rag_formatter.format(profiles=self.runtime_profiles, nodes=self.nodes, groups=self.groups, folders=self.folders ) 
        if self.formatter_type == PromptFormatTypes.DEVICE:
            return device_rag_formatter.format(nodes=self.nodes, groups=self.groups, folders=self.folders ) 
        
        debug(f"Unknown formatter type: {self.formatter_type}, defaulting to per-device format.")
        return device_rag_formatter.format(nodes=self.nodes, groups=self.groups, folders=self.folders)
    
    def _format_nodes(self):
        """Build full-detail RAG documents for all loaded nodes.

        Instantiates a :class:`~rag.ProfileRagFormatter` and delegates to
        :meth:`_formatter_format_nodes`.

        Returns:
            List of formatted RAG document strings.

        Raises:
            :class:`~nucore.nucore_error.NuCoreError`: When nodes are not
                loaded.
        """
        if not self.nodes:
            raise NuCoreError("No nodes loaded.")
        device_rag_formatter = ProfileRagFormatter(json_output=self.json_output)
        return self._formatter_format_nodes(device_rag_formatter)

    def format_nodes_summary(self, condense_profiles: bool):
        """Build a compact device-summary RAG for use in the router/filter prompt.

        When ``condense_profiles`` is ``True`` the output is a single JSON
        object that groups devices by shared commands, properties, and enum
        values — keeping the prompt token count low::

            {
                "devices": ["Nest Matter Family Room", "Meros Smart Plug", ...],
                "cmds":  {"Cool Setpoint": [0, 8, 19], "On": [1, 3, 4], ...},
                "props": {"Temperature": [0, 8, 9], "Mode": [0, 8, 19], ...},
                "enums": {"Off": [0, 3, 4], "On": [4, 13, 15], ...}
            }

        Args:
            condense_profiles: When ``True`` condenses shared-feature output;
                               when ``False`` emits one entry per device.

        Returns:
            :class:`~rag.MinimalRagFormatter` result object.

        Raises:
            :class:`~nucore.nucore_error.NuCoreError`: When nodes are not
                loaded.
        """
        if not self.nodes:
            raise NuCoreError("No nodes loaded.")
        device_rag_formatter = MinimalRagFormatter(json_output=self.json_output, condense=condense_profiles)
        return self._formatter_format_nodes(device_rag_formatter)
    
    async def _load_routines(self) -> None:
        """Fetch all routines from the hub and populate the in-memory stores.

        Builds two parallel stores:

        * ``self.all_routines`` — maps ``routine_id → full routine dict``
          (including trigger/action logic).  Used by the
          ``routine_status_ops`` handler for direct access.
        * ``self.condensed_routines`` — list of lightweight dicts with
          ``id``, ``name``, ``comment``, and ``device_names``.  Used by the
          ``routine_filter`` handler as the LLM prompt payload.

        Silently ignores exceptions so a partial failure does not block
        startup.
        """
        try:
            all_routines = await self.get_all_routines()

            # now go thorugh the list and create both the full and condensed versions of the routines database 
            # codensed version is used for filtering using device names, while the full version is sent to intent handlers for full processing
            for r in all_routines:
                routine = r.get("routine", {})
                routine_id = routine.get("id", "")
                if not routine_id:
                    continue
                condensed_routine = {
                    "id": routine_id, 
                    "name": routine.get("name"),
                    "comment": routine.get("comment"),
                    "device_names": self._get_device_name_list_from_routine(routine) 
                }

                if "invalid" in r:
                    routine["invalid"]=r.get("invalid", False)
                    routine["invalid_reason"]=r.get("error", "")
                    condensed_routine["invalid"]=r.get("invalid", False)
                    condensed_routine["invalid_reason"]=r.get("error", "")
                self.all_routines[routine_id] = routine
                self.condensed_routines.append(condensed_routine)

            self.routines_changed = False
        except Exception as ex:
            pass

    def _get_device_name_list_from_routine(self, routine: dict) -> list[str]:
        """Extract the human-readable device names referenced by a routine.

        Scans the ``if``, ``then``, and ``else`` sections of the routine for
        ``"device"`` fields, resolves each raw address to its display name via
        :meth:`get_device_name`, and returns the deduplicated list.

        Args:
            routine: Full routine dict with optional ``if``/``then``/``else``
                     section lists.

        Returns:
            List of device display name strings (may be empty).
        """
        if routine is None:
            return []

        #first check the if section:        
        if_section: list[dict] = routine.get("if", [])
        then_section: list[dict] = routine.get("then", [])
        else_section: list[dict] = routine.get("else", [])
        device_id_list = []
        for condition in if_section:
            if "device" in condition:
                device = condition.get("device", None)
                if device:                    
                    device_id_list.append(device)
        
        for action in then_section:
            if "device" in action:
                device = action.get("device", None)
                if device:
                    device_id_list.append(device)
   
        for action in else_section:
            if "device" in action:
                device = action.get("device", None)
                if device:
                    device_id_list.append(device)

        device_names: list[str] = []
        for device_id in device_id_list:
            try:
                device_name = self.get_device_name(device_id)
                if device_name:
                    device_names.append(device_name)
            except Exception as ex:
                pass

        return device_names

    
