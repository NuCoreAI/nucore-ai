from textwrap import indent
from dataclasses import dataclass, field
from .nodedef import Property
from .nucore_error import NuCoreError
import xml.etree.ElementTree as ET
from .node_base import NodeBase

@dataclass
class TypeInfo:
    id: str
    val: str


@dataclass
class Node (NodeBase):
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

    def __init__(self, node_elem:ET):
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

    def __str__(self):
        return "\n".join(
            (
                f"Node: {self.name} [{self.address}]",
                indent(str(self.node_def), "  "),
            )
        )

    def json(self, parent):
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
    def load_from_file(nodes_path:str):
        """Load nodes from the specified XML file path.
        :param nodes_path: Path to the XML file containing nodes. (mandatory) 
        :return: Parsed XML root element.
        :raises NuCoreError: If the nodes path is not set or the file cannot be parsed.
        """
        if not nodes_path:
            raise NuCoreError("Nodes path is not set.")
        return ET.parse(nodes_path).getroot()

    @staticmethod
    def load_from_xml(xml):
        """
        Load nodes from an XML rep.
        :param xml: XML string containing nodes. (mandatory)
        :return: Parsed XML root element.
        :raises NuCoreError: If the XML is not set or cannot be parsed.
        """
        if xml is None:
            raise NuCoreError("xml is mandatory.")
        return ET.fromstring(xml) 