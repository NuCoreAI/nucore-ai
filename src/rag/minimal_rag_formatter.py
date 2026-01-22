"""
Minimal RAG Formatter
Outputs devices in ultra-compact delimited format for LLM matching.
Format: device_id: device_name | props: p1, p2 | cmds: c1, c2 | enums: e1, e2
"""

from nucore import Node, Group, Folder, RuntimeProfile
from .rag_data_struct import RAGData
from .rag_formatter import RAGFormatter


class MinimalRagFormatter(RAGFormatter):
    """
    Generates minimal delimited device representations.
    Each line: device_id: device_name | props: p1, p2 | cmds: c1, c2 | enums: e1, e2
    """
    
    def __init__(self,json_output:bool=False):
        self.lines = []
        self.profiles = None
        self.nodes = None
        self.groups = None
        self.folders = None

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

    def _format_node(self, node: Node) -> str:
        """Format a single node into delimited string with sections."""
        if not node or not node.node_def:
            return f"{node.address}: {node.name}"
        
        # Collect property names and their enums
        property_names = []
        property_enums = []
        
        for prop in node.node_def.properties:
            if prop.name:
                property_names.append(prop.name)
            # Collect enums from property editors
            if prop.editor:
                property_enums.extend(self._collect_enum_values(prop.editor))
        
        # Collect command names and their parameter enums
        command_names = []
        command_enums = []
        
        for cmd in node.node_def.cmds.accepts:
            if cmd.name:
                command_names.append(cmd.name)
            # Collect enums from command parameters
            if cmd.parameters:
                for param in cmd.parameters:
                    if param.editor:
                        command_enums.extend(self._collect_enum_values(param.editor))
        
        for cmd in node.node_def.cmds.sends:
            if cmd.name:
                command_names.append(cmd.name)
        
        # Combine all enums and remove duplicates
        all_enums = property_enums + command_enums
        unique_enums = []
        seen = set()
        for enum in all_enums:
            if enum not in seen:
                seen.add(enum)
                unique_enums.append(enum)
        
        # Build delimited string
        parts = [node.name]
        
        if property_names:
            parts.append(f"props: {', '.join(property_names)}")
        
        if command_names:
            parts.append(f"cmds: {', '.join(command_names)}")
        
        if unique_enums:
            parts.append(f"enums: {', '.join(unique_enums)}")
        
        return f"{node.address}: {' | '.join(parts)}"

    def _format_profile(self, profile: RuntimeProfile) -> list[str]:
        """Format all devices in a profile."""
        formatted_lines = []
        
        if not profile or not profile.nodes:
            return formatted_lines
        
        for node in profile.nodes:
            line = self._format_node(node)
            formatted_lines.append(line)
        
        return formatted_lines

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
        
        # Format from profiles if available
        if self.profiles:
            for profile in self.profiles.values():
                all_lines.extend(self._format_profile(profile))
        
        # Otherwise format from nodes directly
        elif self.nodes:
            for node in self.nodes.values():
                line = self._format_node(node)
                all_lines.append(line)
        
        # Create RAG data structure
        rag_docs = RAGData()
        
        # Add as single document with all devices
        if all_lines:
            content = "\n".join(all_lines)
            rag_docs.add_document(
                content,
                None,  # No embeddings
                id="minimal_device_list",
                metadata={"format": "minimal", "device_count": len(all_lines)}
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
