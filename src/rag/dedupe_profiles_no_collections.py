#!/usr/bin/env python3
"""
Extracts large enumerations from device profiles into a shared lookup section,
replacing them with references. This reduces token count for LLM consumption.

Only items with MORE THAN 3 enumerations are extracted:
  - properties with >3 enum values
  - accepts-cmds with parameters having >3 enum values
  - sends-cmds with parameters having >3 enum values

Items with <=3 enumerations stay inline (they're small enough for an LLM to
read in place without wasting significant tokens).

Usage:
    python dedupe_profiles.py <input.json> [output.json]

If output is omitted, writes to <input_deduped.json>.
"""

import json
from utils import get_logger
logger = get_logger(__name__)

MIN_ENUMS = 3  # extract if MORE THAN this many

class DedupeProfiles:
    def __init__(self):
        pass

    @staticmethod
    def _canon(obj: dict) -> str:
        return json.dumps(obj, sort_keys=True)

    @staticmethod
    def _enum_count(item: dict) -> int:
        """Return the number of enum values in an item like {"Ramp Rate": [...]}."""
        name = next(iter(item))
        return len(item[name])

    @staticmethod
    def extract_shared(profiles: list[dict]) -> tuple[dict, dict]:
        """
        Scan all profile sections for items with >MIN_ENUMS values.
        Returns (shared_defs, lookup) where:
        - shared_defs: {section: {id: definition, ...}, ...} for the output
        - lookup: {canonical_json: ref_id} for replacement
        """
        SECTIONS = ("props", "accepts-cmds", "sends-cmds")
        PREFIX = {"props": "prop", "accepts-cmds": "acmd", "sends-cmds": "scmd"}

        shared_defs: dict[str, dict] = {s: {} for s in SECTIONS}
        lookup: dict[str, str] = {}
        counters: dict[str, int] = {s: 0 for s in SECTIONS}

        # Collect unique items with >MIN_ENUMS across all profiles
        seen: set[str] = set()
        for profile in profiles:
            for section in SECTIONS:
                for item in profile.get(section, []):
                    if DedupeProfiles._enum_count(item) <= MIN_ENUMS:
                        continue
                    canon = DedupeProfiles._canon(item)
                    if canon in seen:
                        continue
                    seen.add(canon)

                    counters[section] += 1
                    name = next(iter(item))
                    ref_id = f"{PREFIX[section]}_{counters[section]}"
                    shared_defs[section][ref_id] = item
                    lookup[canon] = ref_id

        # Drop empty sections
        shared_defs = {k: v for k, v in shared_defs.items() if v}
        return shared_defs, lookup

    @staticmethod
    def _dedupe(data: dict) -> dict:
        profiles = data.get("profiles", [])
        shared_defs, lookup = DedupeProfiles.extract_shared(profiles)

        shared_section = {
            "_note": (
                "Items with large enumerations extracted here. "
                "Profiles reference them as {\"$ref\": \"<id>\"}. "
                "Look up the id in the matching section below."
            ),
            **shared_defs,
        }

        ITEM_SECTIONS = ("props", "accepts-cmds", "sends-cmds")
        new_profiles = []
        for profile in profiles:
            new_profile = {"id": profile["id"]}
            for section in ITEM_SECTIONS:
                items = profile.get(section, [])
                new_items = []
                for item in items:
                    canon = DedupeProfiles._canon(item)
                    if canon in lookup:
                        new_items.append({"$ref": lookup[canon]})
                    else:
                        new_items.append(item)
                new_profile[section] = new_items

            # Preserve other fields (devices, etc.)
            for k, v in profile.items():
                if k not in ("id",) and k not in ITEM_SECTIONS:
                    new_profile[k] = v

            new_profiles.append(new_profile)

        return {"shared": shared_section, "profiles": new_profiles}


    def dedupe(self, data: dict) -> dict:

        result = DedupeProfiles._dedupe(data)
        orig_size = len(json.dumps(data))
        new_size = len(json.dumps(result)) 
        reduction = (1 - new_size / orig_size) * 100

        #print(f"Shared: {shared_cmds} cmds, {shared_props} props")
        logger.info(f"Profile dedupe: Size: {orig_size:,} -> {new_size:,} bytes ({reduction:.1f}% reduction)")
        return result


