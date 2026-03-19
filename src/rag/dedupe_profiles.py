#!/usr/bin/env python3
"""
Extracts shared collections of properties, accepts-cmds, and sends-cmds
from device profiles into a shared lookup section.

Strategy:
  For each section (props, accepts-cmds, sends-cmds), find the exact set of
  items shared by 2+ profiles — that becomes a named collection. Each profile
  then references the collection plus any extra items unique to it.

  Items with >3 enumerations are also individually deduplicated within
  collections (and extras) to avoid repeating long enum lists.

Usage:
    python dedupe_profiles.py <input.json> [output.json]
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

MIN_ENUMS = 3  # individually extract items with MORE THAN this many enums
SECTIONS = ("props", "accepts-cmds", "sends-cmds")


class DedupeProfiles:
    def __init__(self):
        pass

    @staticmethod
    def _canon(obj: dict) -> str:
        return json.dumps(obj, sort_keys=True)

    @staticmethod
    def _canon_set(items: list[dict]) -> tuple[str, ...]:
        """Order-independent canonical key for a list of items."""
        return tuple(sorted(DedupeProfiles._canon(i) for i in items))

    @staticmethod
    def _enum_count(item: dict) -> int:
        name = next(iter(item))
        return len(item[name])

    @staticmethod
    def build_collections(profiles: list[dict]) -> dict:
        """
        For each section, group profiles that share the exact same item list.
        Returns {section: {canon_key: {"id": ..., "items": [...], "profile_ids": [...]}}}
        """
        result = {}
        for section in SECTIONS:
            groups: dict[tuple, list[str]] = defaultdict(list)
            items_by_key: dict[tuple, list[dict]] = {}
            for p in profiles:
                items = p.get(section, [])
                key = DedupeProfiles._canon_set(items)
                groups[key].append(p["id"])
                items_by_key[key] = items

            prefix = {"props": "pc", "accepts-cmds": "ac", "sends-cmds": "sc"}[section]
            collections = {}
            idx = 0
            for key, pids in sorted(groups.items(), key=lambda x: -len(x[1])):
                if len(pids) < 2 or not key or key == ("",):
                    continue
                # Skip empty item lists
                if all(s == "[]" for s in key):
                    continue
                idx += 1
                collections[key] = {
                    "id": f"{prefix}_{idx}",
                    "items": items_by_key[key],
                    "profile_ids": pids,
                }
            result[section] = collections
        return result


    @staticmethod
    def build_enum_lookup(collections: dict, profiles: list[dict]) -> tuple[dict, dict]:
        """
        Find items with >MIN_ENUMS that appear across all content (collections + extras).
        Returns (enum_defs, enum_lookup) where:
        - enum_defs: {section: {id: definition}} for the shared section
        - enum_lookup: {canon_json: ref_id}
        """
        PREFIX = {"props": "prop", "accepts-cmds": "acmd", "sends-cmds": "scmd"}
        enum_defs: dict[str, dict] = {s: {} for s in SECTIONS}
        enum_lookup: dict[str, str] = {}
        counters: dict[str, int] = {s: 0 for s in SECTIONS}

        seen: set[str] = set()
        # Scan all profiles (covers both collection and extra items)
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
                    ref_id = f"{PREFIX[section]}_{counters[section]}"
                    enum_defs[section][ref_id] = item
                    enum_lookup[canon] = ref_id

        enum_defs = {k: v for k, v in enum_defs.items() if v}
        return enum_defs, enum_lookup


    @staticmethod
    def replace_enums(items: list[dict], enum_lookup: dict) -> list[dict]:
        """Replace items that have large enums with $ref."""
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
        profiles = data.get("profiles", [])

        # Step 1: Find per-section collections (exact full-set matches)
        collections = DedupeProfiles.build_collections(profiles)

        # Step 2: Find large-enum items for individual dedup
        enum_defs, enum_lookup = DedupeProfiles.build_enum_lookup(collections, profiles)

        # Build collection lookup: profile_id -> {section: collection_id}
        profile_collection_map: dict[str, dict[str, str]] = defaultdict(dict)
        # Also need: profile_id -> {section: set of canon items in collection}
        profile_collection_items: dict[str, dict[str, set]] = defaultdict(
            lambda: defaultdict(set)
        )

        for section, sec_collections in collections.items():
            for key, coll in sec_collections.items():
                for pid in coll["profile_ids"]:
                    profile_collection_map[pid][section] = coll["id"]
                    profile_collection_items[pid][section] = set(key)

        # Build shared section
        shared_section = {
            "_schema": (
                "This JSON describes device profiles with shared structure to reduce repetition.\n"
                "\n"
                "TOP-LEVEL KEYS:\n"
                "  shared    — Lookup tables for collections and enums (defined once, referenced many times)\n"
                "  profiles  — Array of device profiles, each with props, accepts-cmds, sends-cmds, and devices\n"
                "\n"
                "SHARED SECTION:\n"
                "  shared.collections — Named groups of items shared by multiple profiles.\n"
                "    Each collection has an id (e.g. pc_1, ac_2, sc_3) and an 'items' array.\n"
                "    Prefixes: pc_ = props collection, ac_ = accepts-cmds collection, sc_ = sends-cmds collection.\n"
                "\n"
                "  shared.enums — Items with large enumeration lists, stored once and referenced by id.\n"
                "    Prefixes: prop_ = property enum, acmd_ = accepts-cmd enum.\n"
                "\n"
                "HOW TO READ A PROFILE:\n"
                "  Each profile has three item sections: props, accepts-cmds, sends-cmds.\n"
                "  A section can appear in one of these forms:\n"
                "\n"
                '  1. Collection reference:  {"$collection": "pc_1"}\n'
                "     The profile's items for this section are exactly the items in collection pc_1.\n"
                "\n"
                '  2. Collection + extras:   {"$collection": "pc_1", "extras": [...]}\n'
                "     The profile's items = collection pc_1 items UNION the extras array.\n"
                "\n"
                "  3. Inline array:          [{\"On\": []}, {\"Off\": []}]\n"
                "     Items listed directly (no collection matched).\n"
                "\n"
                "  4. Absent/empty:          Section is missing or [] — the profile has none of these.\n"
                "\n"
                "ENUM REFERENCES:\n"
                '  Anywhere you see {"$ref": "prop_1"}, replace it with the definition in shared.enums.prop_1.\n'
                "  This applies inside collections AND inline/extras arrays.\n"
                "\n"
                "ITEM FORMAT:\n"
                '  Each item is {"name": [values]} where name is the property/command name\n'
                "  and values is the list of allowed enumeration values (empty [] means no parameters).\n"
            ),
            "collections": {},
        }

        # Add collections with enum refs applied
        for section in SECTIONS:
            for key, coll in collections[section].items():
                coll_entry = {
                    "items": DedupeProfiles.replace_enums(coll["items"], enum_lookup),
                }
                shared_section["collections"][coll["id"]] = coll_entry

        # Add enum definitions
        if enum_defs:
            shared_section["enums"] = {}
            for section, defs in enum_defs.items():
                shared_section["enums"].update(defs)

        # Build profiles
        new_profiles = []
        for profile in profiles:
            new_profile = {"id": profile["id"]}

            for section in SECTIONS:
                coll_id = profile_collection_map.get(profile["id"], {}).get(section)
                coll_item_canons = profile_collection_items.get(profile["id"], {}).get(
                    section, set()
                )

                all_items = profile.get(section, [])

                if coll_id:
                    # Find extras: items in this profile but not in the collection
                    extras = [
                        i for i in all_items if DedupeProfiles._canon(i) not in coll_item_canons
                    ]
                    new_profile[section] = {"$collection": coll_id}
                    if extras:
                        new_profile[section]["extras"] = DedupeProfiles.replace_enums(
                            extras, enum_lookup
                        )
                else:
                    # No collection match — include items directly
                    replaced = DedupeProfiles.replace_enums(all_items, enum_lookup)
                    if replaced:
                        new_profile[section] = replaced

            # Preserve devices and other fields
            for k, v in profile.items():
                if k not in ("id",) and k not in SECTIONS:
                    new_profile[k] = v

            new_profiles.append(new_profile)

        return {"shared": shared_section, "profiles": new_profiles}


    def dedupe(self, data: dict) -> dict:
        result = DedupeProfiles._dedupe(data)


        # Report
        #orig_size = len(json.dumps(data))
        #new_size = len(json.dumps(result)) 
        #reduction = (1 - new_size / orig_size) * 100
        #colls = result["shared"]["collections"]
        #enums = result["shared"].get("enums", {})

        #print(f"Collections: {len(colls)}")
        #for cid, cdef in colls.items():
        #    print(f"  {cid}: {len(cdef['items'])} items")
        #print(f"Shared enums: {len(enums)}")
        #print(f"Size: {orig_size:,} -> {new_size:,} bytes ({reduction:.1f}% reduction)")
        #print(f"Written to: {output_path}")

        #print(f"Size: {orig_size:,} -> {new_size:,} bytes ({reduction:.1f}% reduction)")
        return result


