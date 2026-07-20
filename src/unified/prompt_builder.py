"""Assembles the single unified system prompt.

Reads ``nucore_interface``/``rag`` directly to build the compact
``DEVICE DATABASE``/``ROUTINES DATABASE`` sections -- no config-file/
directory-loading machinery involved.
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
