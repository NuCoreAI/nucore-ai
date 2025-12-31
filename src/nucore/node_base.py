#Base class for all nodes (devices, groups, folders)
from dataclasses import dataclass, field
from abc import ABC 
from xml.etree import ElementTree as ET
from .nodedef import NodeDef

class NodeTypes:
    '''
        NodeTypes constants represent various flags for node characteristics stored in the flag attribute of a Node.
    '''
    NODE_IS_A_GROUP         = 0x04
    NODE_IS_ROOT            = 0x08
    NODE_IS_IN_ERR          = 0x10
    NODE_IS_DEVICE_PRIMARY  = 0x80

class NodeHierarchy:
    '''
        NodeHierarchy constants represent different types of node hierarchy levels. It is used to categorize nodes based on their role in the hierarchy.
        It's assigned to parent_type attribute of NodeBase class.
    '''
    UD_HIERARCHY_NODE_TYPE_NOTSET   = 0
    UD_HIERARCHY_NODE_TYPE_NODE     = 1
    UD_HIERARCHY_NODE_TYPE_GROUP    = 2
    UD_HIERARCHY_NODE_TYPE_FOLDER   = 3

def node_is_group(flag) -> bool:
        return (flag & NodeTypes.NODE_IS_A_GROUP) != 0


@dataclass
class NodeBase(ABC):
    flag: int = field(default=0)
    node_def_id: str = field(default=None)
    address: str = field(default=None)
    name: str = field(default=None)
    family: int = field(default=0)
    instance: int = field(default=0)
    enabled: bool = field(default=True)
    parent: str = field(default=None)
    parent_type: int = field(default=NodeHierarchy.UD_HIERARCHY_NODE_TYPE_NOTSET)
    hint: str = field(default=None)
    node_def: NodeDef = field(default=None)

    def __init__(self, node_elem:ET):
        if node_elem is None:
            raise ValueError("root element cannot be None")
        
        self.flag=int(node_elem.get("flag"))
        self.node_def_id = node_elem.get("nodeDefId") if node_elem.get("nodeDefId") is not None else None
                # youtube hack
        family_elem = node_elem.find("./family")
        if family_elem is not None:
            try:
                self.family = int(family_elem.text)
            except (ValueError, TypeError):
                self.family = 1
            try:
                self.instance = int(family_elem.get("instance"))
            except (ValueError, TypeError):
                self.instance = 1
        else:
            self.family = 1
            self.instance = 1
        
        self.address=node_elem.find("./address").text
        self.name=node_elem.find("./name").text
        self.enabled=(node_elem.find("./enabled").text.lower() == "true") if node_elem.find("./enabled") is not None else None
        self.hint=node_elem.find("./hint").text if node_elem.find("./hint") is not None else None
        parent_element = node_elem.find("./parent")
        if parent_element is not None:
            self.parent= parent_element.text 
            self.parent_type = int(parent_element.get("type")) if parent_element.get("type") is not None else NodeHierarchy.UD_HIERARCHY_NODE_TYPE_NOTSET

    def node_is_group(self) -> bool:
        return (self.flag & NodeTypes.NODE_IS_A_GROUP) != 0

    def node_is_root(self) -> bool:
        return (self.flag & NodeTypes.NODE_IS_ROOT) != 0

    def node_is_in_err(self) -> bool:
        return (self.flag & NodeTypes.NODE_IS_IN_ERR) != 0

    def node_is_device_primary(self) -> bool:
        return (self.flag & NodeTypes.NODE_IS_DEVICE_PRIMARY) != 0
    
    def node_parent_is_node(self) -> bool:
        return self.parent_type == NodeHierarchy.UD_HIERARCHY_NODE_TYPE_NODE
    
    def node_parent_is_group(self) -> bool:
        return self.parent_type == NodeHierarchy.UD_HIERARCHY_NODE_TYPE_GROUP

    def node_parent_is_folder(self) -> bool:
        return self.parent_type == NodeHierarchy.UD_HIERARCHY_NODE_TYPE_FOLDER
    
