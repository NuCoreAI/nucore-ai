"""``routine_compiler`` -- v2 DSL compiler for the new, richer NuCore
``Trigger`` schema (9 condition types, 18 action types; ``net`` excluded).

Deliberately a brand-new package, not a modification of
``intent_handler_directory/routine_automation/routine_compiler.py`` (the v1
compiler, which stays untouched -- it's shared with the classic,
non-unified intent-handler pipeline, explicitly out of scope for this
rewrite). See that v1 module's docstring for the shared architectural
contract both compilers follow: a pure ``ast``-based walker, never
``exec``/``eval``, with friendly :class:`~.errors.TriggerCompileError`
messages specific enough to drive an LLM repair turn.

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
