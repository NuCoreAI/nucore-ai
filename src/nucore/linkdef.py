"""Link definition dataclasses for IoX node-to-node control links.

A ``LinkDef`` describes the protocol, format, and parameters of a directed
link between an ISY controller and a responder.  ``LinkParameter`` describes
a single tunable parameter within that link.
"""

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
    init_val: str = None
    init_uom: int = None

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
    parameters: dict[str, LinkParameter] = field(default_factory=dict)

    def add_parameters(self, parameters: list["LinkParameter"]) -> None:
        """Register a list of :class:`LinkParameter` objects into the internal dict.

        Args:
            parameters: Sequence of :class:`LinkParameter` instances to index
                by their ``id`` attribute.
        """
        for p in parameters:
            self.parameters[p.id] = p

    def json(self) -> dict:
        """Return a minimal JSON-serialisable dict for this link definition.

        Returns:
            A dict with at minimum ``{"name": ...}``.
        """
        return {
            "name": self.name,
        }
