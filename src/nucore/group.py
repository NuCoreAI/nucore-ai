from cProfile import label

from .linkdef import LinkDef
from enum import member

from .node_base import NodeBase
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET
from .uom import get_uom_by_id

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
    name: str = field(default=None) 
    val: str = field(default=None)
    uom: int = field(default=None)

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

            property_name = self.linkdef.parameters.get(id, None).name if self.linkdef and self.linkdef.parameters and id in self.linkdef.parameters else None

            if property_name is None:            
                property = self.node.node_def.properties.get(id, None)
                if property :
                    property_name = property.name

            type = param.get('type', None)
            type = ParamType.PARAM_TYPE_DEVICE if type is None else ParamType.PARAM_TYPE_VARIABLE if type == 'variable' else ParamType.PARAM_TYPE_DEVICE
            val = param.get('val', None)
            if val is None:
                continue
            value = val.get('value', None)
            if value is None:
                continue
            try:
                value = float(value)
            except ValueError:
                continue
            uom = val.get('uom', None)
            prec = val.get('prec', 0)
            try:
                prec = int(prec)
                value = value / (10 ** prec) if prec > 0 else value
            except ValueError:
                prec = 0
            self.params[id] = LinkParams(type=type, id=id, name=property_name, val=value, uom=uom)

    def explain_json(self):
        responder={}
        label = f"{self.node.name} [address={self.node.address}]"
        responder[label] = {}
        try:
            if self.type == Linktype.LINK_TYPE_NATIVE:
                if len (self.params) > 0:
                    responder[label]["link_type"] = "native"
                    responder[label]["parameters"] = []

                    for param in self.params.values():
                        uom = get_uom_by_id(param.uom) if param.uom else None
                        out_str=(f"{param.val} {uom.name}" if uom else f"{param.val}")
                        try:
                            if uom and uom.name == "Enum":
                                property = self.node.node_def.properties.get(param.id, None)
                                if property and property.editor and property.editor.ranges and property.editor.ranges[0].names:
                                    names = property.editor.ranges[0].names
                                    param_val = names.get(str(int(param.val)), None)
                                    out_str=(f"{param_val}")
                        except Exception as e:
                            pass
                        plabel = f"{param.name} [id={param.id}]"
                        responder[label]["parameters"].append({plabel: out_str})
            elif self.type == Linktype.LINK_TYPE_DEFAULT:
                responder[label]["link_type"] = "default"
            elif self.type == Linktype.LINK_TYPE_COMMAND:
                responder[label]["link_type"] = "command"
            elif self.type == Linktype.LINK_TYPE_IGNORE:
                responder[label]["link_type"] = "ignore"
        except Exception as e:
            responder[label]["link_type"] = "error occured"

        return responder 

    def explain(self, index):
        explanation_lines = []
        try:
            if self.type == Linktype.LINK_TYPE_NATIVE:
                if len (self.params) > 0:
                    explanation_lines.append(f"  {index+1}. `{self.node.name}` is natively activated and set to the following parameters:")
                    for param in self.params.values():
                        uom = get_uom_by_id(param.uom) if param.uom else ""
                        out_str=(f"{param.val} {uom.name}")
                        try:
                            if uom.name == "Enum":
                                property = self.node.node_def.properties.get(param.id, None)
                                if property and property.editor and property.editor.ranges and property.editor.ranges[0].names:
                                    names = property.editor.ranges[0].names
                                    param_val = names.get(str(int(param.val)), None)
                                    out_str=(f"{param_val}")
                        except Exception as e:
                            pass
                        explanation_lines.append(f"    - {param.name} is set to {out_str}")
                else:
                    explanation_lines.append(f"  {index+1}. `{self.node.name}` is natively activated." )
            elif self.type == Linktype.LINK_TYPE_DEFAULT:
                explanation_lines.append(f"  {index+1}. `{self.node.name}` is sent the same command." )
            elif self.type == Linktype.LINK_TYPE_COMMAND:
                explanation_lines.append(f"  {index+1}. `{self.node.name}` is sent the unique command specified in the linkdef." )
            elif self.type == Linktype.LINK_TYPE_IGNORE:
                explanation_lines.append(f"  {index+1}. `{self.node.name}` ignores the command." )
        except Exception as e:
            explanation_lines.append(f"  {index+1}. `{self.node.name}` is linked but an error occurred while explaining the link: {str(e)}")

        return explanation_lines

@dataclass
class GroupMember:
    address: str
    name: str = field(default=None)
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

    def explain_json(self, group:dict, is_nucore_controller=False):    
        if len(self.links) == 0:
            return  
        responders = []
        for _, link in enumerate(self.links.values()):
            if link.node.address == self.address:
                continue
            responder = link.explain_json()
            responders.append(responder)

        return responders

    def explain(self, is_nucore_controller=False):    
        explanation_lines = []
        if len(self.links) == 0:
            return explanation_lines

        if is_nucore_controller:    
            explanation_lines.append(f"* When you activate `{self.name}` scene from the NuCore controller, this is what happens:")
        else:
            explanation_lines.append(f"* When you activate `{self.name}`, this is what happens:")
        for i, link in enumerate(self.links.values()):
            explanation_lines.extend(link.explain(i))
        
        return explanation_lines

@dataclass
class Group(NodeBase):

    def __init__(self, node_elem:ET.Element):
        super().__init__(node_elem)
        self.members = {}
        #add ourselves as container
        self.members[self.address] = GroupMember(address=self.address, name=self.name, type=GroupMemberType.MEMBER_IS_CONTROLLER, family=self.family, instance=self.instance)

    
    def add_links(self, links_root: dict, nodes:dict, linkdef_lookup: dict) -> bool:
        if self.address == "30263":
            crap --- now that we get things working the UOMs are not processed.
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
                    member = self.members[id] = GroupMember(address=id, name=node.name, type=GroupMemberType.MEMBER_IS_CONTROLLER, family=node.family, instance=node.instance)
                member.add_links(ctrl.get('links', {}), nodes, linkdef_lookup)

    def __find_cross_links(self):
        """
        Identify and return any cross-links between members of the group.
        Max number of cross links is the total number of members; each member is a controller with links to responders
        """

        controllers =  []
        responders = []
        for member in self.members.values():
            if member.address == self.address:
                continue
            controllers.append(member.name)
            for link in member.links.values():
                responders.append(link.node.name)
        return (controllers, responders)
    
    def __find_cross_links_json(self):
        """
        Identify and return any cross-links between members of the group.
        Max number of cross links is the total number of members; each member is a controller with links to responders
        """

        controllers =  []
        responders = []
        for member in self.members.values():
            if member.address == self.address:
                continue
            controllers.append({
                "name": member.name,
                "address": member.address
            })
            for link in member.links.values():
                responders.append({
                    "name": link.node.name,
                    "address": link.node.address
                })
        return (controllers, responders)

    
    def explain_json(self):
        """
        LLM friendly explanation of the group, its members, and their links in JSON format. 
        """
        group = {
          #  "id": self.address,
          #  "name": self.name,
        }

        if (len(self.members) <= 1):
            group["nucore_scene_activation"]="This is a collection but does not control anything else"
            return group

        # let's see if our group is controlling anything at all
        me = self.members.get(self.address, None)
        if me is not None and me.type == GroupMemberType.MEMBER_IS_CONTROLLER:
            if len(me.links) > 0:
                group["nucore_scene_activation"]=me.explain_json(True)
            else:
                group["nucore_scene_activation"]="This is a collection but does not control anything else"

        #cross_linked_controllers, _ = self.__find_cross_links_json()
        #if len(cross_linked_controllers) > 0:
        #   group["cross_linked_controllers"]=cross_linked_controllers

        # now let each mmember explain themselves
        if len (self.members) > 0:
            controller_activation_map = {} 
            for member in self.members.values():
                if member.address == self.address:
                    continue

                controller_activation_map[f"{member.name} [address={member.address}]"] = member.explain_json(group)
            group["controller_activation_map"] = controller_activation_map

        return group

    def explain_text(self, is_json=True):
        """
        Generate a human-readable explanation of the group, its members, and their links.
        """
        if is_json:
            return self.explain_json()
        explanation_lines = []
        explanation_lines.append(f"# Group `{self.name}`:")
        if (len(self.members) <= 1):
            explanation_lines.append("* Is a collection but does not control anything else.")
            return "\n".join(explanation_lines)

        # let's see if our group is controlling anything at all
        me = self.members.get(self.address, None)
        if me is not None and me.type == GroupMemberType.MEMBER_IS_CONTROLLER:
            if len(me.links) > 0:
                explanation_lines.extend(me.explain(True))
            else:
                explanation_lines.append("* Is a collection but does not control anything else.")

        cross_linked_controllers, cross_link_responders = self.__find_cross_links()
        if len(cross_linked_controllers) > 0:
           controllers=""
           for controller in cross_linked_controllers:
               controllers += f"`{controller}` "
               if controller != cross_linked_controllers[-1]:
                   controllers += "and "

           explanation_lines.append(f"* {controllers} are **cross-linked** and control the following members:") 
           for index, responder in enumerate(cross_link_responders):
               explanation_lines.append(f"  {index+1}. `{responder}`")

        # now let each mmember explain themselves
        for member in self.members.values():
            if member.address == self.address:
                continue
            explanation_lines.extend(member.explain())

        return "\n".join(explanation_lines)

    def __hash__(self):
        return hash(self.address)  # or another unique identifier