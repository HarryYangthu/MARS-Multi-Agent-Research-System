"""Run control through mars.api: start (with or without human approval), resume / approve, stop.

The facade itself refuses a taken run id, a second process on a running run and unknown runs; this module only
writes the task file the app generated and validates it before the core sees it.
"""
from __future__ import annotations

from pathlib import Path
import math
from types import ModuleType
from typing import Any

import yaml

from mars_app.artifacts import RUN_ID
from mars_app.settings import Settings

REQUIRED_KEYS = ("title", "objective", "targets", "budget")


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_contract(spec: Any) -> list[str]:
    if not isinstance(spec, dict):
        return ["研究约定必须是一个 YAML 映射"]
    problems = [f"缺少字段 {key}" for key in REQUIRED_KEYS if not spec.get(key)]
    for key in ("targets", "budget"):
        if key in spec and not isinstance(spec[key], dict):
            problems.append(f"{key} 必须是映射")
    if "constraints" in spec and not isinstance(spec["constraints"], list):
        problems.append("constraints 必须是列表")
    execution = spec.get("execution")
    if execution is not None and (not isinstance(execution, dict) or execution.get("environment") not in ("local", "remote")):
        problems.append("execution.environment 必须是 local 或 remote")
    targets = spec.get("targets") if isinstance(spec.get("targets"), dict) else {}
    for i, gate in enumerate(targets.get("gates") or []):
        limit = gate.get("max_worse", 0.0) if isinstance(gate, dict) else None
        if not isinstance(gate, dict) or not isinstance(gate.get("metric"), str) or not _finite(limit):
            problems.append(f"targets.gates[{i}] 需要 metric 和有限的 max_worse（负数表示要求改善）")
    for i, gate in enumerate(targets.get("checks") or []):
        bounds = [gate.get(k) for k in ("max", "min") if isinstance(gate, dict) and k in gate]
        if not isinstance(gate, dict) or not gate.get("check") or not gate.get("field") or not bounds or not all(map(_finite, bounds)):
            problems.append(f"targets.checks[{i}] 需要 check、field 和有限的 max 或 min")
    for key in ("proposers", "routes", "max_iterations_per_route", "max_step1_rounds", "research_sources", "codingagent_minutes"):
        value = spec.get("budget", {}).get(key) if isinstance(spec.get("budget"), dict) else None
        if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 1):
            problems.append(f"budget.{key} 必须是正整数")
    return problems


class Launcher:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.tasks_dir = settings.data_dir / "tasks"

    def write_task(self, run_id: str, spec: Any, human_approval: bool, runs_dir: Path | None = None,
                   tasks_dir: Path | None = None) -> Path:
        """Validate a research contract and write it under tasks_dir (default data/tasks/) for the core to read."""
        if not RUN_ID.match(run_id):
            raise ValueError("运行 id 只能包含字母、数字、点、下划线和连字符，且不超过 80 个字符")
        problems = validate_contract(spec)
        if problems:
            raise ValueError("；".join(problems))
        if ((runs_dir or self.settings.runs_dir) / run_id).exists():
            raise ValueError(f"运行 {run_id} 已存在")
        folder = tasks_dir or self.tasks_dir
        folder.mkdir(parents=True, exist_ok=True)
        task = folder / f"{run_id}.yaml"
        task.write_text(yaml.safe_dump({**spec, "human_approval": human_approval}, allow_unicode=True, sort_keys=False),
                        encoding="utf-8")
        return task

    def start(self, api: Any, run_id: str, spec: Any, human_approval: bool, runs_dir: Path | None = None,
              tasks_dir: Path | None = None) -> dict[str, Any]:
        task = self.write_task(run_id, spec, human_approval, runs_dir, tasks_dir)
        started = api.start_run(task, run_id=run_id, auto_approve=not human_approval)
        return {"run_id": started, "task_file": str(task), "human_approval": human_approval}

    def start_refine(self, api: Any, run_id: str, spec: Any, proposal: str, human_approval: bool,
                     runs_dir: Path | None = None, tasks_dir: Path | None = None) -> dict[str, Any]:
        """Refine scenario: the user's own design (plain text, never read as a path) evolves near itself in Step2."""
        if not proposal.strip():
            raise ValueError("方案内容不能为空")
        task = self.write_task(run_id, spec, human_approval, runs_dir, tasks_dir)
        started = api.start_refine(task, proposal, run_id=run_id, auto_approve=not human_approval)
        return {"run_id": started, "task_file": str(task), "human_approval": human_approval}

    def template_path(self, name: str, directory: Path) -> Path:
        path = directory / f"{name}.yaml"
        if not RUN_ID.match(name) or not path.is_file() or not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError(f"项目中没有研究约定 {name}")
        return path

    def log_tail(self, run_id: str, max_bytes: int = 20000) -> str:
        path = self.settings.runs_dir / f"{run_id}.log"  # where mars.api sends the CLI output
        return path.read_bytes()[-max_bytes:].decode(errors="replace") if path.exists() else ""

    def templates(self, directory: Path) -> list[dict[str, Any]]:
        """Research contracts of the selected project (its tasks/ folder): the ones it ships and the ones saved here."""
        items = []
        for path in sorted(directory.glob("*.yaml")) if directory.is_dir() else []:
            if not path.resolve().is_relative_to(directory.resolve()):
                continue
            try:
                spec = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                continue
            if isinstance(spec, dict):
                items.append({"name": path.stem, "title": spec.get("title", ""), "contract": spec,
                              "yaml": path.read_text(encoding="utf-8"), "human_approval": bool(spec.get("human_approval", True))})
        return items
