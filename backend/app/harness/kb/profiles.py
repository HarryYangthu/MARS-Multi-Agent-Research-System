"""Project-scoped memory profiles such as current baseline."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.settings import repo_root


def profiles_root(base: Path | None = None) -> Path:
    root = (base or repo_root() / "knowledge") / "profiles"
    root.mkdir(parents=True, exist_ok=True)
    return root


def profile_path(project: str, name: str, *, base: Path | None = None) -> Path:
    safe_project = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in project)
    safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)
    return profiles_root(base) / f"{safe_project}.{safe_name}.json"


def read_profile(project: str, name: str, *, base: Path | None = None) -> dict[str, Any] | None:
    path = profile_path(project, name, base=base)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return raw if isinstance(raw, dict) else None


def write_profile(
    project: str,
    name: str,
    payload: dict[str, Any],
    *,
    base: Path | None = None,
) -> Path:
    path = profile_path(project, name, base=base)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_baseline_current(project: str, *, base: Path | None = None) -> dict[str, Any] | None:
    return read_profile(project, "baseline_current", base=base)


def write_baseline_current(
    project: str, payload: dict[str, Any], *, base: Path | None = None
) -> Path:
    return write_profile(project, "baseline_current", payload, base=base)
