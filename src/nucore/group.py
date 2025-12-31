from .node_base import NodeBase
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

class GroupMemberType:
    MEMBER_IS_RESPONDER = 0x00
    MEMBER_IS_CONTROLLER = 0xF0

@dataclass
class GroupMember:
    address: str
    type: int = field(default=GroupMemberType.MEMBER_IS_RESPONDER)

@dataclass
class Group(NodeBase):
    members: list[GroupMember] = field(default_factory=list)

    def __init__(self, node_elem:ET):
        super().__init__(node_elem)
        self.members = []
        self.add_members(node_elem)

    def add_members(self, root_node: ET) -> bool:
        """Parse and add group members from the provided XML root node.
        :param root_node: XML root element containing group member definitions. (mandatory) 
        :return: True if members were added successfully, False otherwise.
        """
        members_added = False
        for member_elem in root_node.findall('.//link'):
            address = member_elem.text
            try:
                type = int(member_elem.get('type'))
            except Exception:
                type = GroupMemberType.MEMBER_IS_RESPONDER

            if address:
                self.members.append(GroupMember(address=address, type=type))
                members_added = True
        return members_added
