"""Tests for the workspace path guard — pure path logic, no real config touched.

Each test passes ``root=tmp_path`` explicitly so it never depends on the
process cwd or the global config.
"""

from __future__ import annotations

import pytest

from vegapunk.tools.workspace import PathOutsideWorkspace, resolve_in_workspace


def test_resolves_relative_under_root(tmp_path):
    assert resolve_in_workspace("a/b.txt", root=tmp_path) == (tmp_path / "a/b.txt").resolve()


def test_allows_root_itself(tmp_path):
    assert resolve_in_workspace(".", root=tmp_path) == tmp_path.resolve()


def test_rejects_dotdot_escape(tmp_path):
    with pytest.raises(PathOutsideWorkspace):
        resolve_in_workspace("../escape.txt", root=tmp_path)


def test_rejects_absolute_outside_root(tmp_path):
    outside = str(tmp_path.parent / "evil.txt")
    with pytest.raises(PathOutsideWorkspace):
        resolve_in_workspace(outside, root=tmp_path)


def test_allows_absolute_inside_root(tmp_path):
    inside = str(tmp_path / "ok.txt")
    assert resolve_in_workspace(inside, root=tmp_path) == (tmp_path / "ok.txt").resolve()


def test_rejects_symlink_escape(tmp_path):
    # A symlink inside the root that points outside it must still be refused.
    outside = tmp_path.parent / "outside_dir"
    outside.mkdir()
    link = tmp_path / "link"
    link.symlink_to(outside)
    with pytest.raises(PathOutsideWorkspace):
        resolve_in_workspace("link/x.txt", root=tmp_path)
