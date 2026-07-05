"""Evaluation suite configuration for replay and benchmark runs."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.settings import repo_root
from app.storage.run_store import RUN_SUBDIRS


DEFAULT_REPLAY_SUITE = "configs/evaluation_suites/mars_run_replay_v0.yaml"


@dataclass(frozen=True)
class EvaluationTask:
    id: str
    task: str
    project: str
    entrypoint: str = "pipeline"
    standalone: bool = False
    user_request: str = ""
    trials: int = 1


@dataclass(frozen=True)
class ExpectedOutcome:
    required_dirs: tuple[str, ...] = RUN_SUBDIRS
    required_artifacts: tuple[str, ...] = ()
    required_event_files: tuple[str, ...] = ()
    require_context_manifest: bool = True
    require_tool_audit: bool = True
    require_execution_metrics: bool = False
    require_report_chain_refs: bool = False
    expected_gates: tuple[str, ...] = ()
    expected_entrypoint: str | None = None
    expected_stages: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvaluationSuite:
    id: str
    project: str | None = None
    mode: str = "replay"
    description: str = ""
    graders: tuple[str, ...] = ()
    metrics: tuple[str, ...] = ()
    expected: ExpectedOutcome = field(default_factory=ExpectedOutcome)
    tasks: tuple[EvaluationTask, ...] = ()


def load_suite(path: Path | None = None) -> EvaluationSuite:
    source = path or (repo_root() / DEFAULT_REPLAY_SUITE)
    if not source.exists():
        return EvaluationSuite(id="mars_run_replay_v0")
    raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"evaluation suite must be a mapping: {source}")
    expected_raw = _mapping(raw.get("expected_outcome"))
    return EvaluationSuite(
        id=str(raw.get("id", source.stem)),
        project=_optional_str(raw.get("project")),
        mode=str(raw.get("mode", "replay")),
        description=str(raw.get("description", "")),
        graders=_str_tuple(raw.get("graders")),
        metrics=_str_tuple(raw.get("metrics")),
        tasks=_tasks(raw.get("tasks"), project=_optional_str(raw.get("project"))),
        expected=ExpectedOutcome(
            required_dirs=_str_tuple(
                expected_raw.get("required_dirs"),
                default=RUN_SUBDIRS,
            ),
            required_artifacts=_str_tuple(expected_raw.get("required_artifacts")),
            required_event_files=_str_tuple(expected_raw.get("required_event_files")),
            require_context_manifest=bool(
                expected_raw.get("require_context_manifest", True)
            ),
            require_tool_audit=bool(expected_raw.get("require_tool_audit", True)),
            require_execution_metrics=bool(
                expected_raw.get("require_execution_metrics", False)
            ),
            require_report_chain_refs=bool(
                expected_raw.get("require_report_chain_refs", False)
            ),
            expected_gates=_str_tuple(expected_raw.get("expected_gates")),
            expected_entrypoint=_optional_str(expected_raw.get("expected_entrypoint")),
            expected_stages=_str_tuple(expected_raw.get("expected_stages")),
        ),
    )


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _optional_str(value: object) -> str | None:
    return str(value) if value is not None else None


def _str_tuple(value: object, *, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    if value is None:
        return default
    if not isinstance(value, list):
        return default
    return tuple(str(item) for item in value if str(item).strip())


def _tasks(value: object, *, project: str | None) -> tuple[EvaluationTask, ...]:
    if not isinstance(value, list):
        return ()
    tasks: list[EvaluationTask] = []
    for idx, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("id", f"task_{idx}"))
        task_name = str(item.get("task", item.get("name", task_id)))
        task_project = str(item.get("project", project or "pimc"))
        input_raw = _mapping(item.get("input"))
        user_request = str(item.get("user_request", input_raw.get("user_request", "")))
        trials_raw = item.get("trials", 1)
        trials = trials_raw if isinstance(trials_raw, int) and trials_raw > 0 else 1
        tasks.append(
            EvaluationTask(
                id=task_id,
                task=task_name,
                project=task_project,
                entrypoint=str(item.get("entrypoint", "pipeline")),
                standalone=bool(item.get("standalone", False)),
                user_request=user_request,
                trials=trials,
            )
        )
    return tuple(tasks)
