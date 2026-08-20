"""``TriggerCompileError`` -- raised by ``compile_trigger_source`` for any
DSL shape it doesn't recognize. Deliberately the same contract as the old
compiler's ``RoutineCompileError``: the message is written to be relayed to
the model verbatim, specific enough to drive a repair turn rather than a
generic "invalid syntax."
"""

from __future__ import annotations


class TriggerCompileError(Exception):
    """Raised when routine source cannot be translated to the NuCore Trigger schema."""
