"""The commissioned developer's identity for one ``plugin_output_root`` --
``generate_plugin_scaffold`` stamps ``email``/``name`` into every generated
``server_entry.json``'s ``developer``/``author`` fields, and (when set)
``default_run_as`` into its ``runAs`` fallback. Pure file I/O, no
per-connection state (unlike ``EvidenceLedger``): the config can change
between calls (re-running ``configure_developer``), and a disk read is
cheap, so re-reading each time is simpler than caching and risking
staleness.
"""

from __future__ import annotations

import json
from typing import Any

from .path_confinement import confine_path

DEVELOPER_CONFIG_FILENAME = "developer_config.json"


def load_developer_config(plugin_output_root: str) -> dict[str, Any] | None:
    """The commissioned developer's ``{email, name, github_url?,
    default_run_as?}``, or ``None`` if no ``developer_config.json`` exists
    yet at *plugin_output_root*'s top level, or if what's there doesn't
    parse into the minimum required shape."""
    path = confine_path(plugin_output_root, DEVELOPER_CONFIG_FILENAME)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get("email") or not data.get("name"):
        return None
    return data


def write_developer_config(
    plugin_output_root: str,
    *,
    email: str,
    name: str,
    github_url: str | None = None,
    default_run_as: str | None = None,
) -> dict[str, Any]:
    """Writes (or overwrites) ``developer_config.json``. The caller (the
    ``configure_developer`` handler) is responsible for any identity
    checking before this is ever called -- this function trusts its
    inputs."""
    path = confine_path(plugin_output_root, DEVELOPER_CONFIG_FILENAME)
    config: dict[str, Any] = {"email": email, "name": name}
    if github_url:
        config["github_url"] = github_url
    if default_run_as:
        config["default_run_as"] = default_run_as
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return config
