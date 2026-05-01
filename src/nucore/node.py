"""IoX device node dataclass and XML/JSON loading helpers.

Provides :class:`Node`, which extends :class:`NodeBase` with device-specific
fields (``type``, ``pnode``, ``properties``, etc.) and factory methods for
loading from XML or JSON sources.
"""

import json
from textwrap import indent
from dataclasses import dataclass, field
from .nodedef import Property
from .nucore_error import NuCoreError
import xml.etree.ElementTree as ET
from .node_base import NodeBase

@dataclass
class TypeInfo:
    """A key/value type-info entry embedded in a node's ``<typeInfo>`` element.

    Attributes:
        id: Type-info identifier string.
        val: Associated value string.
    """
    id: str
    val: str


@dataclass
class Node (NodeBase):
    """Represents a single IoX device node.

    Extends :class:`NodeBase` with device-specific fields parsed from the
    IoX XML API.  The ``node_def`` attribute is resolved and attached after
    :meth:`Profile.map_nodes` is called.

    Attributes:
        type: ISY/IoX device type string.
        deviceClass: Device class integer.
        wattage: Rated wattage.
        dcPeriod: DC period value.
        startDelay: Start-delay in seconds.
        endDelay: End-delay in seconds.
        pnode: Primary node address (if this is a non-primary node).
        rpnode: Real primary node address.
        sgid: Scene/group ID this node belongs to.
        typeInfo: List of :class:`TypeInfo` entries.
        properties: Live property values keyed by property ID.
        custom: Raw custom attributes dict.
        devtype: Raw devtype attributes dict.
    """
    type: str = field(default=None)
    deviceClass: int = field(default=0)
    wattage: int = field(default=0)
    dcPeriod: int = field(default=0)
    startDelay: int = field(default=0)
    endDelay: int = field(default=0)
    pnode: str = field(default=None)
    rpnode: str = field(default=None)
    sgid: int = field(default=None)
    typeInfo: list[TypeInfo] = field(default_factory=list)
    properties: dict[str, Property] = field(default_factory=dict) 
    custom: dict = field(default=None)
    devtype: dict = field(default=None)

    def __init__(self, node_elem: ET.Element) -> None:
        """Initialise a ``Node`` from an IoX XML element.

        Args:
            node_elem: The ``<node>`` XML element returned by the IoX API.
        """
        super().__init__(node_elem)
        self.type=node_elem.find("./type").text if node_elem.find("./type") is not None else None
        self.deviceClass=int(node_elem.find("./deviceClass").text) if node_elem.find("./deviceClass") is not None else None
        self.wattage=int(node_elem.find("./wattage").text) if node_elem.find("./wattage") is not None else None
        self.dcPeriod=int(node_elem.find("./dcPeriod").text) if node_elem.find("./dcPeriod") is not None else None
        self.startDelay=int(node_elem.find("./startDelay").text) if node_elem.find("./startDelay") is not None else None
        self.endDelay=int(node_elem.find("./endDelay").text) if node_elem.find("./endDelay") is not None else None
        self.pnode=node_elem.find("./pnode").text if node_elem.find("./pnode") is not None else None
        self.rpnode=node_elem.find("./rpnode").text if node_elem.find("./rpnode") is not None else None
        self.sgid=int(node_elem.find("./sgid").text) if node_elem.find("./sgid") is not None else None

        property_elems = node_elem.findall("./property")
        self.properties = {}
        for p_elem in property_elems:
            prop = Property(
                id=p_elem.get("id"),
                value=p_elem.get("value"),
                formatted=p_elem.get("formatted"),
                uom=p_elem.get("uom"),
                prec=int(p_elem.get("prec")) if p_elem.get("prec") else None,
                name=p_elem.get("name"),
            )
            self.properties[prop.id] = prop 

        typeinfo_elems = node_elem.findall("./typeInfo/t")
        self.typeInfo = [ TypeInfo(t.get("id"), t.get("val")) for t in typeinfo_elems ]
        self.custom=node_elem.find("./custom").attrib if node_elem.find("./custom") is not None else None
        self.devtype=node_elem.find("./devtype").attrib if node_elem.find("./devtype") is not None else None

    def __str__(self) -> str:
        """Return a human-readable summary of this node and its definition."""
        return "\n".join(
            (
                f"Node: {self.name} [{self.address}]",
                indent(str(self.node_def), "  "),
            )
        )

    def json(self, parent) -> dict:
        """Serialise this node to a dict suitable for LLM prompt injection.

        Args:
            parent: The parent ``NodeBase`` instance, or ``None`` if there is
                no parent.

        Returns:
            A dictionary with ``name``, ``address``, parent info, and resolved
            properties / commands / links from the attached ``node_def``.
        """
        #pnode = node.pnode
        #pnode = None if pnode is None or pnode == node.address else pnode 
        #if pnode:
        #    pnode = nodes.get(pnode, None)
        return {
            "name": self.name,
            "address": self.address,
            "parent.name": parent.name if parent else None,
            "parent.address": parent.address if parent else None,
            "properties":[p.json() for p in self.node_def.properties] if self.node_def and self.node_def.properties else [],
            "accepts.commands":[c.json() for c in self.node_def.cmds.accepts] if self.node_def and self.node_def.cmds.accepts else [],
            "sends.commands":[c.json() for c in self.node_def.cmds.sends] if self.node_def and self.node_def.cmds.sends else [],
            "controller.links": [link.json() for link in self.node_def.links.ctl] if self.node_def.links and self.node_def.links.ctl else [],
            "responder.links": [link.json() for link in self.node_def.links.rsp] if self.node_def.links and self.node_def.links.rsp else [],
        }

    @staticmethod
    def load_from_file(nodes_path: str):
        """Load nodes from the specified XML file path.

        Args:
            nodes_path: Path to the XML file containing nodes.

        Returns:
            Parsed XML root element.

        Raises:
            NuCoreError: If *nodes_path* is not set or the file cannot be
                parsed.
        """
        if not nodes_path:
            raise NuCoreError("Nodes path is not set.")
        return ET.parse(nodes_path).getroot()

    @staticmethod
    def load_from_xml(xml):
        """Load nodes from an XML string.

        Args:
            xml: XML string containing nodes.

        Returns:
            Parsed XML root element.

        Raises:
            NuCoreError: If *xml* is ``None`` or cannot be parsed.
        """
        if xml is None:
            raise NuCoreError("xml is mandatory.")
        try:
            return ET.fromstring(xml) 
        except ET.ParseError as e:
            raise NuCoreError(f"Failed to parse XML: {e}")

    @staticmethod
    def load_from_json(json_str):
        """Load nodes from a JSON representation.

        Args:
            json_str: JSON string or dict containing nodes.

        Returns:
            Parsed JSON object (dict).

        Raises:
            NuCoreError: If *json_str* is ``None`` or cannot be parsed.
        """
        if json_str is None:
            raise NuCoreError("json_str is mandatory.")
        if isinstance(json_str, dict):
            return json_str
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            raise NuCoreError(f"Failed to parse JSON: {e}")
    
    def __hash__(self) -> int:
        """Hash by unique node address."""
        return hash(self.address)  # or another unique identifier