"""The only door into mars-core: its stable facade `mars.api` (plan §4; the core bumps API_VERSION on breaking changes).

Detailed views still read the run artifacts directly, which the contract (§4.3) allows as read-only.
"""
from __future__ import annotations

import functools
import importlib
import inspect
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

SUPPORTED_API_VERSIONS = {3}  # 3: projects only; metrics, checks and gates declared by the project


class CoreUnavailable(RuntimeError):
    pass


_api: ModuleType | None = None


def load(core_root: Path) -> ModuleType:
    """Import mars.api from the configured core checkout (once per process)."""
    global _api
    if _api is not None:
        return _api
    if not (core_root / "mars/api.py").is_file():
        raise CoreUnavailable(f"找不到内核门面 {core_root / 'mars/api.py'}")
    root = str(core_root)
    if root not in sys.path:
        sys.path.insert(0, root)
    module = importlib.import_module("mars.api")
    version = getattr(module, "API_VERSION", None)
    if version not in SUPPORTED_API_VERSIONS:
        raise CoreUnavailable(f"内核 API_VERSION={version}，本应用支持 {sorted(SUPPORTED_API_VERSIONS)}，请先升级 mars-app")
    _api = module
    return module


class Scoped:
    """mars.api bound to one project folder: every function that takes `project_root` gets it, so a project's runs,
    candidates and evaluations stay in that folder. project_root None = the core's MARS_DEFAULT_PROJECT."""

    def __init__(self, module: ModuleType, project_root: str | None) -> None:
        self._module, self.project_root = module, project_root

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._module, name)
        if self.project_root is None or not callable(value):
            return value
        try:
            accepts = "project_root" in inspect.signature(value).parameters
        except (TypeError, ValueError):
            accepts = False
        return functools.partial(value, project_root=self.project_root) if accepts else value


def supports_projects(module: ModuleType) -> bool:
    """Core with independent project execution (inspect_project and project_root on every call)."""
    return hasattr(module, "inspect_project")


def events(api: ModuleType, run_id: str, since: int = 0) -> list[dict[str, Any]]:
    """api.events with each event's line index (`since` counts events already consumed; a half-written last line is
    held back by the core and returned on a later call, so indices stay consistent)."""
    return [{"index": since + offset, **event} for offset, event in enumerate(api.events(run_id, since))]
