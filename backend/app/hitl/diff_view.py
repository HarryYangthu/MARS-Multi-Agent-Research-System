"""Compute unified diffs for the front-end DiffViewer."""
from __future__ import annotations

import difflib


def unified(left: str, right: str, *, label_left: str = "v1", label_right: str = "v2") -> str:
    diff = difflib.unified_diff(
        left.splitlines(keepends=True),
        right.splitlines(keepends=True),
        fromfile=label_left,
        tofile=label_right,
        n=3,
    )
    return "".join(diff)
