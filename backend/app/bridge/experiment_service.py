"""Validate existing project experiment bindings for research conversations."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from app.harness.project_workspace import project_root
from app.harness.runtime.project_scope import safe_scope_path

_SCHEMA = "experiment.v1"
_EXP_ID = re.compile(r"[0-9a-f]{32}")


def _experiments_dir(project: str) -> Path:
    root = project_root(project)
    if not root.is_dir():
        raise ValueError("项目不存在")
    return safe_scope_path(root, "experiments/.sentinel").parent


def _experiment_path(project: str, exp_id: str) -> Path:
    if not _EXP_ID.fullmatch(exp_id or ""):
        raise ValueError("无效的 experiment id")
    return safe_scope_path(_experiments_dir(project), f"{exp_id}.yaml")


def _load(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema") != _SCHEMA:
        raise ValueError("无效的 experiment 记录")
    return raw


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
