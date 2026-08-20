#!/usr/bin/env python3
"""
Deduplicates enum-heavy items shared across device profiles.

Strategy: profile-level indirection (a separate COLLECTIONS lookup table
that profiles' props/accepts/sends "extend") was measured to cost far more
than it saves once real installation data is used -- it forces the model to
chase a profile -> collection -> (possibly) enum reference chain just to
read a device's capabilities, which is real reliability risk, in exchange
for savings that are almost entirely concentrated in a small number of
enum value lists, not the collections themselves. So there is no
COLLECTIONS table and no 'extends' anymore: every profile's props/accepts/
sends are always inlined directly, in full.

The one thing still worth deduplicating is large enum value lists that are
genuinely shared by multiple profiles (e.g. a 128-entry backlight enum used
by 6 keypad profiles) -- inlining those into every profile that uses them
really does cost meaningfully more than referencing them once. That's the
only indirection left: items with more than MIN_ENUMS enum values, where
(occurrences - 1) * entry_count clears ENUM_SHARE_COST_THRESHOLD, get
pulled into a small ENUMS table and referenced via ('$ref', enum_id);
everything else -- the large majority of items -- is always inline.

Usage:
    python dedupe_profiles.py <input.json> [output.json]
"""

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

MIN_ENUMS = 3  # only consider items with MORE THAN this many enum values for $ref at all
# (occurrences - 1) * entry_count must clear this before an enum is worth
# referencing instead of inlining everywhere it appears -- e.g. an enum
# shared by 3 profiles needs ~10+ entries, one shared by 6 needs ~4+.
ENUM_SHARE_COST_THRESHOLD = 20
SECTIONS = ("props", "accepts-cmds", "sends-cmds")


class DedupeProfiles:
    def __init__(self):
        pass

    @staticmethod
    def _canon(obj: dict) -> str:
        return json.dumps(obj, sort_keys=True)

    @staticmethod
    def _enum_count(item: dict) -> int:
        name = next(iter(item))
        return len(item[name])

    @staticmethod
    def _slugify(name: str) -> str:
        """Turn a display name into a lowercase snake_case identifier fragment."""
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()
        return slug or "item"

    @staticmethod
    def _unique_id(candidate: str, used: set[str]) -> str:
        """Append a numeric suffix if candidate collides with an already-used id."""
        if candidate not in used:
            return candidate
        n = 2
        while f"{candidate}_{n}" in used:
            n += 1
        return f"{candidate}_{n}"


    @staticmethod
    def build_enum_lookup(profiles: list[dict]) -> tuple[dict, dict]:
        """
        Find enum-bearing items (> MIN_ENUMS values) worth referencing
        instead of inlining everywhere they appear.

        "Worth it" is a real cost comparison, not just an occurrence count:
        (occurrences - 1) * entry_count -- the number of extra copies that
        inlining would create, times how expensive each copy is -- must
        clear ENUM_SHARE_COST_THRESHOLD. An item that only ever appears once
        costs nothing extra either way and is never referenced; a huge enum
        appearing many times (the actual expensive case) is. Occurrences are
        counted per raw appearance, not per distinct profile -- a single
        profile referencing the same enum in both its props and accepts
        sections (e.g. a thermostat's Mode is both a readable property and a
        settable command) genuinely duplicates it twice if inlined, exactly
        as much as two different profiles sharing it once each would.

        Returns (enum_defs, enum_lookup) where:
        - enum_defs: {ref_id: item} for the small set of items kept as $ref
        - enum_lookup: {canon_json: ref_id}
        """
        occurrences: dict[str, int] = defaultdict(int)
        item_by_canon: dict[str, dict] = {}
        for profile in profiles:
            for section in SECTIONS:
                for item in profile.get(section, []):
                    if DedupeProfiles._enum_count(item) <= MIN_ENUMS:
                        continue
                    canon = DedupeProfiles._canon(item)
                    item_by_canon[canon] = item
                    occurrences[canon] += 1

        enum_defs: dict[str, dict] = {}
        enum_lookup: dict[str, str] = {}
        used_ids: set[str] = set()
        for canon, occ in occurrences.items():
            item = item_by_canon[canon]
            name = next(iter(item))
            entry_count = len(item[name])
            if (occ - 1) * entry_count < ENUM_SHARE_COST_THRESHOLD:
                continue
            # Each item's own display name (e.g. "Ramp Rate") already tells
            # a reader what the enum is -- use it instead of a bare counter.
            ref_id = DedupeProfiles._unique_id(f"{DedupeProfiles._slugify(name)}_enum", used_ids)
            used_ids.add(ref_id)
            enum_defs[ref_id] = item
            enum_lookup[canon] = ref_id

        return enum_defs, enum_lookup

    @staticmethod
    def replace_enums(items: list[dict], enum_lookup: dict) -> list[dict]:
        """Replace items whose enum list was kept in ENUMS with $ref;
        everything else stays inline."""
        result = []
        for item in items:
            canon = DedupeProfiles._canon(item)
            if canon in enum_lookup:
                result.append({"$ref": enum_lookup[canon]})
            else:
                result.append(item)
        return result

    @staticmethod
    def _dedupe(data: dict) -> dict:
        """Resolve every profile's props/accepts-cmds/sends-cmds fully
        inline -- no COLLECTIONS table, no 'extends' -- except for the small
        set of enum value lists :meth:`build_enum_lookup` determines are
        genuinely worth referencing instead of repeating (see module
        docstring)."""
        profiles = data.get("profiles", [])
        folders = data.get("folders", [])

        enum_defs, enum_lookup = DedupeProfiles.build_enum_lookup(profiles)
        shared_section = {"enums": enum_defs} if enum_defs else {}

        # Build profiles: every section is always inlined directly now.
        new_profiles = []
        for profile in profiles:
            new_profile = {"id": profile["id"]}

            for section in SECTIONS:
                replaced = DedupeProfiles.replace_enums(profile.get(section, []), enum_lookup)
                if replaced:
                    new_profile[section] = replaced

            # Preserve devices and other fields
            for k, v in profile.items():
                if k not in ("id",) and k not in SECTIONS:
                    new_profile[k] = v

            new_profiles.append(new_profile)

        return {"shared": shared_section, "profiles": new_profiles, "folders": folders}

    # ------------------------------------------------------------------
    # Python-literal rendering (alternative to json.dumps for the deduped dict)
    # ------------------------------------------------------------------

    PYTHON_LEGEND = (
        "# Device/group/folder inventory as Python literals (dict/list/tuple only --\n"
        "# parseable with ast.literal_eval). Same information a JSON profiles/shared\n"
        "# structure would hold, just without repeating field names on every row.\n"
        "#\n"
        "# PROFILES: id -> a dict with any of the following keys. One entry per\n"
        "#   device/group TYPE, with its own physical device/group instances nested\n"
        "#   directly inside it (NOT a separate table) -- a profile's properties stay\n"
        "#   physically adjacent to the devices that have them.\n"
        "#   'props'/'accepts'/'sends': [item, ...] -- this profile's FULL item list for\n"
        "#     that section, always inline (never split across another table). Each\n"
        "#     item is one of:\n"
        "#       (name, values)      -- a property/command, values is a list of any\n"
        "#                              enum labels it has (empty [] if none) -- to use\n"
        "#                              it, pass the exact label text as the value.\n"
        "#       (name, (min, max))  -- a property/command whose value is a plain\n"
        "#                              number in that range (a unit may apply) --\n"
        "#                              pass a plain number as the value.\n"
        "#       (name, {'on': (min, max), 'off': (min, max)}) -- a command needing\n"
        "#                              two independent numbers -- pass an object\n"
        "#                              {'on': <n>, 'off': <n>} as the value.\n"
        "#       ('$ref', enum_id)   -- substitute ENUMS[enum_id] for this one item's\n"
        "#                              (name, values) -- used only for the handful of\n"
        "#                              large enum lists genuinely shared by several\n"
        "#                              profiles -- everything else is always inlined.\n"
        "#   'devices': list of (kind, device_id, name, parent_id, parent_type) tuples.\n"
        "#     kind is 'device' or 'group'. parent_type is folder/node/group; parent_id\n"
        "#     'none' means top-level (parent_type is '' in that case).\n"
        "#\n"
        "# ENUMS: id -> (name, values) -- only the few large enum value lists shared by\n"
        "#   multiple profiles, referenced via ('$ref', id) above. Ids are named\n"
        "#   '{slugified_item_name}_enum', e.g. 'ramp_rate_enum'.\n"
        "#\n"
        "# FOLDERS: id -> (name, parent_id)\n"
        "#\n"
        "# DISABLED / IN_ERROR: list of device/group ids. Sparse -- only present at all\n"
        "#   if at least one device/group is in that state, and each only lists the\n"
        "#   ids that are. Any id not listed in DISABLED is enabled; any id not listed\n"
        "#   in IN_ERROR is not in error. The two are independent (an id can be in\n"
        "#   neither, either, or both).\n"
    )

    @staticmethod
    def _py_repr_one_item(item: dict) -> str:
        """Render a single {"name": [values]} / {"$ref": id} item as a Python tuple literal."""
        if "$ref" in item:
            return f"('$ref', {item['$ref']!r})"
        (name, values), = item.items()
        return f"({name!r}, {values!r})"

    @staticmethod
    def _split_parent(parent: str) -> tuple[str, str]:
        """Split a "id|type" parent reference into (parent_id, parent_type)."""
        if not parent or parent == "none":
            return "none", ""
        if "|" in parent:
            parent_id, parent_type = parent.split("|", 1)
            return parent_id, parent_type
        return parent, ""

    @staticmethod
    def render_python(result: dict) -> str:
        """Render the ``dedupe()`` output (shared/profiles/folders dict) as
        Python literals instead of JSON -- same information, no per-row field
        repetition, and parseable with ``ast.literal_eval`` for verification.
        Device instances are nested directly inside their own profile's entry
        (not a separate table) so a profile's defining properties stay
        physically adjacent to its device names."""
        shared = result.get("shared", {})
        profiles = result.get("profiles", [])
        folders = result.get("folders", [])
        lines: list[str] = [DedupeProfiles.PYTHON_LEGEND]

        enums = shared.get("enums", {})
        if enums:
            lines.append("\nENUMS = {")
            for eid, body in enums.items():
                for name, values in body.items():
                    lines.append(f"  {eid!r}: ({name!r}, {values!r}),")
            lines.append("}")

        disabled_ids: list[str] = []
        error_ids: list[str] = []

        if profiles:
            lines.append("\nPROFILES = {")
            for profile in profiles:
                devices = []
                for kind, key in (("device", "devices"), ("group", "groups")):
                    for device in profile.get(key, []) or []:
                        parent_id, parent_type = DedupeProfiles._split_parent(device.get("parent", "none"))
                        devices.append(
                            f"({kind!r}, {device['id']!r}, {device['name']!r}, "
                            f"{parent_id!r}, {parent_type!r})"
                        )
                        if device.get("disabled"):
                            disabled_ids.append(device["id"])
                        if device.get("error"):
                            error_ids.append(device["id"])
                entry_parts: list[str] = []
                for section_key, field in (
                    ("props", "props"),
                    ("accepts-cmds", "accepts"),
                    ("sends-cmds", "sends"),
                ):
                    items = profile.get(section_key)
                    if items:
                        rendered = ", ".join(DedupeProfiles._py_repr_one_item(i) for i in items)
                        entry_parts.append(f"'{field}': [{rendered}]")

                entry_parts.append(f"'devices': [{', '.join(devices)}]")
                lines.append(f"  {profile['id']!r}: {{{', '.join(entry_parts)}}},")
            lines.append("}")

        if disabled_ids:
            lines.append(f"\nDISABLED = {disabled_ids!r}")
        if error_ids:
            lines.append(f"\nIN_ERROR = {error_ids!r}")

        if folders:
            lines.append("\nFOLDERS = {")
            for folder in folders:
                parent_id, _ = DedupeProfiles._split_parent(folder.get("parent", "none"))
                lines.append(f"  {folder['id']!r}: ({folder['name']!r}, {parent_id!r}),")
            lines.append("}")

        return "\n".join(lines)

    def dedupe(self, data: dict) -> dict:
        return DedupeProfiles._dedupe(data)


