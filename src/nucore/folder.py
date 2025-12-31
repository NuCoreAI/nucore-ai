from .node_base import NodeBase
from dataclasses import dataclass

@dataclass
class Folder(NodeBase):
    pass
    def __init__(self, node_elem):
        super().__init__(node_elem)