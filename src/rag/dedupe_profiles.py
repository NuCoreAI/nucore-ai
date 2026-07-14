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
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

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

            prefix = {"props": "props", "accepts-cmds": "accepts", "sends-cmds": "sends"}[section]
            collections = {}
            used_ids: set[str] = set()
            for key, pids in sorted(groups.items(), key=lambda x: -len(x[1])):
                if len(pids) < 2 or not key or key == ("",):
                    continue
                # Skip empty item lists
                if all(s == "[]" for s in key):
                    continue
                # Name the collection after the shortest (then alphabetically first)
                # profile id that uses it -- readable, and reuses an identifier the
                # reader already recognizes from PROFILES instead of a bare counter.
                anchor = min(pids, key=lambda p: (len(p), p))
                coll_id = DedupeProfiles._unique_id(f"{prefix}_like_{anchor}", used_ids)
                used_ids.add(coll_id)
                collections[key] = {
                    "id": coll_id,
                    "items": items_by_key[key],
                    "profile_ids": pids,
                }
            result[section] = collections
        return result

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
    def build_enum_lookup(collections: dict, profiles: list[dict]) -> tuple[dict, dict]:
        """
        Find items with >MIN_ENUMS that appear across all content (collections + extras).
        Returns (enum_defs, enum_lookup) where:
        - enum_defs: {section: {id: definition}} for the shared section
        - enum_lookup: {canon_json: ref_id}
        """
        enum_defs: dict[str, dict] = {s: {} for s in SECTIONS}
        enum_lookup: dict[str, str] = {}
        used_ids: set[str] = set()

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
                    # Each item's own display name (e.g. "Ramp Rate") already
                    # tells a reader what the enum is -- use it instead of a
                    # bare per-section counter.
                    name = next(iter(item))
                    ref_id = DedupeProfiles._unique_id(f"{DedupeProfiles._slugify(name)}_enum", used_ids)
                    used_ids.add(ref_id)
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
    def _build_profile_collection_maps(collections: dict) -> tuple[dict, dict]:
        """
        Build profile_id -> {section: collection_id} and
        profile_id -> {section: set of canon items in that collection}.
        """
        profile_collection_map: dict[str, dict[str, str]] = defaultdict(dict)
        profile_collection_items: dict[str, dict[str, set]] = defaultdict(
            lambda: defaultdict(set)
        )

        for section, sec_collections in collections.items():
            for key, coll in sec_collections.items():
                for pid in coll["profile_ids"]:
                    profile_collection_map[pid][section] = coll["id"]
                    profile_collection_items[pid][section] = set(key)

        return profile_collection_map, profile_collection_items

    @staticmethod
    def _dedupe(data: dict) -> dict:
        profiles = data.get("profiles", [])
        folders = data.get("folders", [])

        # Step 1: Find per-section collections (exact full-set matches)
        collections = DedupeProfiles.build_collections(profiles)

        # Step 2: Find large-enum items for individual dedup
        enum_defs, enum_lookup = DedupeProfiles.build_enum_lookup(collections, profiles)

        profile_collection_map, profile_collection_items = (
            DedupeProfiles._build_profile_collection_maps(collections)
        )

        # Build shared section
        shared_section = {
            "_schema": (
                "This JSON describes device, group, and folder profiles with shared structure to reduce repetition.\n"
                "\n"
                "TOP-LEVEL KEYS:\n"
                "  shared    — Lookup tables for collections and enums (defined once, referenced many times)\n"
                "  profiles  — Array of device, group, and folder profiles, each with props, accepts-cmds, sends-cmds, and devices and group\n"
                "  "
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

        return {"shared": shared_section, "profiles": new_profiles, "folders": folders}

    # ------------------------------------------------------------------
    # Python-literal rendering (alternative to json.dumps for the deduped dict)
    # ------------------------------------------------------------------

    PYTHON_LEGEND = (
        "# Device/group/folder inventory as Python literals (dict/list/tuple only --\n"
        "# parseable with ast.literal_eval). Same information a JSON profiles/shared\n"
        "# structure would hold, just without repeating field names on every row.\n"
        "#\n"
        "# COLLECTIONS: id -> list of items shared by 2+ profiles. Ids are named\n"
        "#   '{section}_like_{a_profile_id_that_uses_it}', section is props/accepts/sends\n"
        "#   -- e.g. 'accepts_like_DimmerLampSwitch_ADV'.\n"
        "# ENUMS: id -> (name, values) -- large value lists, referenced by id.\n"
        "#   Ids are named '{slugified_item_name}_enum', e.g. 'ramp_rate_enum'.\n"
        "#\n"
        "# PROFILES: id -> a dict with any of the following keys. One entry per\n"
        "#   device/group TYPE, with its own physical device/group instances nested\n"
        "#   directly inside it (NOT a separate table) -- a profile's properties stay\n"
        "#   physically adjacent to the devices that have them.\n"
        "#   'extends': [collection_id, ...] -- substitute every item in\n"
        "#     COLLECTIONS[collection_id], one entry per props/accepts/sends collection\n"
        "#     this profile uses (its section is implied by the id's own prefix).\n"
        "#   'props'/'accepts'/'sends': [item, ...] -- inline items for that section, IN\n"
        "#     ADDITION to anything already pulled in via 'extends'. Each item is either:\n"
        "#       (name, values)      -- an inline property/command, values is a list\n"
        "#       ('$ref', enum_id)   -- substitute ENUMS[enum_id]\n"
        "#   'devices': list of (kind, device_id, name, parent_id, parent_type) tuples.\n"
        "#     kind is 'device' or 'group'. parent_type is folder/node/group; parent_id\n"
        "#     'none' means top-level (parent_type is '' in that case).\n"
        "#\n"
        "# FOLDERS: id -> (name, parent_id)\n"
    )

    @staticmethod
    def _py_repr_one_item(item: dict) -> str:
        """Render a single {"name": [values]} / {"$ref": id} item as a Python tuple literal."""
        if "$ref" in item:
            return f"('$ref', {item['$ref']!r})"
        (name, values), = item.items()
        return f"({name!r}, {values!r})"

    @staticmethod
    def _split_section(value: Any) -> tuple[str | None, list[str]]:
        """Split a profile's props/accepts-cmds/sends-cmds value into the
        collection id it extends (or None) and its rendered inline items
        (either genuinely inline items, or extras alongside a collection)."""
        if not value:
            return None, []
        if isinstance(value, dict) and "$collection" in value:
            inline = [DedupeProfiles._py_repr_one_item(i) for i in (value.get("extras") or [])]
            return value["$collection"], inline
        if isinstance(value, list):
            return None, [DedupeProfiles._py_repr_one_item(i) for i in value]
        return None, []

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

        collections = shared.get("collections", {})
        if collections:
            lines.append("\nCOLLECTIONS = {")
            for cid, body in collections.items():
                items = ", ".join(DedupeProfiles._py_repr_one_item(i) for i in body.get("items", []))
                lines.append(f"  {cid!r}: [{items}],")
            lines.append("}")

        enums = shared.get("enums", {})
        if enums:
            lines.append("\nENUMS = {")
            for eid, body in enums.items():
                for name, values in body.items():
                    lines.append(f"  {eid!r}: ({name!r}, {values!r}),")
            lines.append("}")

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
                extends: list[str] = []
                entry_parts: list[str] = []
                for section_key, field in (
                    ("props", "props"),
                    ("accepts-cmds", "accepts"),
                    ("sends-cmds", "sends"),
                ):
                    coll_id, inline = DedupeProfiles._split_section(profile.get(section_key))
                    if coll_id:
                        extends.append(coll_id)
                    if inline:
                        entry_parts.append(f"'{field}': [{', '.join(inline)}]")

                if extends:
                    entry_parts.insert(0, f"'extends': {extends!r}")
                entry_parts.append(f"'devices': [{', '.join(devices)}]")
                lines.append(f"  {profile['id']!r}: {{{', '.join(entry_parts)}}},")
            lines.append("}")

        if folders:
            lines.append("\nFOLDERS = {")
            for folder in folders:
                parent_id, _ = DedupeProfiles._split_parent(folder.get("parent", "none"))
                lines.append(f"  {folder['id']!r}: ({folder['name']!r}, {parent_id!r}),")
            lines.append("}")

        return "\n".join(lines)

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


