"""``validate_profile``/``lookup_uom`` -- local, hub-free authoring aids for
a plugin developer working with the Dynamic Profiles JSON format (see
design/developers/plugin_model.md). Both tools exercise ``nucore`` directly
(the same domain-model classes the customer-facing runtime uses to parse a
live profile) rather than reimplementing any parsing/lookup logic here.

See ``nucore/schemas/README.md`` for the JSON Schema equivalent of the shape
``validate_profile`` parses -- not wired in here (this tool still validates
purely via ``Profile().load_from_json(...)`` and its captured debug-log
output, below), just a reusable reference for anyone hand-authoring/vetting
a profile document outside this chat loop.
"""

from __future__ import annotations

import logging
from typing import Any

from nucore import NuCoreError, NuCoreInterface, Profile
from nucore.uom import PREDEFINED_UOMS


class _CaptureHandler(logging.Handler):
    """Collects the ``logger.debug(...)`` problem reports
    ``Profile.__parse_profile__`` emits for malformed input -- it reports
    problems that way instead of raising, so this is the non-invasive way to
    surface them to a tool caller without changing nucore's own parsing
    behavior."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


async def validate_profile(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    """*nucore_interface* is unused -- this tool never touches the hub, it
    only exercises nucore's local profile parser."""
    raw_profile = args.get("profile")
    if not isinstance(raw_profile, dict):
        return {"valid": False, "errors": ["'profile' must be a JSON object"]}

    profile_logger = logging.getLogger("nucore.profile")
    capture = _CaptureHandler()
    profile_logger.addHandler(capture)
    # nucore.profile's own logger level (or an inherited root default of
    # WARNING when the host process never called configure_logging) would
    # otherwise drop these debug() calls before they ever reach our handler
    # -- force DEBUG for the duration of this parse, then restore.
    original_level = profile_logger.level
    profile_logger.setLevel(logging.DEBUG)
    try:
        Profile().load_from_json(raw_profile)
    except NuCoreError as exc:
        return {"valid": False, "errors": [str(exc)]}
    except Exception as exc:
        return {"valid": False, "errors": [f"unexpected error: {exc}"]}
    finally:
        profile_logger.removeHandler(capture)
        profile_logger.setLevel(original_level)

    return {"valid": not capture.messages, "errors": capture.messages}


async def lookup_uom(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    """*nucore_interface* is unused -- this tool never touches the hub."""
    keyword = (args.get("keyword") or "").strip().lower()
    if not keyword:
        return {"error": "keyword is required"}

    matches = [
        {
            "id": entry.id,
            "name": entry.name,
            "label": entry.label,
            "description": entry.description,
            "category_id": entry.category_id,
        }
        for entry in PREDEFINED_UOMS.values()
        if keyword in (entry.name or "").lower()
        or keyword in (entry.label or "").lower()
        or keyword in (entry.description or "").lower()
        or keyword in (entry.category_id or "").lower()
    ]
    return {"matches": matches}
