"""confine_path -- shared by read_generated_plugin today and
generate_plugin_scaffold's planned writes later (design/developers/
impl_plan.md Phase 3/4).
"""

from __future__ import annotations

import os

import pytest

from unified.plugin_authoring.path_confinement import confine_path


def test_accepts_a_plain_relative_subdirectory(tmp_path):
    (tmp_path / "plugin_a").mkdir()
    resolved = confine_path(tmp_path, "plugin_a")
    assert resolved == (tmp_path / "plugin_a").resolve()


def test_accepts_a_nested_relative_path(tmp_path):
    (tmp_path / "plugin_a" / "tests").mkdir(parents=True)
    resolved = confine_path(tmp_path, "plugin_a/tests")
    assert resolved == (tmp_path / "plugin_a" / "tests").resolve()


def test_rejects_traversal():
    with pytest.raises(ValueError):
        confine_path("/tmp/does-not-matter", "../etc/passwd")


def test_rejects_traversal_in_the_middle_of_a_path():
    with pytest.raises(ValueError):
        confine_path("/tmp/does-not-matter", "plugin_a/../../etc/passwd")


def test_rejects_an_absolute_path():
    with pytest.raises(ValueError):
        confine_path("/tmp/does-not-matter", "/etc/passwd")


def test_rejects_empty_path():
    with pytest.raises(ValueError):
        confine_path("/tmp/does-not-matter", "")


def test_rejects_a_symlinked_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("nope")

    root = tmp_path / "root"
    root.mkdir()
    os.symlink(outside, root / "escape_link")

    with pytest.raises(ValueError):
        confine_path(root, "escape_link/secret.txt")


def test_rejects_a_symlinked_file_target(tmp_path):
    outside_file = tmp_path / "outside_secret.txt"
    outside_file.write_text("nope")

    root = tmp_path / "root"
    root.mkdir()
    os.symlink(outside_file, root / "innocuous_name.txt")

    with pytest.raises(ValueError):
        confine_path(root, "innocuous_name.txt")
