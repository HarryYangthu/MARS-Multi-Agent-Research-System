"""Project-scoped research experiments sharing one project background.

An Experiment is a named research effort inside a project. It owns no
background material of its own: every experiment reads the project root's
README.md and configured context files, so configuring a project once serves
all of its experiments. Runs record their ``experiment_id`` in run_meta.json;
retries and continuations inherit it.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from app.harness.persistence import atomic_write_text, path_lock
from app.harness.project_workspace import project_root
from app.harness.runtime.project_scope import safe_scope_path

_SCHEMA = "experiment.v1"
_EXP_ID = re.compile(r"[0-9a-f]{32}")
_NAME_MAX = 120


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _experiments_dir(project: str) -> Path:
    root = project_root(project)
    if not root.is_dir():
        raise ValueError("项目不存在")
    return safe_scope_path(root, "experiments/.sentinel").parent


def _experiment_path(project: str, exp_id: str) -> Path:
    if not _EXP_ID.fullmatch(exp_id or ""):
        raise ValueError("无效的 experiment id")
    return safe_scope_path(_experiments_dir(project), f"{exp_id}.yaml")


def _validate_name(name: str) -> str:
    value = name.strip()
    if not value or len(value) > _NAME_MAX:
        raise ValueError(f"实验名称需要 1-{_NAME_MAX} 个字符")
    return value


def _load(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema") != _SCHEMA:
        raise ValueError("无效的 experiment 记录")
    return raw


def create_experiment(project: str, name: str, description: str = "") -> dict[str, Any]:
    value = _validate_name(name)
    directory = _experiments_dir(project)
    directory.mkdir(parents=True, exist_ok=True)
    with path_lock(safe_scope_path(directory, ".experiments.lock")):
        for existing in directory.glob("*.yaml"):
            record = _load(existing)
            if str(record.get("name", "")).strip() == value:
                raise ValueError(f"实验名称已存在：{value}")
        exp_id = uuid.uuid4().hex
        record = {
            "schema": _SCHEMA,
            "id": exp_id,
            "project": project,
            "name": value,
            "description": description.strip(),
            "created_at": _now(),
            "updated_at": _now(),
        }
        atomic_write_text(directory / f"{exp_id}.yaml", yaml.safe_dump(record, allow_unicode=True, sort_keys=False))
    return record


def get_experiment(project: str, exp_id: str) -> dict[str, Any]:
    path = _experiment_path(project, exp_id)
    if not path.exists():
        raise ValueError("实验不存在")
    return _load(path)


def require_experiment(project: str, exp_id: str) -> dict[str, Any]:
    """Validate an experiment_id handed in by a payload; empty means unset."""
    if not exp_id:
        return {}
    return get_experiment(project, exp_id)


def delete_experiment(project: str, exp_id: str) -> None:
    path = _experiment_path(project, exp_id)
    if not path.exists():
        raise ValueError("实验不存在")
    path.unlink()


def list_experiments(project: str) -> list[dict[str, Any]]:
    directory = _experiments_dir(project)
    if not directory.is_dir():
        return []
    records = [_load(path) for path in directory.glob("*.yaml")]
    records.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)
    return records
