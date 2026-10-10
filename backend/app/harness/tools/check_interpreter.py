"""Resolve the host-configured Python placeholder without relying on login PATH."""
from __future__ import annotations

from collections.abc import Sequence
import os
import shutil
import sys


def check_argv(argv: Sequence[str], *, search_path: str | None = None) -> tuple[str, ...]:
    """Bind the host Python before a child environment changes PATH.

    Call only after checking the original host-configured command allowlist.
    This does not accept model-selected executable substitutions.
    """
    result = tuple(argv)
    if result and result[0] == "python":
        selected = shutil.which("python", path=search_path) or sys.executable
        return (os.path.abspath(selected), *result[1:])
    return result
