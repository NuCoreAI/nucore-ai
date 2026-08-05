"""Coverage for the config-driven stream/max_iterations wiring in
runtime_config.py: per-profile 'stream' from JSON, the --stream/--no-stream
CLI-level force override, and the top-level 'max_iterations' field.
"""

from __future__ import annotations

import json

import pytest

from unified.runtime_config import _load_runtime_config
from unified.stream_handler import StreamHandler


def _write_config(tmp_path, **overrides):
    payload = {
        "nucore_runtime": {
            "default": {"provider": "claude", "model": "m"},
            "unified": {"provider": "claude", "model": "m", "stream": True},
        }
    }
    payload.update(overrides)
    path = tmp_path / "runtime_config.json"
    path.write_text(json.dumps(payload))
    return str(path)


def test_profile_stream_flag_honored_when_handler_present(tmp_path):
    path = _write_config(tmp_path)
    cfg = _load_runtime_config(path=path, stream_handler=StreamHandler())

    assert cfg["supported_llms"]["default"]["stream"] is False
    assert cfg["supported_llms"]["unified"]["stream"] is True
    assert callable(cfg["supported_llms"]["unified"]["stream_handler"])


def test_stream_flag_ignored_without_a_handler(tmp_path):
    path = _write_config(tmp_path)
    cfg = _load_runtime_config(path=path, stream_handler=None)

    assert cfg["supported_llms"]["unified"]["stream"] is False


def test_force_stream_false_overrides_profile_flag(tmp_path):
    path = _write_config(tmp_path)
    cfg = _load_runtime_config(path=path, stream_handler=StreamHandler(), force_stream=False)

    assert cfg["supported_llms"]["unified"]["stream"] is False


def test_force_stream_true_overrides_profile_flag(tmp_path):
    path = _write_config(tmp_path)
    cfg = _load_runtime_config(path=path, stream_handler=StreamHandler(), force_stream=True)

    assert cfg["supported_llms"]["default"]["stream"] is True


def test_max_iterations_parsed_from_top_level_config(tmp_path):
    path = _write_config(tmp_path, max_iterations=16)
    cfg = _load_runtime_config(path=path, stream_handler=None)

    assert cfg["max_iterations"] == 16


def test_max_iterations_defaults_to_eight_when_absent(tmp_path):
    path = _write_config(tmp_path)
    cfg = _load_runtime_config(path=path, stream_handler=None)

    assert cfg["max_iterations"] == 8


def test_max_iterations_rejects_non_integer(tmp_path):
    path = _write_config(tmp_path, max_iterations="eight")

    with pytest.raises(ValueError):
        _load_runtime_config(path=path, stream_handler=None)


def test_preferences_dir_parsed_from_top_level_config(tmp_path):
    path = _write_config(tmp_path, preferences_dir="/some/dir")
    cfg = _load_runtime_config(path=path, stream_handler=None)

    assert cfg["preferences_dir"] == "/some/dir"


def test_preferences_dir_has_no_default_when_absent(tmp_path):
    path = _write_config(tmp_path)
    cfg = _load_runtime_config(path=path, stream_handler=None)

    assert cfg["preferences_dir"] is None


def test_preferences_dir_rejects_non_string(tmp_path):
    path = _write_config(tmp_path, preferences_dir=123)

    with pytest.raises(ValueError):
        _load_runtime_config(path=path, stream_handler=None)
