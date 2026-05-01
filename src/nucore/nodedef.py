"""Node definition dataclasses for IoX / ISY devices.

Defines the structure of node properties, commands, links, and node
definitions (``NodeDef``) that describe the behaviour and capabilities of
an IoX device node.
"""

import textwrap
from dataclasses import dataclass, field
from .editor import Editor
from .cmd import Command
from .linkdef import LinkDef

#: Maps directly to the live property value reported by the IoX controller.
@dataclass
class Property:
    """A live property value reported by the IoX controller for a specific node.

    Attributes:
        id: Property identifier (e.g. ``"ST"`` for status).
        value: Raw numeric value as a string.
        formatted: Human-readable formatted value (e.g. ``"75.0 °F"``).
        uom: Unit-of-measure identifier string.
        uom_name: Display name for the unit of measure.
        prec: Decimal precision of the value.
        name: Human-readable property name.
    """
    id: str
    value: str
    formatted: str
    uom: str
    uom_name: str = field(default=None)
    prec: int = field(default=None)
    name: str = field(default=None)

    def __str__(self):
        return {
            "id": self.id,
            "value": self.value,
            "formatted": self.formatted,
            "uom": self.uom,
            "uom_name": self.uom_name,
            "prec": self.prec,
            "name": self.name
        }


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

    def __str__(self) -> str:
        """Return a multi-line human-readable summary of the node definition."""
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
    