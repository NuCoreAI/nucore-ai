from dataclasses import dataclass, field
from .editor import Editor
import textwrap


@dataclass
class CommandParameter:
    """
    Definition of a parameter for a command.
    """

    id: str
    editor: Editor
    name: str | None = None
    init: str | None = None
    optional: bool | None = None

    def __str__(self):
        parts = [f"{self.id} ({self.editor})"]
        if self.name:
            parts.append(f"Name: {self.name}")
        if self.init:
            parts.append(f"Init: {self.init}")
        if self.optional:
            parts.append("Optional")
        return ", ".join(parts).join(" - ").join(self.editor)
    
    def json(self):
        out = {
            "id": self.id,
            "name": self.name,
        }
        out["constraints"] = self.editor.json()
        return out
    
@dataclass
class Command:
    """
    Defines the structure of commands that a node can send or accept.
    """

    id: str
    name: str | None = None
    format: str | None = None
    parameters: list[CommandParameter] = field(default_factory=list)

    def __str__(self):
        s = f"{self.name}"
        if len(self.parameters) > 0:
            s += textwrap.indent("\nParameters:", "--")
            for param in self.parameters:
                s += textwrap.indent(f"\n{param.name}: {param.editor}", "---")
        return s
    
    def json(self):
        return {
            "name": self.name,
            "format": self.format,
            "parameters": [ p.json() for p in self.parameters]
        }
    