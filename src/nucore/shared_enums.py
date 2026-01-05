"""
A module for shared enumerations used across the IoX project. The purpose is to 
reduce the token count in promptps. So, rather than sending a huge list of enums
to the model, and if and only if these enumerations are not used to disambiguate user 
queries, then we can just send the enum IDs and have the model refer to this module
by calling a tool.
"""
from abc import ABC 
from .editor import Editor

class SharedEnumsBase(ABC):
    
    def __init__(self, shared_enums: dict[Editor]):
        self.shared_enums = shared_enums
        self.level = 0 
        self.lines = [] 
        self.indent_str = " "
    
    def is_shared(self, enum_id: str) -> bool:
        return enum_id in self.shared_enums
    
    def is_set(self, enum_id: str) -> bool:
        return self.is_shared(enum_id) and self.shared_enums[enum_id] is not None
    
    def set_editor(self, enum_id: str, editor: Editor):
        if not self.is_shared(enum_id):
            raise ValueError(f"Enum ID {enum_id} is not shared.")
        self.shared_enums[enum_id] = editor

    def block(self, level_increase: int = 2):
        class BlockContext:
            def __init__(self, writer: SharedEnumsBase):
                self.writer = writer

            def __enter__(self):
                self.writer.level += level_increase

            def __exit__(self, exc_type, exc_val, exc_tb):
                self.writer.level -= level_increase

        return BlockContext(self)
    
    def write(self, line: str=""):
        indent = self.indent_str * self.level
        self.lines.append(f"{indent}{line}")

    def get_enum_names(self, enum_id: str, is_for_prompt: bool) -> str:
        if not self.is_shared(enum_id):
            return None
        if is_for_prompt:
            self.shared_enums[enum_id].write_prompt_section(self)
        else:
            self.shared_enums[enum_id].write_description(self)
        out = "\n".join(self.lines)
        self.lines = []
        return out
    
    def get_all_enum_sections(self) -> str:
        out = ""
        for enum_id in self.shared_enums:
            if self.is_set(enum_id):
                self.shared_enums[enum_id].write_prompt_section(self)
                out += "\n\n".join(self.lines)
                self.lines = []
        return out  

