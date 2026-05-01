"""Base class and flag constants for all IoX node types.

Provides :class:`NodeBase` — the common ABC inherited by :class:`Node`,
:class:`Group`, and :class:`Folder` — together with the :class:`NodeTypes`
and :class:`NodeHierarchy` flag constants used to interpret the raw ``flag``
and ``parent_type`` attributes on each node XML element.
"""

#Base class for all nodes (devices, groups, folders)
from dataclasses import dataclass, field
from abc import ABC 
from xml.etree import ElementTree as ET
from .nodedef import NodeDef

class NodeTypes:
    """Bit-flag constants stored in the ``flag`` attribute of a node."""
    NODE_IS_A_GROUP         = 0x04
    NODE_IS_ROOT            = 0x08
    NODE_IS_IN_ERR          = 0x10
    NODE_IS_DEVICE_PRIMARY  = 0x80

class NodeHierarchy:
    """Hierarchy-level constants stored in the ``parent_type`` attribute of a node.

    Used to categorise nodes by their role in the device tree.
    """
    UD_HIERARCHY_NODE_TYPE_NOTSET   = 0
    UD_HIERARCHY_NODE_TYPE_NODE     = 1
    UD_HIERARCHY_NODE_TYPE_GROUP    = 2
    UD_HIERARCHY_NODE_TYPE_FOLDER   = 3

def node_is_group(flag) -> bool:
    """Return True if the node ``flag`` value has the group bit set."""
    return (flag & NodeTypes.NODE_IS_A_GROUP) != 0


@dataclass
class NodeBase(ABC):
    """Abstract base for all IoX node types (devices, groups, folders).

    Populated from a raw ``<node>`` or ``<group>`` XML element obtained from
    the IoX REST API.  Subclasses must call ``super().__init__(node_elem)``.

    Attributes:
        flag: Raw integer flag field encoding ``NodeTypes`` bit-flags.
        node_def_id: Identifier of the associated ``NodeDef``, if any.
        address: Unique node address on the controller.
        name: Human-readable node name.
        family: Protocol family index (e.g. 1 = Insteon, 10 = Z-Wave).
        instance: Instance index within the family.
        enabled: Whether the node is currently enabled on the controller.
        parent: Address of the parent node, group, or folder.
        parent_type: ``NodeHierarchy`` constant indicating the parent's kind.
        hint: Optional hint string from the controller.
        node_def: Resolved ``NodeDef`` object, populated after profile loading.
    """
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

    def __init__(self, node_elem: ET.Element) -> None:
        """Initialise ``NodeBase`` from an XML element.

        Args:
            node_elem: The ``<node>`` or ``<group>`` XML element to parse.

        Raises:
            ValueError: If *node_elem* is ``None``.
        """
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
        """Return True if this node is a group (scene)."""
        return (self.flag & NodeTypes.NODE_IS_A_GROUP) != 0

    def node_is_root(self) -> bool:
        """Return True if this node is a root node."""
        return (self.flag & NodeTypes.NODE_IS_ROOT) != 0

    def node_is_in_err(self) -> bool:
        """Return True if this node is currently in an error state."""
        return (self.flag & NodeTypes.NODE_IS_IN_ERR) != 0

    def node_is_device_primary(self) -> bool:
        """Return True if this node is the primary device node."""
        return (self.flag & NodeTypes.NODE_IS_DEVICE_PRIMARY) != 0
    
    def node_parent_is_node(self) -> bool:
        """Return True if this node's parent is a regular device node."""
        return self.parent_type == NodeHierarchy.UD_HIERARCHY_NODE_TYPE_NODE
    
    def node_parent_is_group(self) -> bool:
        """Return True if this node's parent is a group (scene)."""
        return self.parent_type == NodeHierarchy.UD_HIERARCHY_NODE_TYPE_GROUP

    def node_parent_is_folder(self) -> bool:
        """Return True if this node's parent is a folder."""
        return self.parent_type == NodeHierarchy.UD_HIERARCHY_NODE_TYPE_FOLDER
    
