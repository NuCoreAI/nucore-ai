"""``create_or_update_routine`` -- authors/edits a routine's if/then/else
logic via the restricted Python-like DSL.

Reuses ``compile_routine_source`` (a pure, stateless ast-based compiler with
no dependency on ``BaseIntentHandler`` or any handler class -- see
``intent_handler_directory/routine_automation/routine_compiler.py``'s own
module docstring) and ``NuCoreInterface.create_automation_routine``/
``update_routine``, exactly as the existing handler does. Does not import
or invoke the ``routine_automation`` handler class itself.

Known v1 gap: this DSL needs real property/command ids and uom/precision
(see tool_create_or_update_routine.json's description) -- fidelity the
compact DEVICE DATABASE deliberately does not carry in this design (that
detail was deferred, see design/design.md decision #5). Until a detail-fetch
tool is added, the model can only write correct routine code for
ids/uom/precision the customer states explicitly or that appear elsewhere
in conversation -- otherwise it should ask for clarification rather than
guess, exactly as the tool description instructs.
"""

from __future__ import annotations

from typing import Any

from intent_handler_directory.routine_automation.routine_compiler import (
    RoutineCompileError,
    compile_routine_source,
)
from nucore import NuCoreInterface


async def create_or_update_routine(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    name = args.get("name")
    routine_id = args.get("id")
    comment = args.get("comment")
    code = args.get("code")
    if not name or not code:
        return {"error": "name and code are both required"}

    try:
        compiled = compile_routine_source(name=name, routine_id=routine_id, comment=comment, source=code)
    except RoutineCompileError as exc:
        return {"error": str(exc)}

    try:
        if routine_id:
            await nucore_interface.update_routine(compiled)
        else:
            await nucore_interface.create_automation_routine(compiled)
    except Exception as exc:
        return {"error": f"failed to save routine: {exc}"}

    return {"name": name, "id": routine_id, "status": "saved"}
