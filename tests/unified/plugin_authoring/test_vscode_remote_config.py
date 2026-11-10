"""vscode_remote_config.py's load/write helpers -- the shared, plugin_output_root
-level record of whether NuCoreInterface.setup_eisy_ai_vscode_remote has
already succeeded, consulted by handlers/vscode_debug.py before asking to
run it again.
"""

from __future__ import annotations

from unified.plugin_authoring import vscode_remote_config


def test_load_returns_none_when_file_absent(tmp_path):
    assert vscode_remote_config.load_vscode_remote_config(str(tmp_path)) is None


def test_write_then_load_round_trips_enabled_true(tmp_path):
    written = vscode_remote_config.write_vscode_remote_config(str(tmp_path), enabled=True)
    assert written == {"vscode_remote_enabled": True}
    loaded = vscode_remote_config.load_vscode_remote_config(str(tmp_path))
    assert loaded == written


def test_write_then_load_round_trips_enabled_false(tmp_path):
    written = vscode_remote_config.write_vscode_remote_config(str(tmp_path), enabled=False)
    assert written == {"vscode_remote_enabled": False}
    loaded = vscode_remote_config.load_vscode_remote_config(str(tmp_path))
    assert loaded == written


def test_load_ignores_malformed_json(tmp_path):
    (tmp_path / vscode_remote_config.VSCODE_REMOTE_CONFIG_FILENAME).write_text("{not valid json")
    assert vscode_remote_config.load_vscode_remote_config(str(tmp_path)) is None


def test_load_ignores_non_dict_json(tmp_path):
    (tmp_path / vscode_remote_config.VSCODE_REMOTE_CONFIG_FILENAME).write_text("[1, 2, 3]")
    assert vscode_remote_config.load_vscode_remote_config(str(tmp_path)) is None
