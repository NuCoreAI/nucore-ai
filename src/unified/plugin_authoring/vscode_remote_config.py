"""Whether eisy-ai's VS Code Remote hub-side setup (missing packages +
environment, via ``NuCoreInterface.setup_eisy_ai_vscode_remote``) has been
run successfully for one ``plugin_output_root`` -- shared across every
generated plugin, since the hub-side setup itself isn't plugin-specific.

Deliberately its own file, not folded into ``developer_config.json``:
``write_developer_config`` rebuilds its dict from scratch on every call, so
a ``vscode_remote_enabled`` key stashed there would get silently wiped out
the next time ``configure_developer`` runs.
"""

from __future__ import annotations

import json
from typing import Any

from .path_confinement import confine_path

VSCODE_REMOTE_CONFIG_FILENAME = "vscode_remote_config.json"


def load_vscode_remote_config(plugin_output_root: str) -> dict[str, Any] | None:
    """``{"vscode_remote_enabled": bool}``, or ``None`` if no
    ``vscode_remote_config.json`` exists yet at *plugin_output_root*'s top
    level, or if what's there doesn't parse into a dict."""
    path = confine_path(plugin_output_root, VSCODE_REMOTE_CONFIG_FILENAME)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


def write_vscode_remote_config(plugin_output_root: str, *, enabled: bool) -> dict[str, Any]:
    """Writes (or overwrites) ``vscode_remote_config.json`` with the
    outcome of the most recent ``setup_eisy_ai_vscode_remote`` attempt.
    The caller batches the shared permissions-refresh/files-changed-notify
    hooks itself -- this function is pure file I/O."""
    path = confine_path(plugin_output_root, VSCODE_REMOTE_CONFIG_FILENAME)
    config: dict[str, Any] = {"vscode_remote_enabled": enabled}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return config
