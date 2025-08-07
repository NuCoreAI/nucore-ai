import textwrap
from dataclasses import dataclass, field
from .editor import Editor
from .cmd import Command
from .linkdef import LinkDef

#the actual node property that maps to 
@dataclass
class Property:
    id: str
    value: str
    formatted: str
    uom: str
    prec: int = field(default=None)
    name: str = field(default=None)


@dataclass
class NodeProperty:
    """
    Defines attributes and properties of a node.
    """

    id: str
    editor: Editor
    name: str = None
    hide: bool = None

    def __str__(self):
        return f"{self.name}: {self.editor}"

    def json(self):
        return {
            "id": self.id if self.id else "none",
            "name": self.name if self.name else "none",
            "constraints": self.editor.json() if self.editor else "none"
        }
    
@dataclass
class NodeCommands:
    """
    Specifies the commands that a node can send and accept.
    """

    sends: list[Command] = field(default_factory=list)
    accepts: list[Command] = field(default_factory=list)


@dataclass
class NodeLinks:
    """
    Defines control and response link references for a node.
    """

    ctl: list[LinkDef] = field(default_factory=list)
    rsp: list[LinkDef] = field(default_factory=list)


@dataclass
class NodeDef:
    """
    Describes the properties, commands, and links of a node, defining its
    behavior and capabilities within the system.
    """

    id: str
    properties: dict[str, NodeProperty]
    cmds: NodeCommands
    nls: str = None
    icon: str = None
    links: NodeLinks = None

    def __str__(self):
        #s = [f"Node type: {self.id} ({self.nls})"]
        s=[]
        s.append(textwrap.indent("***Properties***", "  "))
        for prop in self.properties:
            s.append(textwrap.indent(str(prop), "  - "))
        s.append(textwrap.indent("***Sends Commands***", "  "))
        for cmd in self.cmds.sends:
            s.append(textwrap.indent(str(cmd), "  - "))
        if len(self.cmds.accepts) > 0:  
            s.append(textwrap.indent("***Accepts Commands***", "  "))
            for cmd in self.cmds.accepts:
                s.append(textwrap.indent(str(cmd), "  - "))

        return "\n".join(s)
