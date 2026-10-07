"""Resolve the host-configured Python placeholder without relying on login PATH."""
from __future__ import annotations

from collections.abc import Sequence
import shutil
import sys


def check_argv(argv: Sequence[str], *, search_path: str | None = None) -> tuple[str, ...]:
    """Keep explicit executables; a missing `python` uses the running runtime.

    Call only after checking the original host-configured command allowlist.
    This does not accept model-selected executable substitutions.
    """
    result = tuple(argv)
    if result and result[0] == "python" and shutil.which("python", path=search_path) is None:
        return (sys.executable, *result[1:])
    return result
