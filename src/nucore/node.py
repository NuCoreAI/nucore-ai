from textwrap import indent
from dataclasses import dataclass, field
from .nodedef import NodeDef, Property


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

    def json(self):
        return {
            "name": self.name,
            "address": self.address,
            "properties":[p.json() for p in self.node_def.properties] if self.node_def else [],
               "properties":[p.json() for p in self.node_def.properties] if self.node_def else [],
                   "properties":[p.json() for p in self.node_def.properties] if self.node_def else [],
            "links": {
                "ctl": [link for link in self.node_def.links.ctl],
                "rsp": [link for link in self.node_def.links.rsp],
            } if self.node_def and self.node_def.links else [],
        }
    