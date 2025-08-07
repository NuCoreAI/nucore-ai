from dataclasses import dataclass, field
from .editor import Editor


@dataclass
class LinkParameter:
    """
    Defines a parameter for a link definition.
    """

    id: str
    editor: Editor
    optional: bool = None
    name: str = None


@dataclass
class LinkDef:
    """
    Defines the structure of a link definition (linkdef), used to define
    the properties and parameters of a link between nodes.

    If cmd is True, parameters might not be provided.
    If cmd is False or None, parameters can be a list or None.
    """

    id: str
    protocol: str
    name: str = None
    cmd: bool = None
    format: str = None

    parameters: list[LinkParameter] = field(default_factory=list)

    def json(self):
        # very limited for now 
        return {
            "name": self.name,
        }
