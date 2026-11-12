"""developer_config.py's load/write helpers, plus handlers/developer_config.py's
configure_developer -- the one-time (or update) commissioning step
generate_plugin_scaffold requires before it will write anything.
"""

from __future__ import annotations

import json

import pytest

from unified.plugin_authoring import developer_config
from unified.plugin_authoring.handlers.developer_config import configure_developer, get_developer_config


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
async def test_configure_developer_refuses_when_login_concept_applies_but_nobody_logged_in(tmp_path):
    # get_user_id given but returning None means a login concept applies to
    # this session (e.g. an eisyUI chat before its first context message) --
    # commissioning blind, with no account to verify the email against, is
    # exactly the gap this guard closes.
    result = await configure_developer(
        None, {"email": "dev@example.com", "name": "Dev"}, plugin_output_root=str(tmp_path), get_user_id=lambda: None
    )
    assert "error" in result
    assert developer_config.load_developer_config(str(tmp_path)) is None


@pytest.mark.asyncio
async def test_configure_developer_allows_no_login_concept_at_all(tmp_path):
    # get_user_id itself being None (not merely returning None) means no
    # login concept applies to this session at all -- the MCP/CLI path,
    # which has no context-message mechanism and no user-session notion
    # whatsoever. Commissioning must still work there.
    result = await configure_developer(
        None, {"email": "dev@example.com", "name": "Dev"}, plugin_output_root=str(tmp_path), get_user_id=None
    )
    assert result["configured"] is True
    assert developer_config.load_developer_config(str(tmp_path))["email"] == "dev@example.com"


@pytest.mark.asyncio
async def test_configure_developer_allows_overwrite(tmp_path):
    await configure_developer(None, {"email": "dev@example.com", "name": "Dev"}, plugin_output_root=str(tmp_path))
    result = await configure_developer(
        None, {"email": "dev@example.com", "name": "Dev Updated"}, plugin_output_root=str(tmp_path)
    )
    assert result["configured"] is True
    assert developer_config.load_developer_config(str(tmp_path))["name"] == "Dev Updated"


# --- get_developer_config handler (read-only check-first tool) ---


@pytest.mark.asyncio
async def test_get_developer_config_reports_unconfigured_when_nothing_on_file(tmp_path):
    result = await get_developer_config(None, {}, plugin_output_root=str(tmp_path))
    assert result == {"configured": False}


@pytest.mark.asyncio
async def test_get_developer_config_reports_the_commissioned_identity(tmp_path):
    await configure_developer(
        None,
        {"email": "dev@example.com", "name": "Dev Name", "github_url": "https://github.com/dev"},
        plugin_output_root=str(tmp_path),
    )
    result = await get_developer_config(None, {}, plugin_output_root=str(tmp_path))
    assert result == {
        "configured": True,
        "email": "dev@example.com",
        "name": "Dev Name",
        "github_url": "https://github.com/dev",
    }


@pytest.mark.asyncio
async def test_get_developer_config_never_writes_anything(tmp_path):
    await get_developer_config(None, {}, plugin_output_root=str(tmp_path))
    assert not (tmp_path / developer_config.DEVELOPER_CONFIG_FILENAME).exists()


@pytest.mark.asyncio
async def test_get_developer_config_omits_authenticated_user_id_when_no_login_concept(tmp_path):
    result = await get_developer_config(None, {}, plugin_output_root=str(tmp_path), get_user_id=None)
    assert "authenticated_user_id" not in result


@pytest.mark.asyncio
async def test_get_developer_config_reports_none_when_login_concept_applies_but_not_logged_in(tmp_path):
    result = await get_developer_config(None, {}, plugin_output_root=str(tmp_path), get_user_id=lambda: None)
    assert result["authenticated_user_id"] is None


@pytest.mark.asyncio
async def test_get_developer_config_reports_the_authenticated_email_when_logged_in(tmp_path):
    result = await get_developer_config(
        None, {}, plugin_output_root=str(tmp_path), get_user_id=lambda: "dev@example.com"
    )
    assert result["authenticated_user_id"] == "dev@example.com"
