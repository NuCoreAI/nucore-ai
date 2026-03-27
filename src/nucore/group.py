from .linkdef import LinkDef
from enum import member

from .node_base import NodeBase
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET
from .nucore_error import NuCoreError

class GroupMemberType:
    MEMBER_IS_RESPONDER = 0x00
    MEMBER_IS_CONTROLLER = 0xF0

class ParamType:
    PARAM_TYPE_DEVICE = 0x00
    PARAM_TYPE_VARIABLE = 0x01

class Linktype:
    LINK_TYPE_NATIVE = 'native'
    """
    A direct link between the controller and the responder (e.g. Insteon links, Z-Wave associations, etc.). (Controller→Responder)
    """
    LINK_TYPE_DEFAULT = 'default'
    """
    Whatever command is sent by the controller is forwarded to the responder by the NuCore. (Controller→NuCore→Responder)
    """
    LINK_TYPE_COMMAND = 'cmd' 
    """
    Same as Default except when the controller sends an On command the NuCore sends the command **specified in the link** to the responder. (Controller→NuCore→Responder)
    """
    LINK_TYPE_IGNORE = 'ignore'
    """
    No link is made between the controller and responder.
    """

@dataclass
class LinkParams:
    type: int = field(default=ParamType.PARAM_TYPE_DEVICE)
    id: str = field(default=None) 
    val: str = field(default=None)
    uom: int = field(default=None)
    prec: int = field(default=0)

@dataclass
class GroupLink:
    node:NodeBase = field(default=None)
    type: Linktype = field(default=Linktype.LINK_TYPE_DEFAULT)
    linkdef: LinkDef = field(default=None)
    params: dict[str, LinkParams] = field(default_factory=dict)

    def add_params(self, params_root: list):
        """Enrich the GroupLink with parameters based on the provided params_root."""
        if not params_root:
            return
        for param in params_root:
            id = param.get('id', None)
            if id is None:
                continue    
            type = param.get('type', None)
            type = ParamType.PARAM_TYPE_DEVICE if type is None else ParamType.PARAM_TYPE_VARIABLE if type == 'variable' else ParamType.PARAM_TYPE_DEVICE
            val = param.get('val', None)
            if val is None:
                continue
            value = val.get('value', None)
            if value is None:
                continue
            uom = val.get('uom', None)
            if uom is None:
                continue
            prec = val.get('prec', 0)
            try:
                prec = int(prec)
            except ValueError:
                prec = 0
            self.params[id] = LinkParams(type=type, id=id, val=val, uom=uom, prec=prec)


@dataclass
class GroupMember:
    id: str
    family: int = field(default=0)
    instance: int = field(default=0)
    type: int = field(default=GroupMemberType.MEMBER_IS_RESPONDER)
    links: dict[str, GroupLink] = field(default_factory=dict)

    def add_links(self, links_root: list, nodes: dict, linkdef_lookup: dict):
        """Enrich the GroupMember with link definitions based on the provided links_root and linkdef_lookup."""
        if not links_root or not nodes or not linkdef_lookup:
            return
        
        for link in links_root:
             node = link.get('node', None)
             if node is None:
                continue
             node = nodes.get(node, None)
             if node is None:
                continue
             linkdef = link.get('linkdef', None)
             if linkdef is not None:
                linkdef = linkdef_lookup[f"{linkdef}.{node.family}.{node.instance}"] 
             type=link.get('type', None)

             try: 
                group_link = GroupLink(node=node, type=type, linkdef=linkdef)
                group_link.add_params(link.get('params', []))
                self.links[node.address] = group_link 
             except Exception as e:
                continue
        

@dataclass
class Group(NodeBase):

    def __init__(self, node_elem:ET.Element):
        super().__init__(node_elem)
        self.members = {}
        #add ourselves as container
        self.members[self.address] = GroupMember(id=self.address, type=GroupMemberType.MEMBER_IS_CONTROLLER, family=self.family, instance=self.instance)

    
    def add_links(self, links_root: dict, nodes:dict, linkdef_lookup: dict) -> bool:
        if links_root is not None and linkdef_lookup is not None:
            ctrls = links_root.get('ctl', [])
            for ctrl in ctrls:
                id = ctrl.get('id', None)
                if id is None:
                    continue
                member = self.members.get(id, None)
                if not member:
                    node = nodes.get(id, None)
                    if node is None:
                        continue
                    member = self.members[id] = GroupMember(id=id, type=GroupMemberType.MEMBER_IS_CONTROLLER, family=node.family, instance=node.instance)
                member.add_links(ctrl.get('links', {}), nodes, linkdef_lookup)

    def __hash__(self):
        return hash(self.address)  # or another unique identifier