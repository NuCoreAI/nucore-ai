#!/usr/bin/env python3
"""
Device Editor/Profile Deduper

Reads device data as ```python-fenced ===Device=== blocks (bare dict
literals produced by ProfileRagFormatter.format_device_python), and
factors out what's shared across the batch:

  - Editors referenced by 2+ properties/commands (by their real editor
    id, tagged inline by the producer -- not by re-deriving sharing from
    content hashing, which can't tell two coincidentally-identical
    editors apart from one truly shared editor) go into an EDITORS table,
    referenced by id.
  - Devices that share a profile (2+ devices with the same 'profile'
    nodeDefId necessarily have byte-for-byte identical properties/
    accepts/sends, since they come from the same NodeDef) go into a
    PROFILES table, referenced by id -- mirroring DedupeProfiles' pattern
    on the router side.

Usage:
    python dedupe_devices.py <input_file> [output_file]
"""

import ast
import re
from collections import defaultdict
from utils import get_logger

logger = get_logger(__name__)


class DedupeDevices:
    def __init__(self):
        pass

    @staticmethod
    def parse_devices(content: str) -> list[dict]:
        """Extract and ast.literal_eval each ```python-fenced ===Device=== block."""
        devices = []
        for chunk in re.split(r"===Device===\s*", content):
            chunk = chunk.strip()
            if not chunk:
                continue
            m = re.search(r"```python\s*\n?(\{.*?\})\s*\n?```", chunk, re.DOTALL)
            if not m:
                continue
            try:
                devices.append(ast.literal_eval(m.group(1).strip()))
            except (ValueError, SyntaxError) as e:
                logger.warning(f"skipping unparsable device block: {e}")
        return devices

    @staticmethod
    def _iter_editor_tuples(device: dict) -> list[tuple[list, int, str, list]]:
        """Locate every 4-tuple (name, id, editor_id, editor_dict) in this
        device's properties/accepts/sends (including nested command
        parameters). Returns (container_list, index, editor_id, editor_dict)
        so callers can mutate container[index] in place."""
        locs: list[tuple[list, int, str, list]] = []
        props = device.get("properties")
        if props:
            for i, item in enumerate(props):
                if len(item) == 4:
                    locs.append((props, i, item[2], item[3]))
        for section in ("accepts", "sends"):
            cmds = device.get(section)
            if not cmds:
                continue
            for cmd in cmds:
                if len(cmd) < 3 or not isinstance(cmd[2], list):
                    continue
                params = cmd[2]
                for j, p in enumerate(params):
                    if len(p) == 4:
                        locs.append((params, j, p[2], p[3]))
        return locs

    @staticmethod
    def _replace_editor_refs(devices: list[dict], min_occurrences: int = 2) -> dict:
        """Mutate every 4-tuple editor occurrence in place into a 3-tuple:
        (name, id, editor_id) when the editor is shared by >= min_occurrences
        properties/commands across the whole batch (a $ref into the
        returned EDITORS dict), otherwise (name, id, editor_dict) inline.
        """
        per_device_locs = [DedupeDevices._iter_editor_tuples(d) for d in devices]

        counts: dict[str, int] = {}
        sample: dict[str, list] = {}
        for locs in per_device_locs:
            for _, _, editor_id, editor_dict in locs:
                counts[editor_id] = counts.get(editor_id, 0) + 1
                sample[editor_id] = editor_dict

        editors: dict[str, list] = {}
        for locs in per_device_locs:
            for container, idx, editor_id, editor_dict in locs:
                item = container[idx]
                if counts[editor_id] >= min_occurrences:
                    editors[editor_id] = sample[editor_id]
                    container[idx] = item[:2] + (editor_id,)
                else:
                    container[idx] = item[:2] + (editor_dict,)
        return editors

    @staticmethod
    def _split_profiles(devices: list[dict], min_occurrences: int = 2) -> tuple[dict, list[dict]]:
        """Factor out properties/accepts/sends for any profile shared by
        >= min_occurrences devices in the batch into a PROFILES table
        (devices sharing a profile have identical content there by
        construction -- same NodeDef, no comparison needed). Devices with
        a unique profile in this batch keep their content inline."""
        groups: dict[str, list[dict]] = defaultdict(list)
        for device in devices:
            profile = device.get("profile")
            if profile:
                groups[profile].append(device)

        profiles: dict[str, dict] = {}
        new_devices = []
        for device in devices:
            profile = device.get("profile")
            if profile and len(groups[profile]) >= min_occurrences:
                if profile not in profiles:
                    profiles[profile] = {
                        "properties": device.get("properties") or [],
                        "accepts": device.get("accepts") or [],
                        "sends": device.get("sends") or [],
                    }
                entry = {k: v for k, v in device.items() if k not in ("properties", "accepts", "sends")}
            else:
                entry = dict(device)
            new_devices.append(entry)
        return profiles, new_devices

    @staticmethod
    def format_output(editors: dict, profiles: dict, devices: list[dict]) -> str:
        """Render EDITORS / PROFILES / DEVICES as ```python-fenced blocks."""
        parts = []
        if editors:
            lines = ["EDITORS = {"]
            for eid, edict in editors.items():
                lines.append(f"  {eid!r}: {edict!r},")
            lines.append("}")
            parts.append("```python\n" + "\n".join(lines) + "\n```")

        if profiles:
            lines = ["PROFILES = {"]
            for pid, pdict in profiles.items():
                lines.append(f"  {pid!r}: {pdict!r},")
            lines.append("}")
            parts.append("```python\n" + "\n".join(lines) + "\n```")

        dev_lines = ["DEVICES = {"]
        for device in devices:
            did = device.get("id")
            rest = {k: v for k, v in device.items() if k != "id"}
            dev_lines.append(f"  {did!r}: {rest!r},")
        dev_lines.append("}")
        parts.append("```python\n" + "\n".join(dev_lines) + "\n```")

        return "\n".join(parts) + "\n"

    def dedupe(self, content: str, min_occurrences: int = 2) -> str:
        devices = DedupeDevices.parse_devices(content)
        if not devices:
            logger.warning("No devices found.")
            return content

        editors = DedupeDevices._replace_editor_refs(devices, min_occurrences)
        profiles, devices = DedupeDevices._split_profiles(devices, min_occurrences)

        return DedupeDevices.format_output(editors, profiles, devices)
