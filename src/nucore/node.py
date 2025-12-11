from textwrap import indent
from dataclasses import dataclass, field
from .nodedef import NodeDef, Property
from .nucore_error import NuCoreError
import xml.etree.ElementTree as ET


@dataclass
class TypeInfo:
    id: str
    val: str


@dataclass
class Node:
    flag: int
    nodeDefId: str
    address: str
    name: str
    family: int
    instance: int
    hint: str
    type: str
    enabled: bool
    deviceClass: int
    wattage: int
    dcPeriod: int
    startDelay: int
    endDelay: int
    pnode: str
    node_def: NodeDef = None
    rpnode: str = field(default=None)
    sgid: int = field(default=None)
    typeInfo: list[TypeInfo] = field(default_factory=list)
    properties: dict[str, Property] = field(default_factory=dict) 
    parent: str = field(default=None)
    custom: dict = field(default=None)
    devtype: dict = field(default=None)

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