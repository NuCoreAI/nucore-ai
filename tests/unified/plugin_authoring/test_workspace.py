"""list_generated_plugins/read_generated_plugin (design/developers/
impl_plan.md Phase 3) -- all against real tmp_path fixtures, no mocking
needed since this is local-disk-only.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from unified.plugin_authoring.handlers import workspace


def _write_profile(directory, nodedefs=None):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "profile.json").write_text(
        json.dumps({"editors": [], "nodedefs": nodedefs or [], "linkdefs": []})
    )


@pytest.mark.asyncio
async def test_list_generated_plugins_excludes_directories_without_a_profile(tmp_path):
    _write_profile(tmp_path / "has_profile")
    (tmp_path / "no_profile").mkdir()
    (tmp_path / "no_profile" / "notes.txt").write_text("irrelevant")

    result = await workspace.list_generated_plugins(None, {}, plugin_output_root=str(tmp_path))

    locations = {p["location"] for p in result["plugins"]}
    assert locations == {"has_profile"}


@pytest.mark.asyncio
async def test_list_generated_plugins_sorts_most_recent_first(tmp_path):
    _write_profile(tmp_path / "older")
    time.sleep(0.02)
    _write_profile(tmp_path / "newer")

    result = await workspace.list_generated_plugins(None, {}, plugin_output_root=str(tmp_path))

    assert [p["location"] for p in result["plugins"]] == ["newer", "older"]


@pytest.mark.asyncio
async def test_list_generated_plugins_derives_name_from_first_nodedef(tmp_path):
    _write_profile(tmp_path / "pool_controller", nodedefs=[{"id": "ND_POOL", "name": "Pool Controller"}])

    result = await workspace.list_generated_plugins(None, {}, plugin_output_root=str(tmp_path))

    assert result["plugins"][0]["name"] == "Pool Controller"


@pytest.mark.asyncio
async def test_list_generated_plugins_falls_back_to_readme_then_directory_name(tmp_path):
    with_readme = tmp_path / "with_readme"
    _write_profile(with_readme)
    (with_readme / "README.md").write_text("# My Great Plugin\n\nDoes things.\n")

    no_readme = tmp_path / "no_readme"
    _write_profile(no_readme)

    result = await workspace.list_generated_plugins(None, {}, plugin_output_root=str(tmp_path))
    by_location = {p["location"]: p for p in result["plugins"]}

    assert by_location["with_readme"]["description"] == "My Great Plugin"
    assert by_location["no_readme"]["name"] == "no_readme"
    assert by_location["no_readme"]["description"] is None


@pytest.mark.asyncio
async def test_list_generated_plugins_prefers_context_md_latest_note_over_readme(tmp_path):
    plugin_dir = tmp_path / "with_context"
    _write_profile(plugin_dir)
    (plugin_dir / "README.md").write_text("# Stale Readme Title\n")
    (plugin_dir / "context.md").write_text(
        "## 2026-01-01T00:00:00+00:00\n\nFirst iteration note.\n\n"
        "## 2026-01-02T00:00:00+00:00\n\nLatest iteration note.\n\n"
    )

    result = await workspace.list_generated_plugins(None, {}, plugin_output_root=str(tmp_path))

    assert result["plugins"][0]["description"] == "Latest iteration note."


@pytest.mark.asyncio
async def test_list_generated_plugins_empty_root_returns_empty_list(tmp_path):
    result = await workspace.list_generated_plugins(None, {}, plugin_output_root=str(tmp_path / "does_not_exist"))
    assert result == {"plugins": []}


@pytest.mark.asyncio
async def test_read_generated_plugin_returns_all_files(tmp_path):
    plugin_dir = tmp_path / "pool_controller"
    _write_profile(plugin_dir, nodedefs=[{"id": "ND_POOL", "name": "Pool Controller"}])
    (plugin_dir / "plugin.py").write_text("# the backend")
    (plugin_dir / "README.md").write_text("# Pool Controller\n")
    (plugin_dir / "tests").mkdir()
    (plugin_dir / "tests" / "test_pool.py").write_text("def test_x(): pass")

    result = await workspace.read_generated_plugin(None, {"location": "pool_controller"}, plugin_output_root=str(tmp_path))

    assert result["profile"]["nodedefs"][0]["name"] == "Pool Controller"
    assert result["plugin_py"] == "# the backend"
    assert result["readme"] == "# Pool Controller\n"
    assert result["tests"] == {"test_pool.py": "def test_x(): pass"}


@pytest.mark.asyncio
async def test_read_generated_plugin_missing_optional_files_are_none(tmp_path):
    _write_profile(tmp_path / "bare")

    result = await workspace.read_generated_plugin(None, {"location": "bare"}, plugin_output_root=str(tmp_path))

    assert result["plugin_py"] is None
    assert result["readme"] is None
    assert result["tests"] == {}
    assert result["context"] is None


@pytest.mark.asyncio
async def test_read_generated_plugin_returns_context_md(tmp_path):
    plugin_dir = tmp_path / "pool_controller"
    _write_profile(plugin_dir)
    (plugin_dir / "context.md").write_text("## 2026-01-01T00:00:00+00:00\n\nInitial generation.\n\n")

    result = await workspace.read_generated_plugin(None, {"location": "pool_controller"}, plugin_output_root=str(tmp_path))

    assert "Initial generation." in result["context"]


@pytest.mark.asyncio
async def test_read_generated_plugin_requires_location():
    result = await workspace.read_generated_plugin(None, {}, plugin_output_root="/tmp/whatever")
    assert "error" in result


@pytest.mark.asyncio
async def test_read_generated_plugin_nonexistent_location(tmp_path):
    result = await workspace.read_generated_plugin(None, {"location": "nope"}, plugin_output_root=str(tmp_path))
    assert "error" in result


@pytest.mark.asyncio
async def test_read_generated_plugin_rejects_traversal(tmp_path):
    result = await workspace.read_generated_plugin(
        None, {"location": "../../etc"}, plugin_output_root=str(tmp_path)
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_read_generated_plugin_rejects_a_symlinked_escape(tmp_path):
    outside = tmp_path / "outside"
    _write_profile(outside)

    root = tmp_path / "root"
    root.mkdir()
    os.symlink(outside, root / "linked")

    result = await workspace.read_generated_plugin(None, {"location": "linked"}, plugin_output_root=str(root))
    assert "error" in result


@pytest.mark.asyncio
async def test_read_generated_plugin_malformed_profile_json(tmp_path):
    plugin_dir = tmp_path / "broken"
    plugin_dir.mkdir()
    (plugin_dir / "profile.json").write_text("{not valid json")

    result = await workspace.read_generated_plugin(None, {"location": "broken"}, plugin_output_root=str(tmp_path))
    assert "error" in result
