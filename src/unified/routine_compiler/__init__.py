"""``routine_compiler`` -- DSL compiler for NuCore's ``Trigger`` schema (9
condition types, 18 action types; ``net`` excluded).

A pure ``ast``-based walker, never ``exec``/``eval``, with friendly
:class:`~.errors.TriggerCompileError` messages specific enough to drive an
LLM repair turn.

Importing this package registers every implemented condition/action-family
compiler (see ``conditions/*.py``, ``actions/*.py``) with ``core.py``'s
dispatch tables, then re-exports the two things callers need.
"""

from __future__ import annotations

from . import actions  # noqa: F401 -- registers action-family compilers
from . import conditions  # noqa: F401 -- registers condition-family compilers
from .core import compile_trigger_source
from .errors import TriggerCompileError

__all__ = ["compile_trigger_source", "TriggerCompileError"]
