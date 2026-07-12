"""Folder node dataclass for IoX / ISY device hierarchy."""

from .node_base import NodeBase
from dataclasses import dataclass

@dataclass
class Folder(NodeBase):
    """Represents an IoX folder in the device/group hierarchy.

    Folders are used on the ISY controller to organise devices and groups.
    They behave identically to :class:`NodeBase` but carry no additional
    attributes.
    """
    pass
    def __init__(self, node_elem):
        """Initialise from an XML element."""
        super().__init__(node_elem)
    
    def __hash__(self) -> int:
        """Hash by unique node address."""
        return hash(self.address)  # or another unique identifier