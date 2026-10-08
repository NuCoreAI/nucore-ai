"""Coverage for the config-driven stream/max_iterations wiring in
runtime_config.py: per-profile 'stream' and 'max_iterations' from JSON, with
no CLI-level override of either -- this file is the only source of truth.
"""

from __future__ import annotations

import json

import pytest

from unified.runtime_config import _load_runtime_config
from unified.stream_handler import StreamHandler


def _write_config(tmp_path, **overrides):
    payload = {
        "nucore_runtime": {
            "unified": {"provider": "claude", "model": "m", "stream": True},
            "other": {"provider": "claude", "model": "m"},
        }
    }
    payload.update(overrides)
    path = tmp_path / "runtime_config.json"
    path.write_text(json.dumps(payload))
    return str(path)


def test_reasoning_effort_passed_through_when_set(tmp_path):
    payload = {
        "nucore_runtime": {
            "unified": {"provider": "openai", "model": "m"},
            "other": {"provider": "openai", "model": "m", "reasoning_effort": "none"},
        }
    }
    path = tmp_path / "runtime_config.json"
    path.write_text(json.dumps(payload))
    cfg = _load_runtime_config(path=str(path), stream_handler=None)

    assert cfg["supported_llms"]["other"]["reasoning_effort"] == "none"
    assert cfg["supported_llms"]["unified"]["reasoning_effort"] is None


def test_cache_ttl_passed_through_and_defaults_to_none(tmp_path):
    payload = {
        "nucore_runtime": {
            "unified": {"provider": "claude", "model": "m"},
            "other": {"provider": "claude", "model": "m", "cache_ttl": "1h"},
        }
    }
    path = tmp_path / "runtime_config.json"
    path.write_text(json.dumps(payload))
    cfg = _load_runtime_config(path=str(path), stream_handler=None)

    assert cfg["supported_llms"]["other"]["cache_ttl"] == "1h"
    assert cfg["supported_llms"]["unified"]["cache_ttl"] is None


def test_cache_ttl_rejects_an_unknown_value(tmp_path):
    payload = {
        "nucore_runtime": {
            "unified": {"provider": "claude", "model": "m", "cache_ttl": "2h"},
        }
    }
    path = tmp_path / "runtime_config.json"
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError):
        _load_runtime_config(path=str(path), stream_handler=None)


def test_profile_stream_flag_honored_when_handler_present(tmp_path):
    path = _write_config(tmp_path)
    cfg = _load_runtime_config(path=path, stream_handler=StreamHandler())

    assert cfg["supported_llms"]["other"]["stream"] is False
    assert cfg["supported_llms"]["unified"]["stream"] is True
    assert callable(cfg["supported_llms"]["unified"]["stream_handler"])


def test_stream_flag_ignored_without_a_handler(tmp_path):
    path = _write_config(tmp_path)
    cfg = _load_runtime_config(path=path, stream_handler=None)

    assert cfg["supported_llms"]["unified"]["stream"] is False


def test_max_iterations_parsed_per_profile(tmp_path):
    payload = {"nucore_runtime": {"unified": {"provider": "claude", "model": "m", "max_iterations": 16}}}
    path = tmp_path / "runtime_config.json"
    path.write_text(json.dumps(payload))
    cfg = _load_runtime_config(path=str(path), stream_handler=None)

    assert cfg["supported_llms"]["unified"]["max_iterations"] == 16


def test_unrecognized_top_level_and_profile_keys_are_silently_ignored(tmp_path):
    # JSON has no comment syntax -- a documentation-only key (e.g. noting a
    # model/param incompatibility discovered in production, see
    # runtime_config.example.json's "_notes") must survive loading
    # unharmed rather than being rejected as an unknown field.
    payload = {
        "_notes": "see design/developers/merged-toolsets.md",
        "nucore_runtime": {
            "unified": {
                "provider": "claude",
                "model": "m",
                "_notes": "claude-sonnet-5 rejects 'temperature' -- see claude_adapter.py",
            }
        },
    }
    path = tmp_path / "runtime_config.json"
    path.write_text(json.dumps(payload))

    cfg = _load_runtime_config(path=str(path), stream_handler=None)

    assert cfg["supported_llms"]["unified"]["provider"] == "claude"


def test_max_iterations_defaults_to_eight_when_absent(tmp_path):
    path = _write_config(tmp_path)
    cfg = _load_runtime_config(path=path, stream_handler=None)

    assert cfg["supported_llms"]["unified"]["max_iterations"] == 8


def test_max_iterations_rejects_non_integer(tmp_path):
    payload = {"nucore_runtime": {"unified": {"provider": "claude", "model": "m", "max_iterations": "eight"}}}
    path = tmp_path / "runtime_config.json"
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError):
        _load_runtime_config(path=str(path), stream_handler=None)


def test_fabrication_guard_mode_defaults_to_log(tmp_path):
    path = _write_config(tmp_path)
    cfg = _load_runtime_config(path=path, stream_handler=None)

    assert cfg["supported_llms"]["unified"]["fabrication_guard_mode"] == "log"


def test_fabrication_guard_mode_parsed_per_profile(tmp_path):
    payload = {
        "nucore_runtime": {"unified": {"provider": "claude", "model": "m", "fabrication_guard_mode": "block"}}
    }
    path = tmp_path / "runtime_config.json"
    path.write_text(json.dumps(payload))
    cfg = _load_runtime_config(path=str(path), stream_handler=None)

    assert cfg["supported_llms"]["unified"]["fabrication_guard_mode"] == "block"


def test_fabrication_guard_mode_rejects_an_unknown_value(tmp_path):
    payload = {
        "nucore_runtime": {"unified": {"provider": "claude", "model": "m", "fabrication_guard_mode": "loud"}}
    }
    path = tmp_path / "runtime_config.json"
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError):
        _load_runtime_config(path=str(path), stream_handler=None)


def test_max_fabrication_retries_defaults_to_one(tmp_path):
    path = _write_config(tmp_path)
    cfg = _load_runtime_config(path=path, stream_handler=None)

    assert cfg["supported_llms"]["unified"]["max_fabrication_retries"] == 1


def test_max_fabrication_retries_parsed_per_profile(tmp_path):
    payload = {
        "nucore_runtime": {"unified": {"provider": "claude", "model": "m", "max_fabrication_retries": 3}}
    }
    path = tmp_path / "runtime_config.json"
    path.write_text(json.dumps(payload))
    cfg = _load_runtime_config(path=str(path), stream_handler=None)

    assert cfg["supported_llms"]["unified"]["max_fabrication_retries"] == 3


def test_max_fabrication_retries_rejects_non_integer(tmp_path):
    payload = {
        "nucore_runtime": {"unified": {"provider": "claude", "model": "m", "max_fabrication_retries": "three"}}
    }
    path = tmp_path / "runtime_config.json"
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError):
        _load_runtime_config(path=str(path), stream_handler=None)


def test_enabled_defaults_to_true(tmp_path):
    path = _write_config(tmp_path)
    cfg = _load_runtime_config(path=path, stream_handler=None)

    assert cfg["supported_llms"]["unified"]["enabled"] is True
    assert cfg["enabled_profiles"] == ["unified", "other"]


def test_enabled_false_excludes_profile_from_enabled_profiles(tmp_path):
    payload = {
        "nucore_runtime": {
            "unified": {"provider": "claude", "model": "m"},
            "plugin_authoring": {
                "provider": "claude",
                "model": "m",
                "enabled": False,
            },
        }
    }
    path = tmp_path / "runtime_config.json"
    path.write_text(json.dumps(payload))
    cfg = _load_runtime_config(path=str(path), stream_handler=None)

    assert cfg["enabled_profiles"] == ["unified"]


def test_at_least_one_profile_must_be_enabled(tmp_path):
    payload = {"nucore_runtime": {"unified": {"provider": "claude", "model": "m", "enabled": False}}}
    path = tmp_path / "runtime_config.json"
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError):
        _load_runtime_config(path=str(path), stream_handler=None)


def test_plugin_authoring_requires_plugin_output_root_when_enabled(tmp_path):
    payload = {
        "nucore_runtime": {
            "unified": {"provider": "claude", "model": "m"},
            "plugin_authoring": {"provider": "claude", "model": "m"},
        }
    }
    path = tmp_path / "runtime_config.json"
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError):
        _load_runtime_config(path=str(path), stream_handler=None)


def test_plugin_authoring_disabled_does_not_require_plugin_output_root(tmp_path):
    payload = {
        "nucore_runtime": {
            "unified": {"provider": "claude", "model": "m"},
            "plugin_authoring": {"provider": "claude", "model": "m", "enabled": False},
        }
    }
    path = tmp_path / "runtime_config.json"
    path.write_text(json.dumps(payload))

    cfg = _load_runtime_config(path=str(path), stream_handler=None)
    assert cfg["enabled_profiles"] == ["unified"]


def test_default_profile_name_prefers_unified_when_both_enabled(tmp_path):
    path = _write_config(tmp_path)
    cfg = _load_runtime_config(path=path, stream_handler=None)

    assert cfg["default_profile_name"] == "unified"


def test_default_profile_name_falls_back_to_the_only_enabled_profile(tmp_path):
    payload = {
        "nucore_runtime": {
            "unified": {"provider": "claude", "model": "m", "enabled": False},
            "plugin_authoring": {
                "provider": "claude",
                "model": "m",
                "plugin_output_root": "/allowed/root",
            },
        }
    }
    path = tmp_path / "runtime_config.json"
    path.write_text(json.dumps(payload))
    cfg = _load_runtime_config(path=str(path), stream_handler=None)

    assert cfg["default_profile_name"] == "plugin_authoring"


def test_max_tool_set_switches_per_turn_defaults_to_one(tmp_path):
    path = _write_config(tmp_path)
    cfg = _load_runtime_config(path=path, stream_handler=None)

    assert cfg["max_tool_set_switches_per_turn"] == 1


def test_max_tool_set_switches_per_turn_parsed_from_top_level_config(tmp_path):
    path = _write_config(tmp_path, max_tool_set_switches_per_turn=3)
    cfg = _load_runtime_config(path=path, stream_handler=None)

    assert cfg["max_tool_set_switches_per_turn"] == 3


def test_search_engine_api_key_passed_through_as_placeholder(tmp_path):
    path = _write_config(tmp_path, search_engine_api_key="${SEARCH_ENGINE_API_KEY}")
    cfg = _load_runtime_config(path=path, stream_handler=None)

    assert cfg["search_engine_api_key"] == "${SEARCH_ENGINE_API_KEY}"
