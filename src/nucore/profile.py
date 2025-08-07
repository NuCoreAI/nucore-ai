from dataclasses import dataclass, field
from .editor import Editor
from .linkdef import LinkDef
from .nodedef import NodeDef


@dataclass
class Instance:
    """
    An instance of a family, containing specific configurations
    for editors, link definitions, and node definitions.
    """

    id: str
    name: str
    editors: list[Editor] = field(default_factory=list)
    linkdefs: list[LinkDef] = field(default_factory=list)
    nodedefs: list[NodeDef] = field(default_factory=list)


@dataclass
class Family:
    """
    A family object that groups related instances.
    """

    id: str
    name: str
    instances: list[Instance]


@dataclass
class Profile:
    """
    Defines the overall structure of a profile file, containing
    information about families and instances.
    """

    timestamp: str
    families: list[Family]
