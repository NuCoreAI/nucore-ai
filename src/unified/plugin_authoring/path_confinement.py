"""Shared path-confinement check for plugin_authoring's filesystem-touching
tools -- today's readers (handlers/workspace.py's ``read_generated_plugin``)
and the future writer (design/developers/impl_plan.md's Phase 4
``generate_plugin_scaffold``) both need the exact same guarantee: a
caller-supplied relative path must resolve to somewhere inside this
profile's ``plugin_output_root`` (runtime config), never outside it. Written
once here so Phase 4 reuses it instead of re-deriving the same checks.
"""

from __future__ import annotations

import os
from pathlib import Path


def confine_path(root: str | Path, relative: str) -> Path:
    """Resolve *relative* against *root* and return the resolved path, or
    raise ``ValueError`` if it doesn't stay inside *root*.

    Three independent checks, because any one alone is bypassable:

    - Reject a literal ``..`` path component outright, before any
      resolution happens (belt-and-suspenders -- the commonpath check below
      would also catch a successful traversal, but this rejects the
      attempt even if resolution itself would raise for an unrelated
      reason).
    - Reject an absolute *relative* (``Path("/etc/passwd")`` joined onto
      anything is still ``/etc/passwd`` -- ``Path.__truediv__`` doesn't
      protect against this on its own).
    - Resolve both paths (following symlinks) and require
      ``os.path.commonpath`` of the two to equal the resolved root -- this
      is what catches a symlinked parent directory or a symlinked target
      pointing outside the root, not just a textual ``..``.
    """
    if not relative or relative.strip() == "":
        raise ValueError("a path is required")
    if Path(relative).is_absolute():
        raise ValueError(f"'{relative}' must be a relative path")
    if ".." in Path(relative).parts:
        raise ValueError(f"'{relative}' may not contain '..'")

    resolved_root = Path(root).expanduser().resolve()
    resolved_target = (resolved_root / relative).resolve()

    common = os.path.commonpath([str(resolved_root), str(resolved_target)])
    if common != str(resolved_root):
        raise ValueError(f"'{relative}' escapes the allowed root")

    return resolved_target
