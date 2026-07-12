from dataclasses import dataclass, field
from .editor import Editor
import textwrap


@dataclass
class CommandParameter:
    """Definition of a single parameter accepted by a :class:`Command`.

    Attributes:
        id:       Parameter identifier string (e.g. ``"OL"``).
        editor:   :class:`~nucore.editor.Editor` instance that describes the
                  allowed values, range, or enumeration for this parameter.
        name:     Human-readable display name (optional).
        init:     Default/initial value string (optional).
        optional: When ``True`` the parameter may be omitted from a command
                  call (optional).
    """

    id: str
    editor: Editor
    name: str | None = None
    init: str | None = None
    optional: bool | None = None

    def json(self) -> dict:
        """Serialise to a JSON-compatible dict for prompt/tool injection.

        Returns:
            Dict with ``"id"``, ``"name"``, and ``"constraints"`` keys,
            where ``"constraints"`` is the :meth:`~nucore.editor.Editor.json`
            representation of the allowed value space.
        """
        out = {
            "id": self.id,
            "name": self.name,
        }
        out["constraints"] = self.editor.json()
        return out
    
@dataclass
class Command:
    """Defines the structure of a command that a node can send or accept.

    Attributes:
        id:         Command identifier string (e.g. ``"DON"``, ``"DOF"``).
        name:       Human-readable display name (optional).
        format:     Optional format string describing the command payload.
        parameters: Ordered list of :class:`CommandParameter` instances
                    accepted by this command.
    """

    id: str
    name: str | None = None
    format: str | None = None
    parameters: list[CommandParameter] = field(default_factory=list)

    def json(self) -> dict:
        """Serialise to a JSON-compatible dict for prompt/tool injection.

        Returns:
            Dict with ``"name"``, ``"format"``, and ``"parameters"`` keys,
            where ``"parameters"`` is a list of
            :meth:`CommandParameter.json` dicts.
        """
        return {
            "name": self.name,
            "format": self.format,
            "parameters": [p.json() for p in self.parameters],
        }
    