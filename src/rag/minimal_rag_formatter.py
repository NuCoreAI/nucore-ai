"""
Minimal RAG Formatter
Outputs devices in ultra-compact delimited format for LLM matching.
Format: device_id: device_name | props: p1, p2 | cmds: c1, c2 | enums: e1, e2
"""

import json
from nucore import Node, Group, Folder, RuntimeProfile, NodeDef
from .rag_data_struct import RAGData
from .rag_formatter import RAGFormatter
from .dedupe_profiles import DedupeProfiles


class MinimalRagFormatter(RAGFormatter):
    """
    Generates minimal delimited device representations.
    Each line: device_id: device_name | props: p1, p2 | cmds: c1, c2 | enums: e1, e2
    """
    
    def __init__(self,json_output:bool=False, condense:bool=False):
        self.lines = []
        self.profiles = None
        self.nodes = None
        self.groups = None
        self.folders = None
        self.condense = condense
        self.json_output = json_output

    def _collect_enum_values(self, editor) -> list[str]:
        """Extract all enum value labels from an editor."""
        enums = []
        if not editor or not editor.ranges:
            return enums
        
        for range_obj in editor.ranges:
            if hasattr(range_obj, 'names') and range_obj.names:
                # Extract just the labels from "value: label" format
                for value, label in range_obj.names.items():
                    if label and label not in enums:
                        enums.append(label)
        return enums
    
    def _format_nodedef_json(self, node_def: NodeDef) -> dict:
        """Format a single node into delimited string with sections."""
        if not node_def:
            return None 
        
        out={}

        # Collect property names and their enums
        property_names = []
        
        for prop_id, prop in node_def.properties.items():
            if prop.name:
                value={f'{prop.name}': []}
            # Collect enums from property editors
                if prop.editor:
                    enums=[]
                    enums.extend(self._collect_enum_values(prop.editor))
                    value={f'{prop.name}': enums}
        
                property_names.append(value)
        # Collect command names and their parameter enums
        accepts_commands = []
        sends_commands = []
        
        for cmd in node_def.cmds.accepts:
            if cmd.name:
                value={f'{cmd.name}': []}
            # Collect enums from command parameters
                if cmd.parameters:
                    enums=[]
                    for param in cmd.parameters:
                        if param.editor:
                            enums.extend(self._collect_enum_values(param.editor))
                    value={f'{cmd.name}': enums}
                accepts_commands.append(value)

        
        for cmd in node_def.cmds.sends:
            if cmd.name:
                value={f'{cmd.name}': []}
            # Collect enums from command parameters
                if cmd.parameters:
                    enums=[]
                    for param in cmd.parameters:
                        if param.editor:
                            enums.extend(self._collect_enum_values(param.editor))
                    value={f'{cmd.name}': enums}
                sends_commands.append(value)
        
        out["props"] = property_names
        out["accepts-cmds"] = accepts_commands
        out["sends-cmds"] = sends_commands
        return out
    
    def _format_node_json(self, node: Node) -> dict:
        """Format a single node into delimited string with sections."""
        if not node or not node.node_def:
            return None 

        out = {
            "name": node.name,
            "id": node.address, 
            "props": [],
            "accepts-cmds": [],
            "sends-cmds": []
        }

        node_def = self._format_nodedef_json(node.node_def)
        if node_def:
            out["props"] = node_def.get("props", [])
            out["accepts-cmds"] = node_def.get("accepts-cmds", [])
            out["sends-cmds"] = node_def.get("sends-cmds", [])
        
        return out


    def _format_node(self, node: Node):
        """Format a single node into delimited string with sections."""
        if not node or not node.node_def:
            return None 
        
        if self.json_output:
            return self._format_node_json(node)
        
        # Collect property names and their enums
        property_names = []
      #  property_enums = []
        
        for prop in node.node_def.properties:
            if prop.name:
                value={f'{prop.name}': []}
                # Collect enums from property editors
                if prop.editor:
                    enums=[]
                    enums.extend(self._collect_enum_values(prop.editor))
                    value={f'{prop.name}': enums}
                property_names.append(value)
        
        # Collect command names and their parameter enums
        accept_commands = []
        send_commands = []
      #  command_enums = []
        
        for cmd in node.node_def.cmds.accepts:
            if cmd.name:
                value={f'{cmd.name}': []}
            # Collect enums from command parameters
                if cmd.parameters:
                    enums=[]
                    for param in cmd.parameters:
                        if param.editor:
                            enums.extend(self._collect_enum_values(param.editor))
                    value={f'{cmd.name}': enums}
                accept_commands.append(value)
        
        for cmd in node.node_def.cmds.sends:
            if cmd.name:
                value={f'{cmd.name}': []}
            # Collect enums from command parameters
                if cmd.parameters:
                    enums=[]
                    for param in cmd.parameters:
                        if param.editor:
                            enums.extend(self._collect_enum_values(param.editor))
                    value={f'{cmd.name}': enums}
                send_commands.append(value)
        
        # Combine all enums and remove duplicates
        #all_enums = property_enums + command_enums
        #unique_enums = []
        #seen = set()
        #for enum in all_enums:
        #    if enum not in seen:
        #        seen.add(enum)
        #        unique_enums.append(enum)
        
        # Build delimited string
        parts = [f"\"{node.name}\""]
        
        if property_names:
            parts.append(f"`props`: {', '.join(property_names)}")
        
        if accept_commands:
            parts.append(f"`accept-cmds`: {', '.join(accept_commands)}")
        
        if send_commands:
            parts.append(f"`send-cmds`: {', '.join(send_commands)}")
        
        #if unique_enums:
        #    parts.append(f"`enums`: {', '.join(unique_enums)}")
        
        return f">>> \"{node.address}\" : {' | '.join(parts)} <<<"

    def _format_profile(self, profile: RuntimeProfile) -> list[str]:
        """Format all devices in a profile."""
        
        if not profile or not profile.nodes or not profile.nodedef:
            return []
        
        if not self.json_output:
            raise ValueError("JSON output must be enabled to format profiles with nested structure.")

        out = {
           "id": profile.nodedef.id if profile.nodedef.id else "none",
           "props": [],
           "accepts-cmds": [],
           "sends-cmds": [],
           "devices": []
        }

        node_def = self._format_nodedef_json(profile.nodedef)
        if node_def:
            out["props"] = node_def.get("props", [])
            out["accepts-cmds"] = node_def.get("accepts-cmds", [])
            out["sends-cmds"] = node_def.get("sends-cmds", [])

        devices = []
        groups = []
        folders = []
        for node in profile.nodes:
            if node.address:
                if isinstance(node, Group):
                    groups.append({ "id": node.address, "name": node.name})
                elif isinstance(node, Folder):
                    folders.append({ "id": node.address, "name": node.name})
                else:
                    devices.append({ "id": node.address, "name": node.name})
        if len(devices) > 0:
            out["devices"] = devices
        if len(groups) > 0:
            out["groups"] = groups
        if len(folders) > 0:
            out["folders"] = folders
        
        return out

#        formatted_lines = []
#        formatted_json = {
#            "devices": []
#        }

#        for node in profile.nodes:
#            line = self._format_node(node)
#            if line:
#                if self.json_output:
#                    formatted_json["devices"].append(line)
#                else:
#                    formatted_lines.append(line)
#        
#        return formatted_json if self.json_output else formatted_lines

    def format(self, **kwargs) -> RAGData:
        """
        Convert devices into minimal comma-separated format.
        
        :param profiles: optional, dictionary of profiles to format
        :param nodes: optional, dictionary of nodes to format directly
        :param groups: optional, dictionary of groups
        :param folders: optional, dictionary of folders
        :return: RAGData object containing the formatted documents
        """
        self.profiles = kwargs.get("profiles")
        self.nodes = kwargs.get("nodes")
        self.groups = kwargs.get("groups")
        self.folders = kwargs.get("folders")
        
        all_lines = []
        profile_first=True
        
        device_count = 0
        # Format from profiles if available
        if self.profiles:
            all_lines_json = {
                "profiles": []
            }
            for profile in self.profiles.values():
                result = self._format_profile(profile)
                if self.json_output:
                    all_lines_json["profiles"].append(result)
                else:
                    all_lines.extend(result)
                if result.get("devices"):
                    device_count += len(result["devices"])
            deduper = DedupeProfiles()
            all_lines_json = deduper.dedupe(all_lines_json)
        # Otherwise format from nodes directly
        elif self.nodes:
            profile_first=False
            device_count = len(self.nodes)
            all_lines_json = {
                "devices": []
            }
            for node in self.nodes.values():
                line = self._format_node(node)
                if (line):
                    if self.json_output:
                        all_lines_json["devices"].append(line)
                    else:
                        all_lines.append(line)
        
        # Create RAG data structure
        rag_docs = RAGData()


        # Add as single document with all devices
        content = "" 
        if self.json_output:
            if all_lines_json["devices" if not profile_first else "profiles"]:
                content = f"```json\n{json.dumps(all_lines_json)}\n```"
        else:
            if all_lines:
                content = "\n".join(all_lines)
                device_count = len(all_lines)

        
        rag_docs.add_document(
            content,
            None,  # No embeddings
            id="minimal_device_list",
            metadata={"format": "minimal", "device_count": device_count}
        )
        
        return rag_docs

    def format_to_string(self, **kwargs) -> str:
        """
        Convenience method to get formatted output as string.
        
        :param profiles: optional, dictionary of profiles to format
        :param nodes: optional, dictionary of nodes to format directly
        :return: String with one device per line in delimited format
        """
        rag_data = self.format(**kwargs)
        if rag_data.documents:
            return rag_data.documents[0].content
        return ""
