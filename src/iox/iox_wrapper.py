import datetime
import json
import sys
import os
import base64
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo
from urllib.parse import quote

import requests
from requests import api
import websockets
import urllib3
import re 

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from nucore.nucore_interface import NuCoreInterface, PromptFormatTypes
from nucore.nodedef import Property
from nucore.node import Node
from nucore.group import Group
from nucore.uom import PREDEFINED_UOMS, UNKNOWN_UOM, is_enumeration_uom
from nucore.nucore_error import NuCoreError
from rag import ProfileRagFormatter, MinimalRagFormatter
from typing import Literal, Any
from utils import get_logger
from xml.sax.saxutils import escape as xml_escape
from .diagnostics.iox_diagnostics import IoXDiagnostics
from .iox_definitions import IoXSOAPAction, DEVICE_FAMILIES, DEVICE_FAMILY_INSTEON, DEVICE_FAMILY_LEGACY_Z_WAVE, DEVICE_FAMILY_PLUGIN, DEVICE_FAMILY_Z_WAVE, DEVICE_FAMILY_ZIGBEE, DEVICE_FAMILY_MATTER

logger = get_logger(__name__)


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def xml_elem_to_obj(elem):
    if elem is None:
        return None

    children = list(elem)
    text = (elem.text or "").strip()

    # Leaf node
    if not children:
        if elem.attrib:
            out = {"@attrs": dict(elem.attrib)}
            if text:
                out["#text"] = text
            return out if out else None
        return text if text else None

    # Node with children
    out = {}
    if elem.attrib:
        out["@attrs"] = dict(elem.attrib)
    if text:
        out["#text"] = text

    for child in children:
        value = xml_elem_to_obj(child)
        tag = child.tag

        if tag in out:
            if not isinstance(out[tag], list):
                out[tag] = [out[tag]]
            out[tag].append(value)
        else:
            out[tag] = value

    return out

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
        self.diagnostics = IoXDiagnostics(self)

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
    
    def delete(self, path: str, body: str = None, headers: dict = None):
        """Send an authenticated HTTP DELETE request to the ISY hub.

        Args:
            path: API path (with or without a leading ``/``).
            body: Request body string (e.g. JSON-encoded payload).
            headers: HTTP headers dict (e.g. ``{"Content-Type": "application/json"}``).

        Returns:
            :class:`requests.Response`, or ``None`` on connection error.
        """
        try:
            path = path if path.startswith("/") else f"/{path}"
            url=f"{self.base_url}{path}" 
            # Method 1a: Using auth parameter (simplest)
            response = requests.delete(url, auth=(self.username, self.password), data=body, headers=headers,  verify=False)
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

    def post(self, path: str, body: str, headers: dict=None):
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

    def patch(self, path: str, body: str, headers: dict):
        """Send an authenticated HTTP PATCH request to the ISY hub.

        Args:
            path:    API path.
            body:    Request body string.
            headers: HTTP headers dict.

        Returns:
            :class:`requests.Response`, or ``None`` on connection error.
        """
        try:
            url=f"{self.base_url}{path}"
            response = requests.patch(url, auth=(self.username, self.password), data=body, headers=headers,  verify=False)
            if response.status_code != 200:
                logger.error(f"invalid url status code = {response.status_code}")
            return response
        except Exception as ex:
            logger.error(f"failed patch: {ex}")
            return None

    def soap_post(self, path: str, body: str, soap_action: str = None, headers: dict = None):
        """Send an authenticated SOAP POST request to the ISY hub.

        Thin wrapper over :meth:`post` that fills in the SOAP-specific
        headers (``Content-Type: text/xml`` and, when given, ``SOAPAction``)
        so callers only need to supply the XML envelope.

        Args:
            path:        API path (with or without a leading ``/``).
            body:        The SOAP XML envelope string.
            soap_action: Value for the ``SOAPAction`` header, if the target
                         service requires one.
            headers:     Additional headers to merge in; these override the
                         SOAP defaults on key collision (e.g. to pass a
                         non-default charset).

        Returns:
            :class:`requests.Response`, or ``None`` on connection error.
        """
        soap_headers = {"Content-Type": "text/xml; charset=utf-8"}
        if soap_action is not None:
            soap_headers["SOAPAction"] = soap_action
        if headers:
            soap_headers.update(headers)
        return self.post(path, body, soap_headers)

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

    async def _submit_soap_request(self, soap_action: str, inner_body: str) -> str | None:
        """POST *inner_body* wrapped in the SOAP envelope for *soap_action*;
        returns the raw response body text, or ``None`` on a connection error
        or non-200 response (mirrors the Java method returning ``null`` when
        ``resp == null`` or ``!resp.opStat``)."""
        envelope = self._get_soap_envelope(soap_action, inner_body)
        response = self.soap_post("/services", envelope, soap_action=soap_action)
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
        response = self.get("/api/groups/links")
        if response == None or response.status_code != 200:
            return None
        try:
            return response.json()
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing JSON response for group links: {e}")
            return None

    def _group_scene_response(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute and normalize a group-scene backend request response."""
        headers = {"Content-Type": "application/json"}
        payload = json.dumps(body) if body is not None else None

        try:
            if method == "GET":
                response = self.get(path)
            elif method == "POST":
                response = self.post(path, payload or "{}", headers)
            elif method == "PATCH":
                response = self.patch(path, payload or "{}", headers)
            elif method == "DELETE":
                response = self.delete(path, payload, headers)
            else:
                return {
                    "successful": False,
                    "stage": "execution",
                    "method": method,
                    "path": path,
                    "error": f"unsupported method: {method}",
                }
        except Exception as exc:
            return {
                "successful": False,
                "stage": "execution",
                "method": method,
                "path": path,
                "error": f"request failed: {exc}",
            }

        if response is None:
            return {
                "successful": False,
                "stage": "execution",
                "method": method,
                "path": path,
                "error": "no response",
            }

        out: dict[str, Any] = {
            "successful": 200 <= int(getattr(response, "status_code", 0)) < 300,
            "method": method,
            "path": path,
            "status_code": int(getattr(response, "status_code", 0)),
        }
        try:
            out["data"] = response.json()
        except Exception:
            out["data"] = getattr(response, "text", "")
        return out

    def group_scene_add_member(
        self,
        group_address: str,
        link_address: str,
        is_controller: bool,
        name: str | None = None,
    ) -> dict[str, Any]:
        """Add a member to a group as controller or responder."""
        payload: dict[str, Any] = {
            "nodeAddress": link_address,
            "isController": is_controller,
        }
        if name is not None:
            payload["name"] = name
        return self._group_scene_response(
            method="POST",
            path=f"/api/groups/members/{quote(group_address, safe='')}",
            body=payload,
        )

    def group_scene_remove_member(self, group_address: str, link_address: str) -> dict[str, Any]:
        """Remove a member node from a group."""
        return self._group_scene_response(
            method="DELETE",
            path=f"/api/groups/members/{quote(group_address, safe='')}",
            body={"nodeAddress": link_address},
        )

    def group_scene_update_link(
        self,
        group_address: str,
        controller_address: str,
        link: dict[str, Any],
    ) -> dict[str, Any]:
        """Update link settings for a controller/member pair in a group."""
        payload = {
            "controllerAddress": controller_address,
            "link": link,
        }
        return self._group_scene_response(
            method="PATCH",
            path=f"/api/groups/links/{quote(group_address, safe='')}",
            body=payload,
        )

    def group_scene_get_node_roles(self, node_address: str) -> dict[str, Any] | None:
        """Get node role capabilities for group membership decisions."""
        path = f"/api/groups/nodeRoles/{quote(node_address, safe='')}"
        response = self.get(path)
        if response is None or response.status_code != 200:
            return None
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            logger.error(f"Error parsing JSON response for group node roles: {exc}")
            return None

    def group_scene_get_link_types(self, controller_address: str, responder_address: str) -> dict[str, Any] | None:
        """Get available link types for a controller/responder pair."""
        path = (
            f"/api/groups/linkTypes?controller={quote(controller_address, safe='')}&"
            f"responder={quote(responder_address, safe='')}"
        )
        response = self.get(path)
        if response is None or response.status_code != 200:
            return None
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            logger.error(f"Error parsing JSON response for group link types: {exc}")
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

        #it migght be a group
        node = self.groups.get(device_str, None)  # Return None if device_id not found
        if node:
            return node.address

        for node in self.groups.values():
            if node.name == device_str:
                return node.address
        logger.error(f"Device/Scene not found: {device_str}")
        return None
    
    def _validate_node_name(self, name: str):
            #make sure name does not contain \<>&;?!/ characters since those are not allowed in node names
        if any(c in name for c in ['\\', '<', '>', '&', ';', '?', '!', '/']):
            out = f"Node name ({name}) cannot contain the following characters: \\ < > & ; ? ! /"
            logger.error(out)
            return False, out
        return True, None
    
    def _get_node(self, node_id: str):
        """
        Determine the family of a given node ID.
        if not found, return None and log an error.
        if found, return family and the node
        """
        if not node_id:
            logger.error("Node ID is empty.")
            return None
        return self.nodes.get(node_id, None)  # Return None if node_id not found
    
    def _get_node_type(self, node_id: str):
        """
        Determine whether a given node ID corresponds to a device, group, or folder.
        if not found, return None and log an error.
        if found, return type and the node
        """
        if not node_id:
            out = "Node ID is empty."
            logger.error(out)
            return None, out
        type = "node"
        node = self.nodes.get(node_id, None)  # Return None if node_id not found
        if not node:
            node = self.groups.get(node_id, None)
            if not node:
                node = self.folders.get(node_id, None)
                if not node:
                    out = f"Node not found: {node_id}"
                    logger.error(out)
                    return None, out
                type = "folder" 
            else:
                type = "group"
        return type, node
    
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
    
    async def create_automation_routine(self, trigger: dict):
        """Submit a new ``Trigger`` (see the new trigger/action schema) to IoX.

        Unlike the old shape this replaces, the caller (the ``routine_compiler``
        package) is responsible for producing an already-fully-resolved
        ``NewTrigger`` dict -- real device/group/routine ids (the unified
        path's device ids are already real IoX addresses, never display
        names, so no ``get_device_id`` name-resolution step is needed here
        the way the old shape required) and already uom/precision-scaled
        values (the compiler has real uom/precision in hand via
        ``get_device_detail``, the same way ``resolve_value``/``send_command``
        already do deterministic scaling elsewhere in this codebase). This
        method's job shrinks to wire submission only -- no per-condition/
        per-action translation, since there's no longer a bespoke
        intermediate shape to translate *from*.

        Args:
            trigger: A ``NewTrigger``-shaped dict (no ``id``; ``parent``
                     optional).

        Returns:
            :class:`requests.Response` from :meth:`_create_routine`, or
            ``None`` on failure.

        Raises:
            :class:`~nucore.nucore_error.NuCoreError`: When *trigger* is
                empty or ``None``.
        """
        if not trigger:
            raise NuCoreError("No valid trigger provided.")
        return self._create_routine(trigger)

    def _create_routine(self, trigger: dict):
        """Submit a ``NewTrigger``-shaped dict to the IoX hub via PUT.

        Args:
            trigger: ``NewTrigger``-shaped dict (see ``create_automation_routine``).

        Returns:
            :class:`requests.Response`, or ``None`` on failure.
        """
        response = None
        try:
            # Confirmed: unlike the old /api/ai/trigger endpoint, the new
            # /api/trigger endpoint takes the NewTrigger/UpdatedTrigger dict
            # directly as the request body -- no {"routine": ...} wrapper.
            body = trigger
            headers = {
                "Content-Type": "application/json"
            }
            response = self.put(f'/api/trigger', body=json.dumps(body), headers=headers)
        except Exception as ex:
            logger.error(f"Error creating trigger: {ex}")

        return response

    async def get_all_routines_summary(self):
        """Fetch lightweight runtime-state summaries for all routines/folders.

        Returns the ``data`` array from ``/api/programs``. Each entry carries
        live runtime state -- ``folder`` (program vs. folder; a folder can
        carry its own gating condition), ``status`` (its if-condition's
        current evaluation), ``lastRunTime``/``lastFinishTime``,
        ``enabled``, ``runAtStartup``, ``running``, and
        ``nextScheduledRunTime`` -- but not the if/then/else trigger/action
        logic itself (see :meth:`get_all_routines`/:meth:`get_routine` for
        that). ``_load_routines`` merges this onto the condensed routines
        list by id.

        Returns:
            List of runtime summary dicts, or ``None`` on failure.
        """
        response = self.get("/api/programs")
        if response is None or response.status_code != 200:
            return None
        try:
            return response.json()['data']
        except Exception as ex:
            logger.error(f"Error retrieving routines summary: {ex}")
            return None

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

        Returns the ``data`` array from ``/api/triggers`` -- a list of
        ``Trigger``/``InvalidTrigger``-shaped dicts, one per routine, with the
        full ``if``/``then``/``else`` logic for every routine.

        Returns:
            List of full routine dicts, or the raw response / ``None`` on
            failure.
        """
        response = self.get("/api/triggers")
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
                        ``/api/triggers/<id>`` endpoint).

        Returns:
            ``data`` value from the API response (a ``Trigger``/
            ``InvalidTrigger``-shaped dict), or ``None`` on failure.
        """
        # NOTE: self.get is synchronous (line ~143) -- the `await` here was
        # a pre-existing bug (present before this endpoint migration) that
        # would have made this method crash unconditionally; fixed in
        # passing since this exact line was already being touched.
        response = self.get(f"/api/triggers/{program_id}")
        if response == None or response.status_code != 200:
            return response if response else None
        try:
            return response.json()['data']
        except Exception as ex:
            logger.error(f"Error retrieving routine: {ex}")
            return None

    async def update_routine(self, program: dict):
        """Update an existing routine (``UpdatedTrigger``-shaped -- has a
        real ``id``, real resolved ids/values, same as ``create_automation_routine``)
        on the hub via POST.

        Args:
            program: ``UpdatedTrigger``-shaped dict.

        Returns:
            :class:`requests.Response`, or ``False`` when ``program`` is empty.
        """
        if not program:
            return False
        response=None
        try:
            # Same confirmed no-wrapper contract as _create_routine.
            program_content = program
            headers = {
                "Content-Type": "application/json"
            }
            response = self.post(f'/api/trigger', body=json.dumps(program_content), headers=headers)
        except Exception as ex:
            logger.error(f"Error updating routine: {ex}")

        return response

    async def delete_routine(self, program_id: str):
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
            # self.delete is synchronous -- same pre-existing await-on-sync
            # bug as get_routine, fixed in passing.
            response = self.delete(f'/api/trigger/{program_id}')
        except Exception as ex:
            logger.error(f"Error deleting routine: {ex}")

        return response
    
    async def add_node(self, node_name:str, type:Literal["folder", "group"]):
        """
        Add a new node (folder or group) to the device structure. Distinct
        from start_device_pairing -- this creates a software organizational
        node via the REST API; it has nothing to do with physical devices,
        despite "AddNode" being the (unrelated) name of the SOAP action
        start_device_pairing uses.
        :param node_name: The name of the node to add.
        :param type: The type of the node, either "folder" or "group".
        :return: response from the API or None if failure
        """
        type=type.lower()
        if not type in ["folder", "group"]:
            out = f"Invalid node type: {type}. Must be 'folder' or 'group'."
            logger.error(out)
            return out

        if not node_name:
            out = "Node name cannot be empty."
            logger.error(out)
            return out  

        response = None
        #make sure name does not contain \<>&;?!/ characters since those are not allowed in node names
        rc, out = self._validate_node_name(node_name)
        if not rc:
            return out
        try:
            body = {
              'name': node_name, 
              'nodeType': type, 
            }
            headers = {
                "Content-Type": "application/json"
            }
            response = self.post(f'/api/nodes', body=json.dumps(body), headers=headers)
        except Exception as ex:
            out = f"Error adding node: {ex}"
            logger.error(out)
            response = out
        
        return response 

    async def node_ops(self, node_id:str, operation:Literal["delete", "enable", "disable", "rename", "move", "group"], **kwargs):
        """
        Perform an operation on a node (folder or group).
        :param node_id: The ID of the node to operate on.
        :param operation: The operation to perform (e.g., "delete", "enable", "disable", "rename", "move").
        :param kwargs: Additional parameters for the operation:
          new_name for rename
          new_parent_id for move
        :return: response from the API or None if failure 
        """
        if not node_id:
            out = "Node ID cannot be empty."
            logger.error(out)
            return out
        operation = operation.lower()
        if operation not in ["delete", "enable", "disable", "rename", "move", "group"]:
            out = f"Invalid operation: {operation}. Must be one of 'delete', 'enable', 'disable', 'rename', 'move', 'group'."
            logger.error(out)
            return out
        
        if operation == "enable" or operation == "disable":
            try:
                body = { 'enabled': operation == "enable" }
                headers = {
                    "Content-Type": "application/json"
                }
                response = self.patch(f'/api/nodes/{node_id}/', body=json.dumps(body), headers=headers)
            except Exception as ex:
                out = f"Error performing node {operation} operation: {ex}"
                logger.error(out)
                response = out
            return response  

        type, out = self._get_node_type(node_id)
        if not type:
            return out
        node = out 
        if operation == "delete":
            try:
                body = { 'nodeType': type }
                if type == "node":
                    body['family'] = node.family if node.family else "n/a"
                headers = {
                    "Content-Type": "application/json"
                }
                response = self.delete(f'/api/nodes/{node_id}/', body=json.dumps(body), headers=headers)
            except Exception as ex:
                out = f"Error performing delete node operation: {ex}"
                logger.error(out)
                response = out
            return response

        if operation == "rename":
            new_name = kwargs.get("new_name", None)
            if not new_name:
                out = "New name must be provided for rename operation."
                logger.error(out)
                return out
            #make sure name does not contain \<>&;?!/ characters since those are not allowed in node names
            rc, out = self._validate_node_name(new_name)
            if not rc:
                return out
            
            try:
                body = { 'name': new_name, 'nodeType': type} 
                headers = {
                    "Content-Type": "application/json"
                }
                response = self.patch(f'/api/nodes/{node_id}/', body=json.dumps(body), headers=headers)
            except Exception as ex:
                out = f"Error performing rename node operation: {ex}"
                logger.error(out)
                response = out
            return response      

        if operation == "move":
            new_parent_id = kwargs.get("new_parent_id", None)
            if not new_parent_id:
                out = "New parent ID must be provided for move operation."
                logger.error(out)
                return out

            parent_type, out = self._get_node_type(new_parent_id)
            if not parent_type:
                return out

            try:
                body = { 'nodeType': type, 'parentAddress': new_parent_id, 'parentNodeType': parent_type } 
                headers = {
                    "Content-Type": "application/json"
                }
                response = self.patch(f'/api/nodes/{node_id}/', body=json.dumps(body), headers=headers)
            except Exception as ex:
                out = f"Error performing move node operation: {ex}"
                logger.error(out)
                response = out
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
        response = None
        try:
            if operation == "delete":
                # The one routine_ops operation that lives on the trigger-
                # content endpoint rather than /rest/programs -- moves with
                # the rest of the CRUD migration, unlike every other
                # operation below (confirmed unchanged).
                response = self.delete(f'/api/trigger/{routine_id}')
            else:
                if isinstance(routine_id, str):
                    try:
                        routine_id = int(routine_id)
                        #convert it to 4 digit hex string without 0x prefix since that's what the API expects
                    except ValueError:
                        #already in hex
                        pass
                routine_id = format(routine_id, '04x')
                response = self.get(f'/rest/programs/{routine_id}/{operation}')
        except Exception as ex:
            logger.error(f"Error performing routine operation: {ex}")

        return response

    async def variable_ops(
        self,
        var_type: int,
        var_id: str | None,
        operation: Literal["create", "update", "delete"],
        **kwargs,
    ):
        """Create, update, or delete a NuCore variable.

        Args:
            var_type: 1 (integer variable) or 2 (state variable).
            var_id: Required for "update"/"delete", ignored for "create".
            operation: "create", "update", or "delete".
            kwargs: For "create"/"update": name, prec, value, init (all optional).

        Returns:
            :class:`requests.Response`, or ``None`` when the operation is
            unrecognised or a required id is missing.
        """
        if operation not in ("create", "update", "delete"):
            logger.error(f"Invalid variable operation: {operation}")
            return None
        if operation != "create" and not var_id:
            logger.error("var_id is required for update/delete variable operations")
            return None

        response = None
        headers = {"Content-Type": "application/json"}
        try:
            if operation == "create":
                body = {k: kwargs[k] for k in ("name", "prec") if k in kwargs}
                response = self.put(f'/api/variables/{var_type}', body=json.dumps(body), headers=headers)
            elif operation == "update":
                body = {k: kwargs[k] for k in ("value", "init", "prec", "name") if k in kwargs}
                response = self.post(f'/api/variables/{var_type}/{var_id}', body=json.dumps(body), headers=headers)
            else:  # delete
                response = self.delete(f'/api/variables/{var_type}/{var_id}')
        except Exception as ex:
            logger.error(f"Error performing variable operation: {ex}")

        return response

    # ------------------------------------------------------------------
    # Timezone management
    # ------------------------------------------------------------------
    async def get_timespecs(self) -> dict[str, str]:
        """
        Get time/timezone/location information from the device, including the
        current time and today's sunrise/sunset (both localized to the
        device's own configured timezone) -- useful for the LLM when setting
        up or explaining time- or sunrise/sunset-based routines.
        :return: Dictionary of timespecs or None if failure

        API:
        /rest/time
        """
        try:
            response = self.get(f'/rest/time')
            if response == None or response.status_code != 200:
                return response if response else None

            #response is in xml, convert to json
            root = ET.fromstring(response.text)
#                <DT>
#                    <NTP>3989354564</NTP>
#                    <GMT>1780390964</GMT>
#                    <TMZOffset>-8</TMZOffset>
#                    <DST>true</DST>
#                    <DSTRule>NAM</DSTRule>
#                    <Lat>34.050000</Lat>
#                    <Long>118.233000</Long>
#                    <Sunrise>3989022221</Sunrise>
#                    <SunriseGMT>1780058621</SunriseGMT>
#                    <Sunset>3989073402</Sunset>
#                    <SunsetGMT>1780109802</SunsetGMT>
#                    <IsMilitary>false</IsMilitary>
#                    <TzId>America/Los_Angeles</TzId>
#                </DT>
            #convert to dictionary -- read every field first since TzId (needed
            #to localize the GMT/SunriseGMT/SunsetGMT epoch fields below) comes
            #last in the XML, after the fields that depend on it
            raw = {child.tag: child.text for child in root}

            tz_name = raw.get("TzId")
            tzinfo = ZoneInfo(tz_name) if tz_name else datetime.timezone.utc

            time_data: dict[str, Any] = {"timezone": tz_name}

            if raw.get("Lat") is not None:
                time_data["latitude"] = float(raw["Lat"])
            if raw.get("Long") is not None:
                time_data["longitude"] = float(raw["Long"]) * -1 #the API returns longitude as a positive value, convert it to negative since that's the standard format for longitude

            #GMT/SunriseGMT/SunsetGMT are already Unix epoch seconds (unlike
            #NTP/Sunrise/Sunset, which are NTP epoch -- seconds since 1900, not
            #1970) -- so no epoch conversion is needed, just localization
            for key, field in (("current_time", "GMT"), ("sunrise", "SunriseGMT"), ("sunset", "SunsetGMT")):
                value = raw.get(field)
                if value is not None:
                    time_data[key] = (
                        datetime.datetime.fromtimestamp(int(value), tz=datetime.timezone.utc)
                        .astimezone(tzinfo)
                        .isoformat()
                    )

            return time_data
        except Exception as ex:
            logger.error(f"Error performing timespecs operation: {ex}")

    # ------------------------------------------------------------------
    # Plugin management  
    # ------------------------------------------------------------------
    
    async def get_active_plugins(self) -> dict[str, str]:
        """
        Get a list of active plugins that can be installed on the device. 
        :return: Dictionary of active plugins or None if failure

        API:
        /api/plugins/store/list/active
        """
        try:
            response = self.get(f'/api/plugins/store/list/active')
            if response == None or response.status_code != 200:
                return response if response else None
            return response.json()
        except Exception as ex:
            logger.error(f"Error performing get active plugins operation: {ex}")
            return None

    async def get_purchased_plugins(self) -> dict[str, str]:
        """
        Get the licenses this installation has purchased for plugins.
        :return: Dictionary of purchased plugin licenses or None if failure

        API:
        /api/plugins/licenses
        """
        try:
            response = self.get(f'/api/plugins/licenses')
            if response == None or response.status_code != 200:
                return response if response else None
            return response.json()
        except Exception as ex:
            logger.error(f"Error performing get purchased plugins operation: {ex}")
            return None

    async def get_installed_plugins(self) -> dict[str, str]:
        """
        Get a list of plugins installed on this device.
        :return: Dictionary of installed plugins or None if failure

        API:
        /api/plugins

        Response shape:
        {"successful": true, "data": [{"profileNum": 3, "name": "YouTube", "isLocal": false}, ...]}

        ``profileNum`` is this plugin's id -- used as ``plugin_id`` for
        subsequent plugin_ops()/configure_plugin() calls.
        """
        try:
            response = self.get(f'/api/plugins')
            if response == None or response.status_code != 200:
                return response if response else None
            # now go through the list and rename profileNum to plugin_id for consistency with other plugin APIs
            return response.json()
        except Exception as ex:
            logger.error(f"Error performing get installed plugins operation: {ex}")
            return None
    
    async def plugin_ops(self, plugin_id:str, operation:Literal["details", "install", "uninstall", "status", "start", "stop", "restart", "purchase"]):
        """
        Perform an operation on a plugin.
        :param plugin_id: The ID of the plugin to operate on -- profileNum
                           from get_installed_plugins() for start/stop/restart
                           (and, once implemented, install/uninstall/status);
                           nsid for details/purchase.
        :param operation: The operation to perform.
        :return: response from the API or None if failure

        Details API:
        /api/plugins/store/entry/:nsid

        Start/Stop/Restart API:
        /api/plugins/<profileNum>/start
        /api/plugins/<profileNum>/stop
        /api/plugins/<profileNum>/restart

        Install/Purchase: no real API exists yet -- stubbed with a simulated
        success so callers can be built/tested end-to-end.
        """
        if operation in ("start", "stop", "restart"):
            try:
                headers = {"Content-Type": "application/json"}
                response = self.post(f'/api/plugins/{plugin_id}/{operation}', body="{}", headers=headers)
                if response == None or response.status_code != 200:
                    return response if response else None
                return response.json()
            except Exception as ex:
                logger.error(f"Error performing plugin {operation} operation: {ex}")
                return None

        if operation in ("install", "purchase"):
            # STUB: no real install/purchase API exists yet -- simulate
            # success so the calling flow can be built/tested end-to-end.
            # Replace with a real HTTP call once NuCore ships one.
            return {
                "successful": True,
                "data": {"plugin_id": plugin_id, "operation": operation, "stub": True},
            }

        raise NotImplementedError(f"plugin_ops operation '{operation}' is not yet implemented.")

    async def configure_plugin(self, plugin_id:str, config:dict[str, Any]):
        """
        Configure a plugin on the device.
        :param plugin_id: The ID of the plugin to configure.
        :param config: A dictionary containing the configuration parameters.
        :return: response from the API or None if failure
        """
        raise NotImplementedError("Subclasses must implement the configure_plugin method.")

    async def get_plugin_prompt(self, plugin_id: str) -> dict:
        """
        Returns usage guidance deterministic from plugin_id alone. 
        """
        try:
            response = self.get(f'/api/plugin/{plugin_id}/prompt')
            if response == None or response.status_code != 200:
                return {
                    "successful": False,
                    "data": f"No response" if response is None else f"HTTP {response.status_code} from /api/plugin/{plugin_id}/prompt",
                }
            out = response.json()
            if not isinstance(out, dict) or "successful" not in out or "data" not in out:
                return {
                    "successful": False,
                    "data": "Invalid response format",
                }
            return out
        except Exception as ex:
                return {
                    "successful": False,
                    "data": str(ex),
                }

    async def get_plugin_tools(self, plugin_id: str) -> dict:
        """
        Returns tool-spec list deterministic from plugin_id alone, uniquified with
        the plugin's own id per the naming convention used for multiple
        installed instances of the same plugin type. 
        """
        try:
            response = self.get(f'/api/plugin/{plugin_id}/tools')
            if response == None or response.status_code != 200:
                return {
                    "successful": False,
                    "data": f"No response" if response is None else f"HTTP {response.status_code} from /api/plugin/{plugin_id}/tools",
                }
            out = response.json()
            if not isinstance(out, dict) or "successful" not in out or "data" not in out:
                return {
                    "successful": False,
                    "data": "Invalid response format",
                }
            tools = out.get("data", [])
            if not isinstance(tools, list):
                return {
                    "successful": False,
                    "data": "Invalid tools format",
                }
            # rename the tools to make them unique per plugin_id, since multiple instances of the same plugin type can be installed
            for tool in tools:
                if "name" in tool:
                    tool["name"] = f"{plugin_id}_{tool['name']}"
            return {
                "successful": True,
                "data": {
                    "tools": tools,
                },
            }

        except Exception as ex:
                return {
                    "successful": False,
                    "data": str(ex),
                }

    async def handle_plugin_llm_result(self, plugin_id: str, args: dict[str, Any]) -> dict:
        """
        Returns the result of the plugin's execution of the tool specified in *args["tool_name"]*
        with *args*, deterministic from plugin_id/tool_name. 
        """
        timeout=60000

        try:
            response = self.post(f'/api/plugin/{plugin_id}/request',
                                 body=json.dumps({"timeout": timeout, "payload": args}), headers={"Content-Type": "application/json"})
            if response == None or response.status_code != 200:
                return {
                    "successful": False,
                    "data": f"No response" if response is None else f"HTTP {response.status_code} from /api/plugin/{plugin_id}/request",
                }
            out = response.json()
            if not isinstance(out, dict) or "successful" not in out or "data" not in out:
                return {
                    "successful": False,
                    "data": "Invalid response format",
                }
            return out
        except Exception as ex:
            return {
                "successful": False,
                "data": str(ex),
            }

    # ------------------------------------------------------------------
    # Diagnostics -- all actual registry/state/dispatch logic lives on
    # IoXDiagnostics (self.diagnostics); these just satisfy NuCoreInterface.
    # ------------------------------------------------------------------

    async def start_diagnostics(self, **kwargs) -> Any:
        """
        Open (or re-show) the diagnostic session -- see
        IoXDiagnostics.start_diagnostics.
        """
        return await self.diagnostics.start_diagnostics(**kwargs)

    async def run_diagnostic_step(self, step: str, **params) -> Any:
        """
        Run one step of the in-progress diagnostic session -- see
        IoXDiagnostics.run_diagnostic_step.
        """
        return await self.diagnostics.run_diagnostic_step(step, **params)

    def get_running_diagnostic(self) -> dict[str, Any] | None:
        """
        Return info about the diagnostic currently in flight, if any -- see
        IoXDiagnostics.get_running_diagnostic.
        """
        return self.diagnostics.get_running_diagnostic()

    # ------------------------------------------------------------------
    # Device pairing -- previously dead diagnostics-only SOAP calls (never
    # wired into a diagnostic step), moved here rather than wiring up the
    # untested, zero-call-site SetDeviceLinkMode action. Distinct from
    # add_node above -- these drive the PLM's physical pairing/linking
    # hardware workflow, not NuCore's own node database. INSTEON only for
    # now -- no Z-Wave/Zigbee/Matter pairing primitives exist anywhere in
    # this class yet.
    # ------------------------------------------------------------------

    async def add_device(self, device_address: str, **kwargs) -> Any:
        return await self._send_device_specific_with_option(
            IoXSOAPAction.SOAP_TYPE_ADD_NODE, device_address, None, 0x01, None
        )

    async def discover_devices(self) -> Any:
        return await self._send_device_specific_with_option(IoXSOAPAction.SOAP_TYPE_DISCOVER_NODES, None, None, 0x01, None)

    async def finish_device_discovery(self) -> Any:
        return await self._send_device_specific_with_option(IoXSOAPAction.SOAP_TYPE_CANCEL_NODES_DISCOVERY, None, None, 0x01, None)

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
            def sanitize_bare_ampersands(xml_text: str) -> str:
                # Replace & not starting a valid XML entity.
                return re.sub(r'&(?!amp;|lt;|gt;|quot;|apos;|#[0-9]+;|#x[0-9A-Fa-f]+;)', '&amp;', xml_text)

            async with websockets.connect(ws_url, ssl=ssl_context, additional_headers=headers) as websocket:
                if on_connect_callback:
                    await on_connect_callback()
                try:
                    async for message in websocket:

                        if on_message_callback:
                            try:
                                message = sanitize_bare_ampersands(message)
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
                                    # parsed structure (works for text OR nested tags)
                                    'eventInfo': xml_elem_to_obj(eventInfo),
                                }
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
        
        logger.debug(f"Unknown formatter type: {self.formatter_type}, defaulting to per-device format.")
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
    
    # Runtime-state fields /api/programs carries that /api/triggers does not
    # -- see get_all_routines_summary(). Copied verbatim (no renaming/
    # reshaping) onto each condensed routine when present.
    _RUNTIME_SUMMARY_FIELDS = (
        "folder", "status", "lastRunTime", "lastFinishTime",
        "enabled", "runAtStartup", "running", "nextScheduledRunTime",
    )

    def _merge_routine_runtime_summary(self, condensed_routine: dict, summary: dict | None) -> None:
        if not summary:
            return
        for field in self._RUNTIME_SUMMARY_FIELDS:
            if field in summary:
                condensed_routine[field] = summary[field]

    async def _load_variables(self) -> None:
        """Fetch all integer (type 1) and state (type 2) variables from the
        hub and populate ``self.variables``/``self.condensed_variables``.

        ``self.variables`` is keyed ``"<type>:<id>"`` (see
        ``NuCoreInterface.get_variable``) so the two id spaces never
        collide. Rebuilt from scratch each call (unlike ``_load_routines``,
        which appends -- there's no existing accumulate-across-refreshes
        behavior to preserve here since this store is brand new).

        Called from ``_refresh_routines_database`` before ``_load_routines``
        so routines' ``variable_names`` cross-reference has fresh data to
        resolve against. Silently ignores exceptions so a partial failure
        does not block startup -- same convention as ``_load_routines``.
        """
        try:
            variables: dict[str, Any] = {}
            condensed: list = []
            for var_type in (1, 2):
                response = self.get(f'/api/variables/{var_type}')
                if response is None or response.status_code != 200:
                    continue
                try:
                    records = response.json().get('data') or []
                except Exception as ex:
                    logger.error(f"Error parsing variables (type {var_type}): {ex}")
                    continue
                for record in records:
                    var_id = record.get("id")
                    if var_id is None:
                        continue
                    entry = {**record, "type": var_type}
                    variables[f"{var_type}:{var_id}"] = entry
                    condensed.append(entry)
            self.variables = variables
            self.condensed_variables = condensed
        except Exception as ex:
            logger.error(f"Error loading variables: {ex}")

    async def _load_routines(self) -> None:
        """Fetch all routines from the hub and populate the in-memory stores.

        Builds two parallel stores:

        * ``self.all_routines`` — maps ``routine_id → full routine dict``
          (including trigger/action logic).  Used by the
          ``routine_status_ops`` handler for direct access.
        * ``self.condensed_routines`` — list of lightweight dicts with
          ``id``, ``name``, ``comment``, ``device_names``, and the runtime
          fields listed in ``_RUNTIME_SUMMARY_FIELDS`` (when known).  Used by
          the ``routine_filter`` handler as the LLM prompt payload.

        Runtime state comes from ``get_all_routines_summary()``
        (``/api/programs``), merged onto each entry by id -- fetched
        best-effort: a failure there still leaves the trigger-sourced fields
        populated, it just means the runtime fields are missing. Folders
        (and anything else that ``/api/programs`` knows about but
        ``/api/triggers`` doesn't -- folders carry a gating condition but no
        if/then/else trigger content) still get a condensed entry, with
        ``device_names``/``comment`` empty since there's no trigger content
        to derive them from.

        Silently ignores exceptions so a partial failure does not block
        startup.
        """
        try:
            all_routines = await self.get_all_routines()

            try:
                summaries = await self.get_all_routines_summary() or []
            except Exception as ex:
                logger.error(f"Error retrieving routines runtime summary: {ex}")
                summaries = []
            summary_by_id = {str(s["id"]): s for s in summaries if s.get("id") is not None}

            # New schema: each list item IS the Trigger/InvalidTrigger dict
            # directly -- no more {"routine": {...}, "invalid"?, "error"?}
            # wrapper (that was the old shape's convention; the new
            # Trigger/InvalidTrigger types carry id/name/if/then/else and,
            # for InvalidTrigger, invalid/error/xml all at their own top
            # level, per the schema).
            seen_ids: set[str] = set()
            for routine in all_routines:
                routine_id = routine.get("id", "")
                if not routine_id:
                    continue
                seen_ids.add(str(routine_id))
                condensed_routine = {
                    "id": routine_id,
                    "name": routine.get("name"),
                    "comment": routine.get("comment"),
                    "device_names": self._get_device_name_list_from_routine(routine),
                    "variable_names": self._get_variable_name_list_from_routine(routine),
                }

                if routine.get("invalid"):
                    condensed_routine["invalid"] = True
                    condensed_routine["invalid_reason"] = routine.get("error", "")
                self._merge_routine_runtime_summary(condensed_routine, summary_by_id.get(str(routine_id)))
                self.all_routines[routine_id] = routine
                self.condensed_routines.append(condensed_routine)

            for sid, summary in summary_by_id.items():
                if sid in seen_ids:
                    continue
                condensed_routine = {
                    "id": summary.get("id"),
                    "name": summary.get("name"),
                    "comment": "",
                    "device_names": [],
                    "variable_names": [],
                }
                self._merge_routine_runtime_summary(condensed_routine, summary)
                self.condensed_routines.append(condensed_routine)

            self.routines_changed = False
        except Exception as ex:
            pass

    def _get_device_name_list_from_routine(self, routine: dict) -> list[str]:
        """Extract the human-readable device names referenced by a routine.

        Scans the ``if``, ``then``, and ``else`` sections for a device/node
        reference and resolves each to its display name via
        :meth:`get_device_name`, deduplicated.

        Only conditions/actions carrying a device reference at their own top
        level are scanned -- ``status``/``control`` conditions and ``cmd``
        actions, via the new schema's ``"node"`` field (a real rename from
        the old shape's ``"device"`` key, not cosmetic -- the new schema is
        the authoritative wire shape). ``paren`` conditions are recursed into
        since they nest their own ``conditions`` list. Every other construct
        type (``var``, ``x10``, ``triggerref``, ``inet``, ``comment``,
        ``sys``, ``notify``, the program-control actions, ``lp``, ``device``)
        legitimately has no top-level device reference and is silently
        skipped -- this was never meant to be exhaustive, just a best-effort
        "what devices does this routine mention" summary for prompt display.

        Args:
            routine: Full routine dict with optional ``if``/``then``/``else``
                     section lists.

        Returns:
            List of device display name strings (may be empty).
        """
        if routine is None:
            return []

        def _collect_condition_nodes(conditions, into: set) -> None:
            for condition in conditions or []:
                if not isinstance(condition, dict):
                    continue
                if condition.get("type") == "paren":
                    _collect_condition_nodes(condition.get("conditions"), into)
                    continue
                node = condition.get("node")
                if node:
                    into.add(node)

        def _collect_action_nodes(actions, into: set) -> None:
            for action in actions or []:
                if not isinstance(action, dict):
                    continue
                node = action.get("node")
                if node:
                    into.add(node)

        device_id_list: set = set()
        _collect_condition_nodes(routine.get("if", []), device_id_list)
        _collect_action_nodes(routine.get("then", []), device_id_list)
        _collect_action_nodes(routine.get("else", []), device_id_list)

        device_names: list[str] = []
        for device_id in device_id_list:
            try:
                device_name = self.get_device_name(device_id)
                if device_name:
                    device_names.append(device_name)
            except Exception as ex:
                pass

        return list(device_names)

    def _get_variable_name_list_from_routine(self, routine: dict) -> list[str]:
        """Extract the human-readable variable names referenced by a
        routine -- mirrors :meth:`_get_device_name_list_from_routine`.

        A variable can appear as: a ``var`` condition/action's own top-level
        ``id``/``varType``; a nested ``var`` reference (comparing/assigning
        against another variable -- keyed ``id``/``type``, not ``varType``,
        per the compiler's own output shape); or nested inside a ``repeat``
        action's ``while`` sub-object. ``paren`` conditions are recursed
        into. Resolved via ``self.variables`` (populated by
        ``_load_variables``, which always runs first -- see
        ``NuCoreInterface._refresh_routines_database``).

        Returns:
            List of variable display name strings (may be empty).
        """
        if routine is None:
            return []

        refs: set[str] = set()

        def _add_ref(var_type, var_id) -> None:
            if var_type is not None and var_id is not None:
                refs.add(f"{var_type}:{var_id}")

        def _collect_condition_vars(conditions) -> None:
            for condition in conditions or []:
                if not isinstance(condition, dict):
                    continue
                ctype = condition.get("type")
                if ctype == "paren":
                    _collect_condition_vars(condition.get("conditions"))
                    continue
                if ctype != "var":
                    continue
                _add_ref(condition.get("varType"), condition.get("id"))
                nested = condition.get("var")
                if isinstance(nested, dict):
                    _add_ref(nested.get("type"), nested.get("id"))

        def _collect_action_vars(actions) -> None:
            for action in actions or []:
                if not isinstance(action, dict):
                    continue
                atype = action.get("type")
                if atype == "var":
                    _add_ref(action.get("varType"), action.get("id"))
                    nested = action.get("var")
                    if isinstance(nested, dict):
                        _add_ref(nested.get("type"), nested.get("id"))
                elif atype == "repeat":
                    while_block = action.get("while")
                    nested = while_block.get("var") if isinstance(while_block, dict) else None
                    if isinstance(nested, dict):
                        _add_ref(nested.get("varType"), nested.get("id"))

        _collect_condition_vars(routine.get("if", []))
        _collect_action_vars(routine.get("then", []))
        _collect_action_vars(routine.get("else", []))

        variable_names: list[str] = []
        for ref in refs:
            variable = self.variables.get(ref)
            if variable and variable.get("name"):
                variable_names.append(variable["name"])

        return variable_names

    async def _on_device_event(self, message:dict):
        """
        We'll override the superclass to give more information to the diagnostics module.
        Callback function to handle device events.
        What we are looking for are events that change device structure such as device added/removed, property added/removed, etc.
        :param event: The event data received.
        """
        if message is None or 'node' not in message or 'control' not in message:
            logger.debug(f"Received invalid message format {message}")
            return
        control = message['control']
        action = message.get('action', '')
        if action:
            action=action.get('value', None)
        node = message.get('node', None)
        eventInfo = message.get('eventInfo', None)
        if control == "_3": #node updated event
            await self.diagnostics.on_node_updated_event(node, control, action, eventInfo)
            if action and action in [ 'NX', 'PI', 'WD', 'EN', 'WH' ]:
                if action == 'WH':
                    node = self.nodes.get(node, None) # just to be on the safe side
                    if node:
                        node.pending_write = True
                    #we may want to use 'WD' (device write pending) later for diagnostics 
                    return
            self.device_structure_changed = True # just to be on the safe side
        elif control == "_1": #programs updated event
            self.routines_changed = True # just to be on the safe side
        elif control in [ "_21" , "_25", "_27", "_28"]: # zw, zw-zwave, zw-zigbee, zw-matter
            await self.diagnostics.on_device_event(node, control, action, eventInfo)
        elif control == "_2": # variable write pending
            await self.diagnostics.update_links_table(node, control, action, eventInfo)

    def _get_node_family(self, device_id) -> str | None:
        node = self._get_node(device_id)
        if node is None:
            return None, None
        if not hasattr(node, "family"):
            logger.error(f"Node object for device_id {device_id} does not have 'family' attribute")
            return None, None

        family = node.family
        if family is None:
            logger.error(f"Node object for device_id {device_id} has 'family' attribute set to None")
            return None, None

        family_code = str(family).strip()
        try:
            if family_code not in DEVICE_FAMILIES:
                logger.error(f"Node object for device_id {device_id} has unrecognized 'family' value: {family}")
                return None, None
            return family_code, DEVICE_FAMILIES[family_code]
        except KeyError:
            logger.error(f"Node object for device_id {device_id} has unrecognized 'family' value: {family}")
            return None, None

    def _is_insteon_family(self, device_id) -> bool:
        family, _ = self._get_node_family(device_id)
        if not family:
            return False
        return family == DEVICE_FAMILY_INSTEON

    def _is_legacy_z_wave_family(self, device_id) -> bool:
        family, _ = self._get_node_family(device_id)
        if not family:
            return False
        return family == DEVICE_FAMILY_LEGACY_Z_WAVE

    def _is_zway_z_wave_family(self, device_id) -> bool:
        family, _ = self._get_node_family(device_id)
        if not family:
            return False
        return family == DEVICE_FAMILY_Z_WAVE

    def _is_z_wave_family(self, device_id) -> bool:
        family, _ = self._get_node_family(device_id)
        if not family:
            return False
        return family in [DEVICE_FAMILY_LEGACY_Z_WAVE, DEVICE_FAMILY_Z_WAVE]

    def _is_zigbee_family(self, device_id) -> bool:
        family, _ = self._get_node_family(device_id)
        if not family:
            return False
        return family == DEVICE_FAMILY_ZIGBEE 

    def _is_matter_family(self, device_id) -> bool:
        family, _ = self._get_node_family(device_id)
        if not family:
            return False
        return family == DEVICE_FAMILY_MATTER 

    def _is_plug_in_family(self, device_id) -> bool:
        family, _ = self._get_node_family(device_id)
        if not family:
            return False
        return family == DEVICE_FAMILY_PLUGIN

    def _get_group_by_device_group_id(self, device_group_id) -> Group | None:
        if not self.groups:
            return None
        for group in self.groups.values():
            if group.device_group == device_group_id:
                return group
        return None