"""Standing OS/product-identity text for both tool sets' system prompts.

Fetched once, lazily, and cached for the process's lifetime: the real
controller's product/model/OS can't change while this process is running,
the same reasoning ``prompt_builder.py``'s ``_HOST_PLATFORM`` already uses
for the *backend process's* own platform string. A sibling module (not
inside ``unified.prompt_builder``) so ``plugin_authoring``'s own prompt
builder -- deliberately independent of ``unified``'s, see that module's
docstring -- can share this fact without depending on the customer prompt
builder, and so the fetch only ever happens once total rather than once per
tool set.
"""

from __future__ import annotations

import asyncio
from typing import Any

from nucore import NuCoreInterface

_cache: str | None = None
_lock = asyncio.Lock()


def _render(full_config: dict[str, Any] | None) -> str:
    full_config = full_config or {}
    model_name = full_config.get("Model Name") or "unknown"
    model_number = full_config.get("Model Number") or "unknown"
    os_line = full_config.get("OS") or "an unknown OS"
    return (
        f"This installation's real product is {model_name} (model {model_number}), "
        f"running {os_line}. Use these exact facts whenever the product or its OS comes "
        f"up -- never guess or hedge about either one."
    )


async def get_identity_text(nucore_interface: NuCoreInterface) -> str:
    """The rendered OS/product-identity fact block, fetched once and cached
    after that -- safe to call on every prompt build. Only caches once real
    data comes back; a transient backend failure (unreachable at process
    start, say) leaves the cache empty so the next call retries instead of
    freezing in an "unknown" state for the rest of the process's life."""
    global _cache
    if _cache is not None:
        return _cache
    async with _lock:
        if _cache is None:
            try:
                full_config = await nucore_interface.get_full_system_config()
            except Exception:
                full_config = None
            if full_config and (full_config.get("OS") or full_config.get("Model Name")):
                _cache = _render(full_config)
            else:
                return _render(full_config)
    return _cache
