# format rag 
"""
Profile RAG Formatter
Formats profile information into a structured text format suitable for RAG processing.
Each profile is represented with its properties and commands in a clear, hierarchical manner.
The top level is a profile and therefore, profiles/editors are not repeated for devices of the same nodedef. 
"""

from nucore import NodeProperty, Node

from .rag_data_struct import RAGData
from .rag_formatter import RAGFormatter
from nucore import Node, Group, Folder, RuntimeProfile, NodeHierarchy
import base64

ENCODE_IDS = False # whether to encode ids to make them URL safe

PROFILE_HEADER = "Profile"
DEVICE_HEADER = "Device"

PROFILE_SECTION_HEADER = f"==={PROFILE_HEADER}==="
DEVICE_SECTION_HEADER = f"==={DEVICE_HEADER}==="

class RagChunk:
    def __init__(self, nodedef_id: str, begin_index: int):
        self.nodedef_id = nodedef_id
        self.begin_index = begin_index
        self.end_index = -1
        self.nodes : list[Node] = []
        self.cmds : list = []
        self.properties: list[NodeProperty] = []

    def get_content(self, lines: list[str]) -> str:
        """
        Get the content of the chunk from the provided lines.
        :param lines: List of lines to extract content from. 
        :return: the content in string.
        """
        content = ""
        for i in range(self.begin_index, self.end_index + 1):
            content += lines[i] + "\n"
        return content.strip()

    def get_meta_data(self) -> dict:
        """
        Get the metadata of the chunk.
        :return: metadata dictionary of devices (nodes) and their names/ids, commands and properties
        """

        return {
            "devices": [{"name": node.name, "id": node.address} for node in self.nodes],
            "commands": [{"name": cmd.name} for cmd in self.cmds],
            "properties": [{"name": prop.name} for prop in self.properties]
        }

class ProfileRagFormatter(RAGFormatter):
    def __init__(self, json_output:bool, indent_str: str = " ", prefix: str = ""):
        self.lines = []
        self.level = 0
        self.rag_chunks: list[RagChunk] = []
        self.json_output = json_output
        self.indent_str = indent_str
        self.prefix = prefix

    @staticmethod
    def encode_id(id:str)->str:
        # encode to base64 to make it URL safe
        if not id:
            return ""
        return base64.b64encode(id.encode('utf-8')).decode('utf-8') if ENCODE_IDS else id

    @staticmethod 
    def decode_id(id:str)->str:
        if not id:
            return ""
        return base64.b64decode(id).decode('utf-8') if ENCODE_IDS else id


    def write(self, line: str = ""):
        indent = self.indent_str * self.level
        self.lines.append(f"{indent}{line}")

    def block(self, level_increase: int = 2):
        class BlockContext:
            def __init__(self, writer: ProfileRagFormatter):
                self.writer = writer

            def __enter__(self):
                self.writer.level += level_increase

            def __exit__(self, exc_type, exc_val, exc_tb):
                self.writer.level -= level_increase

        return BlockContext(self)

    def add_device_section_json(self, device: Node, parent: Node, comma:bool ):
        line=(f"{{\"name\":\"{device.name}\",\"id\":\"{self.encode_id(device.address)}\"")
        if parent:
            line+=(f",\"parent\":{{\"name\":\"{parent.name}\",\"id\":\"{self.encode_id(parent.address)}\"}}")
        line+=("}")
        if comma:
            line+=","
        self.write(line)

    def add_device_section(self, device: Node, parent: Node, comma:bool ):
        if self.json_output:
            self.add_device_section_json(device, parent, comma)
            return
        
        self.write(f"- \"{device.name}\" id={self.encode_id(device.address)}")
        if parent:
            with self.block():
                self.write(f"Parent: \"{parent.name}\" id={self.encode_id(parent.address)}")

    def add_group_section_json(self, group: Group, parent: Node, comma:bool):
        line=(f"{{\"name\":\"{group.name}\",\"id\":\"{self.encode_id(group.address)}\"")
        if parent:
            line+=(f",\"parent\":{{\"name\":\"{parent.name}\",\"id\":\"{self.encode_id(parent.address)}\"}}")
        line+=("}")
        if comma:
            line+=","
        self.write(line)

    def add_group_section(self, group: Group, parent: Node, comma:bool):
        if self.json_output:
            self.add_group_section_json(group, parent, comma)
            return
        
        self.write(f"- \"{group.name}\" id={self.encode_id(group.address)}")
        if parent:
            with self.block():
                if self.json_output:
                    self.write(f"\"parent\": {{\"name\":\"{parent.name}\", \"id\":\"{self.encode_id(parent.address)}\"}}")
                else:
                    self.write(f"Parent: \"{parent.name}\" id={self.encode_id(parent.address)}")

    def add_folder_section_json(self, folder: Folder, parent: Node, comma:bool):
        line=(f"{{\"name\":\"{folder.name}\",\"id\":\"{self.encode_id(folder.address)}\"")
        if parent:
            line+=(f",\"parent\":{{\"name\":\"{parent.name}\",\"id\":\"{self.encode_id(parent.address)}\"}}")
        line+=("}")
        if comma:
            line+=","
        self.write(line)
        
    def add_folder_section(self, folder: Folder, parent: Node, comma:bool):
        if self.json_output:
            self.add_folder_section_json(folder, parent, comma)
            return
        
        if parent:
            with self.block():
                if self.json_output:
                    self.write(f"\"parent\": {{\"name\":\"{parent.name}\", \"id\":\"{self.encode_id(parent.address)}\"}}")
                else:
                    self.write(f"Parent: \"{parent.name}\" id={self.encode_id(parent.address)}")
    
    def add_property_json(self, prop: NodeProperty, comma:bool):
        with self.block():
            line=(f"{{\"name\":\"{prop.name}\",\"id\":\"{self.encode_id(prop.id)}\",")
            if prop.editor and prop.editor.ranges:
                line += prop.editor.get_json_descriptions()
        line += ("}")
        if comma: 
            line += ","
        self.write(line)

    def add_property(self, prop: NodeProperty, comma:bool):
        if self.json_output:
            self.add_property_json(prop,comma)
            return

        with self.block():
            self.write(f"- \"{prop.name}\" id={prop.id}")
            if prop.editor and prop.editor.ranges:
                prop.editor.write_descriptions(self)

    def add_command_json(self, command, comma:bool):
        with self.block():
            line=(f"{{\"name\":\"{command.name}\",\"id\":\"{command.id}\"")
            if command.parameters is not None and len(command.parameters) > 0:
                line+=(",\"parameters\":[")
                for i, param in enumerate(command.parameters):
                    line+=(f"{{\"name\":\"{param.name if param.name else 'n/a'}\",\"id\":\"{self.encode_id(param.id) if param.id else 'n/a'}\"")
                    if param.editor and param.editor.ranges:
                        line+=","+param.editor.get_json_descriptions()
                    line+=("}")
                    if i < len(command.parameters) - 1:
                        line+=","
                line+=("]")
            line+=("}") 
            if comma:
                line+=","
            self.write(line)

    def add_command(self, command, comma):
        if self.json_output:
            self.add_command_json(command,comma)
            return

        with self.block():
            self.write(f"- \"{command.name}\" id={command.id}")
            if command.parameters is not None and len(command.parameters) > 0:
                with self.block():
                    self.write("Parameters:")
                    with self.block():
                        for param in command.parameters:
                            #self.write(f"Parameter {i}: name={param.name if param.name else 'n/a'} [id={param.id if param.id else 'n/a'}]")
                            self.write(f"- \"{param.name if param.name else 'n/a'}\" id={param.id if param.id else 'n/a'}")
                            if param.editor and param.editor.ranges:
                                param.editor.write_descriptions(self)


    def __get_parent_node__(self, node:Node)->Node:
        try:
            pnode = node.pnode if isinstance(node, Node) else node.parent
            pnode_type = node.parent_type
            pnode = None if pnode is None or pnode == node.address else pnode 
            if pnode:
                if pnode_type == NodeHierarchy.UD_HIERARCHY_NODE_TYPE_GROUP:
                    return self.groups.get(pnode, None)
                if pnode_type == NodeHierarchy.UD_HIERARCHY_NODE_TYPE_FOLDER:
                    return self.folders.get(pnode, None)
                return self.nodes.get(pnode, None)
        except Exception as e:
            print(f"Error getting parent node for {node.name if hasattr(node, 'name') else 'unknown'}: {e}")
        return None

    def add_node(self, node:Node, comma:bool):
        parent = self.__get_parent_node__(node)
        self.add_device_section(node, parent, comma)

    def format_profile_first(self, profile:RuntimeProfile, device_first:bool=False):
        """
        Format the profile with profile first, then list of supported devices.
        :aram device_first: if true, format devices first then shared features.
        :param profile: with a list of supported devices 
        """
        if profile is None or profile.nodedef is None or not isinstance(profile, RuntimeProfile):
            raise ValueError("Invalid runtime profile provided to format")

        chunk = RagChunk(profile.nodedef.id, len(self.lines))
        self.write(PROFILE_SECTION_HEADER)   
        line = "" 
        if self.json_output:
            line = f"{{\"id\":\"{profile.nodedef.id}\"" 
        else:  
            self.write("profile id=" + profile.nodedef.id) 
        with self.block():
            if len(profile.nodes) > 0 and device_first:
                if self.json_output:
                    line += ",\"supported_devices\":["
                    self.write(line)
                else:
                    self.write("Supported Devices:")
                nodes = list(profile.nodes)
                for i, node in enumerate(nodes):
                    self.add_node(node, self.json_output and i < len(nodes) - 1)
                if self.json_output:
                    self.write("],\"properties\":[")
            if len(profile.nodedef.properties) > 0:
                if not self.json_output:
                    self.write("Properties:")
                for i, prop in enumerate(profile.nodedef.properties):
                    self.add_property(prop, self.json_output and i < len(profile.nodedef.properties) - 1)
            if self.json_output:
                self.write("],\"accept_commands\":[")
            if len(profile.nodedef.cmds.accepts) > 0:
                if not self.json_output:
                    self.write("Accept Commands:")
                for i, cmd in enumerate(profile.nodedef.cmds.accepts):
                    self.add_command(cmd, self.json_output and i < len(profile.nodedef.cmds.accepts) - 1)
            if self.json_output:
                self.write("],\"send_commands\":[")
            if len(profile.nodedef.cmds.sends) > 0:
                if not self.json_output:
                    self.write("Sends Commands:")
                for i, cmd in enumerate(profile.nodedef.cmds.sends):
                    self.add_command(cmd, self.json_output and i < len(profile.nodedef.cmds.sends) - 1)
            if self.json_output:
                self.write("]")
            if not device_first and len(profile.nodes) > 0:
                if self.json_output:
                    self.write(",\"supported_devices\":[")
                else:
                    self.write("Supported Devices:")
                for i, node in enumerate(profile.nodes): 
                    self.add_node(node, self.json_output and i < len(profile.nodes) - 1)
                if self.json_output:
                    self.write("]")
        if self.json_output:
            self.write("}")

        chunk.end_index = len(self.lines) - 1   
        chunk.nodes = profile.nodes
        chunk.cmds = list(profile.nodedef.cmds.sends) + list(profile.nodedef.cmds.accepts)
        chunk.properties = list(profile.nodedef.properties)
        self.rag_chunks.append(chunk) 

    def format_per_device(self, node:Node):
        """
        Format the profile per device, with devices as atomic units with their own properties/commands/editors. 
        
        :param node: the node to format
        """
        if node is None or not isinstance(node, Node):
            raise ValueError("Invalid runtime profile provided to format")

        chunk = RagChunk(node.address, len(self.lines))
        self.write(DEVICE_SECTION_HEADER)   
        self.add_node(node)
        
        with self.block():
            if node.node_def:
                self.write("Properties:")
                for prop in node.node_def.properties: 
                    self.add_property(prop)
                self.write("Accept Commands:")
                for cmd in node.node_def.cmds.accepts:
                    self.add_command(cmd)
                self.write("Sends Commands:")
                for cmd in node.node_def.cmds.sends:
                    self.add_command(cmd)

        chunk.end_index = len(self.lines) - 1   
        chunk.nodes = [ node ] 
        chunk.cmds = list(node.node_def.cmds.sends) + list(node.node_def.cmds.accepts)
        chunk.properties = list(node.node_def.properties)
        self.rag_chunks.append(chunk) 

    def format(self, **kwargs)->RAGData:
        """
        Convert the formatted profiles into a list of RAG documents.
        Each document contains an ID, name, and content.
        :param profiles: mandatory, a dictionary of profiles to format. (includees nodes/groups)
        :param nodes: mandatory, a list of nodes to be used for parent lookup. 
        :param groups: mandatory, a list of groups to be used for hierarhies.
        :param folders: mandatory, a list of folders to be used for hierarchies 
        :return: RAGData object containing the formatted documents.
        :raises ValueError: if no profiles are provided or if profiles is not a list. 
        """
        self.profiles = None
        self.nodes = None
        self.groups = None
        self.folders = None
        if "profiles" in kwargs:
            self.profiles = kwargs["profiles"]
            if not isinstance(self.profiles, dict): 
                raise ValueError("Profiles must be a dictionary")
        if not "nodes" in kwargs:
            raise ValueError("No nodes provided to format")
        self.nodes = kwargs["nodes"]
        if not isinstance(self.nodes, dict):
            raise ValueError("Nodes must be a dict")

        if "groups" in kwargs:
            self.groups = kwargs["groups"]
            if not isinstance(self.groups, dict):
                raise ValueError("Groups must be a dictionary")
        
        if "folders" in kwargs:
            self.folders = kwargs["folders"]
            if not isinstance(self.folders, dict):
                raise ValueError("Folders must be a dict")

        if self.profiles is not None:
            for profile in self.profiles.values():
                self.format_profile_first(profile, device_first=True)
        else:
            if self.nodes is None or self.groups is None:
                raise ValueError("Insufficient data to format profile RAG (need both nodes and groups).")
            for node in self.nodes.values():
                self.format_per_device(node)
            
        rag_docs:RAGData = RAGData()
        for chunk in self.rag_chunks:
            rag_docs.add_document(
                chunk.get_content(self.lines),
                None, # No embeddings
                id=f"{chunk.nodedef_id}",
                metadata=chunk.get_meta_data(),
            )
        
        return rag_docs