# format rag 

import re
from nucore import NodeProperty, Node
from .rag_data_struct import RAGData
from .rag_formatter import RAGFormatter
from nucore import Node 


DEVICE_SECTION_HEADER="***Device***"

class DeviceRagFormatter(RAGFormatter):
    def __init__(self, indent_str: str = "    ", prefix: str = ""):
        self.lines = []
        self.level = 0
        self.indent_str = indent_str
        self.prefix = prefix

    def write(self, line: str = ""):
        indent = self.indent_str * self.level
        self.lines.append(f"{indent}{line}")

    def write_lines(self, lines: list[str]):
        for line in lines:
            self.write(line)

    def section(self, title: str):
        self.write(f"***{title}***")

    def block(self, level_increase: int = 2):
        class BlockContext:
            def __init__(self, writer: DeviceRagFormatter):
                self.writer = writer

            def __enter__(self):
                self.writer.level += level_increase

            def __exit__(self, exc_type, exc_val, exc_tb):
                self.writer.level -= level_increase

        return BlockContext(self)

    def add_device_section(self, device: Node, parent: Node ):
        self.section(f"Device")
        self.write(f"Name: {device.name}")
        self.write(f"ID: {device.address}")
        if parent:
            self.write(f"Parent: {parent.name} [ID: {parent.address}]")

    def add_properties_section(self):
        with self.block():
            self.section("Properties")

    def add_accept_commands_section(self):
        with self.block():
            self.section("Accept Commands")
    
    def add_send_commands_section(self):
        with self.block():
            self.section("Send Commands")

    def add_property(self, prop: NodeProperty):
        with self.block(level_increase=4):
            self.write(f"{prop.name} [id={prop.id}]")
            if prop.editor and prop.editor.ranges:
                for range in prop.editor.ranges:
                    with self.block():
                        if range.get_description():
                            self.write(f"{range.get_description()}")
                        if range.names:
                            with self.block(): 
                                #self.write("Permissible values:")
                                for name in range.get_names():
                                    self.write(name)

    def add_command(self, command):
        with self.block(level_increase=4):
            self.write(f"{command.name} [{command.id}]")
            if command.parameters:
                with self.block():
                    i=1
                    with self.block():
                        for param in command.parameters:
                            self.write(f"Parameter {i}: name={param.name if param.name else 'n/a'} [id={param.id if param.id else 'n/a'}]")
                            i += 1
                            if param.editor and param.editor.ranges:
                                for range in param.editor.ranges:
                                    with self.block():
                                        if range.get_description():
                                            self.write(f"{range.get_description()}")
                                        if range.names:
                                            with self.block(): 
                                                #self.write("Permissible values:")
                                                for name in range.get_names():
                                                    self.write(name)

    def format_node(self, node, parent):
        self.add_device_section(node, parent)
        if node.node_def:
            if node.node_def.properties:
                self.add_properties_section()
                for prop in node.node_def.properties:
                    self.add_property(prop)

            if node.node_def.cmds.accepts:
                self.add_accept_commands_section()
                for cmd in node.node_def.cmds.accepts:
                    self.add_command(cmd)

            if node.node_def.cmds.sends:
                self.add_send_commands_section()
                for cmd in node.node_def.cmds.sends:
                    self.add_command(cmd)


    def to_text(self) -> str:
        return "\n".join(self.lines)
    
    def __get_device_id__(self, line):
        if not line:
            return None
        match = re.search(r"ID:\s*(\S+)", line)
        if match:
            return match.group(1)
        return None

    def __get_device_name__(self, line):
        if not line:
            return None
        match = re.search(r"Name:\s*(\S+)", line)
        if match:
            return match.group(1)
        return None
    
    def __get_device_content__(self, index:int, rag_docs:RAGData):
        if not isinstance(rag_docs, RAGData):
            raise ValueError("RAG data must be a non-empty dictionary")

        if not self.lines[index].startswith(DEVICE_SECTION_HEADER):
            return None 
        
        content = self.lines[index] 
        index += 1
        device_id = "n/a"
        device_name = "n/a"
        i = index
        for i in range(index, len(self.lines)) :
            if self.lines[i].startswith("ID:"):
                device_id = self.__get_device_id__(self.lines[i])
            if self.lines[i].startswith("Name:"):
                device_name = self.__get_device_name__(self.lines[i])
            elif self.lines[i].startswith(DEVICE_SECTION_HEADER):
                # we reached the end of this device content
                break
            content += "\n" + self.lines[i]
        
        rag_docs.add_document(content, [], device_id, {"name": device_name})

        return i-1
    
    def format(self, **kwargs)->RAGData:
        """
        Convert the formatted devices into a list of RAG documents.
        Each document contains an ID, name, and content.
        :param nodes: mandatory, a list of nodes to format.
        :return: RAGData object containing the formatted documents.
        :raises ValueError: if no nodes are provided or if nodes is not a list. 
        """
        if not "nodes" in kwargs:
            raise ValueError("No nodes provided to format")
        if not isinstance(kwargs["nodes"], dict):
            raise ValueError("Nodes must be a dictionary")
        nodes = kwargs["nodes"]
        for node in nodes.values():
            pnode = node.pnode
            pnode = None if pnode is None or pnode == node.address else pnode 
            if pnode:
                pnode = nodes.get(pnode, None)
            self.format_node(node, pnode)
        rag_docs:RAGData = RAGData()
        i = 0
        # Iterate through the lines to find device sections
        # and extract their content
        while i < len(self.lines):
            if self.lines[i].startswith(DEVICE_SECTION_HEADER):
                i = self.__get_device_content__(i, rag_docs)
            i += 1
        
        return rag_docs