"""Assembles the single unified system prompt.

Mirrors the substitution pattern ``IntentRouter.build_router_prompt`` already
uses (``router.py``) -- same compact ``DEVICE DATABASE``/``ROUTINES
DATABASE`` sources, already proven to fit in a system prompt -- but reads
``nucore_interface``/``rag`` directly instead of going through
``IntentHandlerRegistry``'s common-module placeholder expansion, so this
module has no dependency on the router/intent-handler loading machinery.
"""

from __future__ import annotations

from pathlib import Path

from nucore import NuCoreInterface
from rag import DedupeRoutines

_PROMPT_DIR = Path(__file__).parent / "prompt"


async def build_system_prompt(nucore_interface: NuCoreInterface) -> str:
    """Build the complete unified system prompt for the given backend."""
    await nucore_interface._refresh_routines_database()

    system_prompt = (_PROMPT_DIR / "system_prompt.md").read_text(encoding="utf-8").strip()
    definitions = (_PROMPT_DIR / "definitions.md").read_text(encoding="utf-8").strip()

    device_database = (
        nucore_interface.summary_rags.docs_to_string() if nucore_interface.summary_rags else ""
    )
    routines_database = f"```python\n{DedupeRoutines.render_python(nucore_interface.condensed_routines)}\n```"

    prompt = system_prompt.replace("<<definitions>>", definitions)
    prompt = prompt.replace("<<device_database>>", device_database)
    prompt = prompt.replace("<<routines_database>>", routines_database)
    return prompt
