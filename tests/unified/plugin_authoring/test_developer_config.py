"""developer_config.py's load/write helpers, plus handlers/developer_config.py's
configure_developer -- the one-time (or update) commissioning step
generate_plugin_scaffold requires before it will write anything.
"""

from __future__ import annotations

import json

import pytest

from unified.plugin_authoring import developer_config
from unified.plugin_authoring.handlers.developer_config import configure_developer


# --- developer_config.py ---


def test_load_returns_none_when_file_absent(tmp_path):
    assert developer_config.load_developer_config(str(tmp_path)) is None


def test_write_then_load_round_trips(tmp_path):
    written = developer_config.write_developer_config(
        str(tmp_path), email="dev@example.com", name="Dev Name", github_url="https://github.com/dev"
    )
    assert written == {"email": "dev@example.com", "name": "Dev Name", "github_url": "https://github.com/dev"}
    loaded = developer_config.load_developer_config(str(tmp_path))
    assert loaded == written


def test_write_without_github_url_omits_the_key(tmp_path):
    written = developer_config.write_developer_config(str(tmp_path), email="dev@example.com", name="Dev Name")
    assert "github_url" not in written
    loaded = developer_config.load_developer_config(str(tmp_path))
    assert "github_url" not in loaded


def test_write_with_default_run_as_round_trips(tmp_path):
    written = developer_config.write_developer_config(
        str(tmp_path), email="dev@example.com", name="Dev Name", default_run_as="polyglot"
    )
    assert written["default_run_as"] == "polyglot"
    loaded = developer_config.load_developer_config(str(tmp_path))
    assert loaded["default_run_as"] == "polyglot"


def test_write_without_default_run_as_omits_the_key(tmp_path):
    written = developer_config.write_developer_config(str(tmp_path), email="dev@example.com", name="Dev Name")
    assert "default_run_as" not in written
    loaded = developer_config.load_developer_config(str(tmp_path))
    assert "default_run_as" not in loaded


def test_load_ignores_malformed_json(tmp_path):
    (tmp_path / developer_config.DEVELOPER_CONFIG_FILENAME).write_text("{not valid json")
    assert developer_config.load_developer_config(str(tmp_path)) is None


def test_load_ignores_missing_required_fields(tmp_path):
    (tmp_path / developer_config.DEVELOPER_CONFIG_FILENAME).write_text(json.dumps({"email": "dev@example.com"}))
    assert developer_config.load_developer_config(str(tmp_path)) is None


# --- configure_developer handler ---


@pytest.mark.asyncio
async def test_configure_developer_requires_email_and_name(tmp_path):
    result = await configure_developer(None, {"email": "", "name": "Dev"}, plugin_output_root=str(tmp_path))
    assert "error" in result

    result = await configure_developer(None, {"email": "dev@example.com", "name": ""}, plugin_output_root=str(tmp_path))
    assert "error" in result


@pytest.mark.asyncio
async def test_configure_developer_requires_a_valid_looking_email(tmp_path):
    result = await configure_developer(None, {"email": "not-an-email", "name": "Dev"}, plugin_output_root=str(tmp_path))
    assert "error" in result


@pytest.mark.asyncio
async def test_configure_developer_requires_matching_authenticated_user(tmp_path):
    result = await configure_developer(
        None,
        {"email": "dev@example.com", "name": "Dev"},
        plugin_output_root=str(tmp_path),
        get_user_id=lambda: "someone-else@example.com",
    )
    assert "error" in result
    assert developer_config.load_developer_config(str(tmp_path)) is None


@pytest.mark.asyncio
async def test_configure_developer_allows_no_authenticated_user_at_all(tmp_path):
    # REPL/CLI mode has no mechanism to ever populate an authenticated
    # identity -- commissioning must still work, since the check only
    # blocks a confirmed mismatch, never the absence of an identity to
    # compare against.
    result = await configure_developer(
        None, {"email": "dev@example.com", "name": "Dev"}, plugin_output_root=str(tmp_path), get_user_id=lambda: None
    )
    assert result["configured"] is True
    assert developer_config.load_developer_config(str(tmp_path))["email"] == "dev@example.com"


@pytest.mark.asyncio
async def test_configure_developer_passes_through_default_run_as(tmp_path):
    result = await configure_developer(
        None,
        {"email": "dev@example.com", "name": "Dev", "default_run_as": "polyglot"},
        plugin_output_root=str(tmp_path),
    )
    assert result["configured"] is True
    assert result["default_run_as"] == "polyglot"
    assert developer_config.load_developer_config(str(tmp_path))["default_run_as"] == "polyglot"


@pytest.mark.asyncio
async def test_configure_developer_allows_overwrite(tmp_path):
    await configure_developer(None, {"email": "dev@example.com", "name": "Dev"}, plugin_output_root=str(tmp_path))
    result = await configure_developer(
        None, {"email": "dev@example.com", "name": "Dev Updated"}, plugin_output_root=str(tmp_path)
    )
    assert result["configured"] is True
    assert developer_config.load_developer_config(str(tmp_path))["name"] == "Dev Updated"
