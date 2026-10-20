"""Resolve the host-configured Python placeholder without relying on login PATH."""
from __future__ import annotations

from collections.abc import Sequence
import os
import shutil
import sys


def check_argv(argv: Sequence[str], *, search_path: str | None = None) -> tuple[str, ...]:
    """Bind the running host Python before a child environment changes PATH.

    Call only after checking the original host-configured command allowlist.
    This does not accept model-selected executable substitutions.
    A declared search path is an explicit host override. Without one, preserve
    the running venv entrypoint even when the login PATH names a base Python.
    """
    result = tuple(argv)
    if result and result[0] == "python":
        selected = sys.executable
        if search_path is not None:
            selected = shutil.which("python", path=search_path) or sys.executable
        return (os.path.abspath(selected), *result[1:])
    return result
