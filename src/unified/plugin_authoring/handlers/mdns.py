"""``mdns_search``: lets the plugin-authoring LLM search the eisy's local
network over mDNS/Bonjour (a printer, a hub, or "everything that's online")
and see what answered, so it can write a plugin's discovery code against
real data.

The search runs in this process, on the host the assistant's backend runs on
(the eisy), through ``..mdns_client`` -- a stdlib-only mDNS browser, the same
approach the generated ``hp_printer`` plugin uses. This handler only
validates the LLM's arguments, bounds the request, and moves the blocking
socket work off the event loop; it never raises -- a failure becomes an
``{"error": ...}`` dict, same as every other tool handler in this codebase.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from nucore import NuCoreInterface

from .. import mdns_client

DEFAULT_TIMEOUT_SECONDS = 6.0
# Browsing everything needs several rounds (types first, then each type's instances,
# then their hosts/addresses), so it gets a longer default window.
DEFAULT_BROWSE_ALL_TIMEOUT_SECONDS = 10.0
MIN_TIMEOUT_SECONDS = 1.0
MAX_TIMEOUT_SECONDS = 20.0
MAX_SERVICE_TYPES = 10

# "_ipp._tcp", "_hap._tcp.local", or a subtype form like "_universal._sub._ipp._tcp"
_LABEL_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,62}$")


def _normalize_service_type(index: int, value: Any) -> tuple[str | None, str | None]:
    where = f"service_types[{index}]"
    if not isinstance(value, str) or not value.strip():
        return None, f"{where} must be a non-empty string such as '_ipp._tcp'"
    name = value.strip().rstrip(".")
    if name.lower().endswith(".local"):
        name = name[: -len(".local")]
    labels = name.split(".")
    if len(labels) < 2 or labels[-1].lower() not in ("_tcp", "_udp") or not labels[-2].startswith("_"):
        return None, f"{where} must look like '_ipp._tcp' (a service name starting with '_', then '_tcp' or '_udp')"
    if not all(_LABEL_RE.match(label) for label in labels):
        return None, f"{where} contains an invalid label"
    return name + ".local", None


async def mdns_search(nucore_interface: NuCoreInterface | None, args: dict[str, Any]) -> Any:
    """*nucore_interface* is unused -- the search never touches the hub."""
    raw_types = args.get("service_types")
    if raw_types is None or raw_types == []:
        raw_types = []
    elif not isinstance(raw_types, list):
        return {"error": "service_types must be a list of strings such as '_ipp._tcp', or omitted to browse everything"}
    if len(raw_types) > MAX_SERVICE_TYPES:
        return {"error": f"at most {MAX_SERVICE_TYPES} service_types per call, got {len(raw_types)}"}

    service_types: list[str] = []
    for index, raw in enumerate(raw_types):
        normalized, error = _normalize_service_type(index, raw)
        if error:
            return {"error": error}
        if normalized not in service_types:
            service_types.append(normalized)

    timeout = args.get("timeout_seconds")
    if timeout is None:
        timeout = DEFAULT_TIMEOUT_SECONDS if service_types else DEFAULT_BROWSE_ALL_TIMEOUT_SECONDS
    elif isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        return {"error": "timeout_seconds must be a number"}
    timeout = float(min(max(timeout, MIN_TIMEOUT_SECONDS), MAX_TIMEOUT_SECONDS))

    try:
        return await asyncio.to_thread(mdns_client.browse, service_types or None, timeout)
    except Exception as ex:  # a tool handler never raises
        return {"error": f"mDNS search failed: {ex}"}
