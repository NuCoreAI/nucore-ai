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

``validate_profile`` also independently cross-checks every editor range's
``uom`` against the real UOM table (``_check_uom_consistency``, below) --
``nucore.profile.__build_editor__`` already logs a debug message for a
*nonexistent* uom id, but silently accepts an *existing* uom id paired with
the wrong range shape (e.g. uom 25/"Enum", `nucore.uom.is_enumeration_uom`'s
"index into an enumerated list" uoms, given a ``min``/``max`` range instead
of ``subset``) -- the system prompt tells the model to call ``lookup_uom``
instead of guessing a uom from memory, but that's a request the model can
silently skip (and, if asked about it afterward, confabulate a plausible-
sounding justification for) with no consequence. This check makes the wrong
answer fail mechanically instead of depending on the model remembering.
"""

from __future__ import annotations

import logging
from typing import Any

from nucore import NuCoreError, NuCoreInterface, Profile
from nucore.uom import PREDEFINED_UOMS, get_uom_by_id, is_enumeration_uom


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


def _check_uom_consistency(raw_profile: dict[str, Any]) -> list[str]:
    """Walks every editor range in the catalog-shaped *raw_profile* and
    flags two things ``nucore.profile.__build_editor__`` doesn't:
    - a ``uom`` id that isn't in ``PREDEFINED_UOMS`` at all (that function
      only logs a debug message for this today, same severity as every
      other structural problem it reports -- repeated here as a real error
      so it can't be missed).
    - an *existing* ``uom`` id that's one of ``is_enumeration_uom``'s
      "index into an enumerated list" uoms (25/"Enum", 146/148's
      notification-id variants) paired with a ``min``/``max`` range instead
      of ``subset`` -- structurally valid per nucore's parser (which just
      builds an ``EditorMinMaxRange`` regardless), but not a real uom/range
      pairing that exists in the actual table.
    """
    errors: list[str] = []
    for family in raw_profile.get("families") or []:
        if not isinstance(family, dict):
            continue
        for instance in family.get("instances") or []:
            if not isinstance(instance, dict):
                continue
            for editor in instance.get("editors") or []:
                if not isinstance(editor, dict):
                    continue
                editor_id = editor.get("id") or "?"
                for rng in editor.get("ranges") or []:
                    if not isinstance(rng, dict) or "uom" not in rng:
                        continue
                    uom_id = rng["uom"]
                    uom = get_uom_by_id(uom_id)
                    if uom is None:
                        errors.append(f"editor '{editor_id}': uom '{uom_id}' is not a recognized UOM id")
                        continue
                    if is_enumeration_uom(uom_id) and "subset" not in rng:
                        errors.append(
                            f"editor '{editor_id}': uom '{uom_id}' ({uom.label}) is an index into an "
                            "enumerated list and must use a 'subset' range, not min/max"
                        )
    return errors


async def validate_profile(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    """*nucore_interface* is unused -- this tool never touches the hub, it
    only exercises nucore's local profile parser (plus ``_check_uom_
    consistency``'s own independent cross-check, see module docstring)."""
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

    errors = capture.messages + _check_uom_consistency(raw_profile)
    return {"valid": not errors, "errors": errors}


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
