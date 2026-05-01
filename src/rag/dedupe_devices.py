#!/usr/bin/env python3
"""
Device Editor Deduper

Reads device data in ===Device=== delimited format with full JSON properties,
extracts shared/duplicate editor definitions into a ===Collections=== section,
and rewrites each device to reference collections via $ref.

Usage:
    python dedupe_device_editors.py <input_file> [output_file]
"""

import json
import re
import sys
import copy
from collections import OrderedDict
from utils import get_logger

logger = get_logger(__name__)

class DedupeDevices:
    def __init__(self):
        pass

    @staticmethod
    def canonical_json(obj):
        """Stable JSON string for fingerprinting."""
        return json.dumps(obj, sort_keys=True, separators=(',', ':'))


    @staticmethod
    def generate_name(editor, ctx_id, used_names):
        """Generate a descriptive collection name from editor content and first-seen context."""
        uom_label = editor.get("uom_label", "").lower().replace("%", "pct").replace(" ", "_")
        base = f"{ctx_id}_{uom_label}" if ctx_id else uom_label
        base = re.sub(r'[^a-zA-Z0-9_]', '_', base).strip('_')

        name = base
        i = 2
        while name in used_names:
            name = f"{base}_{i}"
            i += 1
        return name


    @staticmethod
    def collect_editors(device):
        """Yield (editor_dict, context_id, context_name) for every editor in a device."""
        for prop in device.get("Properties", []):
            for ed in prop.get("editors", []):
                yield ed, prop.get("id", ""), prop.get("name", "")

        for section in ("Accepts Commands", "Sends Commands"):
            for cmd in device.get(section, []):
                for param in cmd.get("parameters", []):
                    for ed in param.get("editors", []):
                        yield ed, cmd.get("id", ""), cmd.get("name", "")


    @staticmethod
    def _dedupe_commands(modified, collections, used_names, min_occurrences=2):
        """
        Group commands that co-occur across devices into shared command collections.
        Instead of one $ref per command, creates a single collection array of commands
        shared by multiple devices, so each device references the group + its unique commands.
        """
        for section in ("Accepts Commands", "Sends Commands"):
            # Build a map: command_key -> set of device indices that have it
            cmd_to_devices = {}
            cmd_obj = {}
            for dev_idx, dev in enumerate(modified):
                for cmd in dev.get(section, []):
                    key = DedupeDevices.canonical_json(cmd)
                    if key not in cmd_to_devices:
                        cmd_to_devices[key] = set()
                        cmd_obj[key] = cmd
                    cmd_to_devices[key].add(dev_idx)

            # Group commands by their device signature (frozenset of device indices)
            sig_to_keys = {}
            for key, dev_set in cmd_to_devices.items():
                if len(dev_set) >= min_occurrences:
                    sig = frozenset(dev_set)
                    if sig not in sig_to_keys:
                        sig_to_keys[sig] = []
                    sig_to_keys[sig].append(key)

            # For each signature group, create a shared collection (ordered by first appearance)
            sig_to_name = {}
            for sig, keys in sig_to_keys.items():
                # Preserve original order: sort keys by first device index, then position in that device
                first_dev = min(sig)
                dev_cmds = modified[first_dev].get(section, [])
                dev_keys_ordered = [DedupeDevices.canonical_json(c) for c in dev_cmds]
                keys_sorted = sorted(keys, key=lambda k: dev_keys_ordered.index(k) if k in dev_keys_ordered else len(dev_keys_ordered))

                cmd_list = [cmd_obj[k] for k in keys_sorted]
                prefix = "shared_accepts" if section == "Accepts Commands" else "shared_sends"
                base = prefix
                name = base
                i = 2
                while name in used_names:
                    name = f"{base}_{i}"
                    i += 1
                used_names.add(name)
                collections[name] = cmd_list
                sig_to_name[sig] = name

            # Replace matching commands in each device with a single $ref + remaining unique commands
            shared_keys = set(cmd_to_devices.keys()) & {k for keys in sig_to_keys.values() for k in keys}
            for dev_idx, dev in enumerate(modified):
                if section not in dev:
                    continue
                new_cmds = []
                refs_added = set()
                for cmd in dev[section]:
                    key = DedupeDevices.canonical_json(cmd)
                    if key in shared_keys:
                        # Find which signature group this command belongs to
                        for sig, keys in sig_to_keys.items():
                            if key in keys and dev_idx in sig:
                                ref_name = sig_to_name[sig]
                                if ref_name not in refs_added:
                                    new_cmds.append({"$ref": ref_name})
                                    refs_added.add(ref_name)
                                break
                        else:
                            new_cmds.append(cmd)  # not in any group for this device
                    else:
                        new_cmds.append(cmd)
                dev[section] = new_cmds


    @staticmethod
    def replace_editors(device, key_to_name):
        """Replace editors in-place with {"$ref": name} where applicable."""
        for prop in device.get("Properties", []):
            if "editors" in prop:
                for i, ed in enumerate(prop["editors"]):
                    key = DedupeDevices.canonical_json(ed)
                    if key in key_to_name:
                        prop["editors"][i] = {"$ref": key_to_name[key]}

        for section in ("Accepts Commands", "Sends Commands"):
            for cmd in device.get(section, []):
                for param in cmd.get("parameters", []):
                    if "editors" in param:
                        for i, ed in enumerate(param["editors"]):
                            key = DedupeDevices.canonical_json(ed)
                            if key in key_to_name:
                                param["editors"][i] = {"$ref": key_to_name[key]}


    @staticmethod
    def parse_devices(content):
        """Parse ===Device=== delimited content into a list of device JSON dicts."""
        devices = []
        chunks = re.split(r'===Device===\s*', content)
        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk:
                continue
            m = re.search(r'```json\s*\n?(.*?)\n?\s*```', chunk, re.DOTALL)
            if m:
                try:
                    devices.append(json.loads(m.group(1).strip()))
                except json.JSONDecodeError as e:
                    logger.warning(f"skipping bad JSON: {e}")
        return devices

    @staticmethod
    def _dedupe(devices, min_occurrences=2):
        """
        Extract editors appearing min_occurrences+ times into collections.
        Returns (collections_dict, modified_devices).
        """
        # Phase 1: count every editor occurrence, keep first context for naming
        editor_count = {}
        editor_obj = {}
        editor_ctx = {}

        for dev in devices:
            for ed, ctx_id, ctx_name in DedupeDevices.collect_editors(dev):
                key = DedupeDevices.canonical_json(ed)
                editor_count[key] = editor_count.get(key, 0) + 1
                if key not in editor_obj:
                    editor_obj[key] = ed
                    editor_ctx[key] = (ctx_id, ctx_name)

        # Phase 2: build collections (most frequent first)
        collections = OrderedDict()
        key_to_name = {}
        used_names = set()

        for key, count in sorted(editor_count.items(), key=lambda x: -x[1]):
            if count >= min_occurrences:
                ed = editor_obj[key]
                ctx_id, _ = editor_ctx[key]
                name = DedupeDevices.generate_name(ed, ctx_id, used_names)
                used_names.add(name)
                collections[name] = ed
                key_to_name[key] = name

        # Phase 3: deep-copy devices and replace editors with $ref
        modified = []
        for dev in devices:
            d = copy.deepcopy(dev)
            DedupeDevices.replace_editors(d, key_to_name)
            modified.append(d)

        # Phase 4: group shared commands into union collections
        DedupeDevices._dedupe_commands(modified, collections, used_names, min_occurrences)

        return collections, modified

    @staticmethod
    def compact_device_json(device):
        """Format a device JSON in the original compact style (one property/command per line)."""
        lines = []
        lines.append('{' + f'"name":"{device["name"]}","id":"{device["id"]}"')

        # Properties
        if device.get("Properties"):
            lines[-1] += ','
            lines.append('  "Properties":[')
            for i, prop in enumerate(device["Properties"]):
                comma = "," if i < len(device["Properties"]) - 1 else ""
                lines.append(f'  {json.dumps(prop, separators=(",", ":"))}{comma}')
            lines.append('  ]')

        # Accept Commands
        if device.get("Accepts Commands"):
            lines[-1] += ',"Accepts Commands":['
            for i, cmd in enumerate(device["Accepts Commands"]):
                comma = "," if i < len(device["Accepts Commands"]) - 1 else ""
                lines.append(f'    {json.dumps(cmd, separators=(",", ":"))}{comma}')
            lines.append('  ]')

        # Send Commands
        if device.get("Sends Commands"):
            lines[-1] += ',"Sends Commands":['
            for i, cmd in enumerate(device["Sends Commands"]):
                comma = "," if i < len(device["Sends Commands"]) - 1 else ""
                lines.append(f'    {json.dumps(cmd, separators=(",", ":"))}{comma}')
            lines.append('  ]')

        # Links Info
        if device.get("Links Info"):
            lines[-1] += ',"Links Info":' + json.dumps(device["Links Info"], separators=(",", ":"))

        lines.append('}')
        return "\n".join(lines)


    @staticmethod
    def format_output(collections, devices):
        """Reassemble the ===Collections=== + ===Device=== delimited output."""
        parts = []
        if collections:
            
            parts.append("===Collections===")
            parts.append("```json")
            # Collections stay pretty-printed since they're the reference definitions
            parts.append(json.dumps(collections, indent=2))
            parts.append("```")

        for dev in devices:
            parts.append("===Device===")
            parts.append("```json")
            parts.append(DedupeDevices.compact_device_json(dev))
            parts.append("```")

        return "\n".join(parts) + "\n"


    def dedupe(self, content:dict)->dict:
        #with open("/tmp/not-deduped.json", 'w') as f:
        #    f.write(json.dumps(content, indent=2))
        devices = DedupeDevices.parse_devices(content)
        if not devices:
            logger.warning("No devices found.")
            return {}

#        print(f"Parsed {len(devices)} devices", file=sys.stderr)

        collections, modified = DedupeDevices._dedupe(devices)
#        print(f"Extracted {len(collections)} shared editor collections", file=sys.stderr)

        output = DedupeDevices.format_output(collections, modified)

       # orig_size = len(content)
       # new_size = len(output)
       # pct = 100 * (orig_size - new_size) / orig_size if orig_size else 0
       # print(f"Size: {orig_size} -> {new_size} bytes ({pct:.1f}% reduction)", file=sys.stderr)
        return output
