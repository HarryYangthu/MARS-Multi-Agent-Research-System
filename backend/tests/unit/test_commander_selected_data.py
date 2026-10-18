"""Real default dataset profiles are frozen at the Commander creation boundary."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.bridge.commander_session import ChatMessage, CommanderSession
from app.bridge.commander_tools import ToolContext, execute_tool
from app.bridge.orchestrator import Orchestrator, RunSession
from app.harness.project_workspace import open_folder
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.tools.execution.local_command import LocalCommandJob, command_request
from app.settings import reset_settings_cache
from app.storage.data_source_store import DataSourceStore
from app.storage.run_store import RunStore


@contextmanager
def _runtime(root: Path) -> Iterator[tuple[DataSourceStore, ToolContext, ToolContext]]:
    configured = {"MARS_FOLDER_PROJECTS_REGISTRY": str(root / "registry.json"),
                  "MARS_EXECUTION_CONFIG_PATH": str(root / "execution.yaml"),
                  "MARS_EXECUTION_BACKEND": "local_command", "MARS_RUNTIME_MODE": "development"}
    saved = {key: os.environ.get(key) for key in configured}
    os.environ.update(configured)
    reset_settings_cache()
    try:
        projects = [open_folder(str(root / name), create=True) for name in ("first", "other")]
        for project in projects:
            code = project.root / "code/ordinary"
            (code / "configs").mkdir(parents=True)
            (code / "configs/existing.yaml").write_text("model_name: ExistingModel\nseed: 2026\n")
            (code / "train.py").write_text("def forward(value):\n    return value\n")
            (project.metadata_root / "repo_link.yaml").write_text(yaml.safe_dump({
                "repo_path": str(code), "allowed_paths": ["configs/*.yaml", "train.py"], "read_only": True}))
        (root / "execution.yaml").write_text(yaml.safe_dump({"execution": {"backend": "local_command",
            "local_commands": [{"id": "current_data_binding", "project_ids": [p.name for p in projects],
                "entrypoints": ["train.py"], "config_paths": ["configs/existing.yaml"], "consumes_data_path": True}]}}))
        runs = RunStore(root / "runs", project_storage=True)
        orchestrator = Orchestrator(run_store=runs, bus=InProcessEventBus())
        contexts = [ToolContext(orchestrator, CommanderSession(conv_id=f"data-{i}", project=p.name), runs)
                    for i, p in enumerate(projects)]
        yield DataSourceStore(base=root / "legacy-data", project_storage=True), contexts[0], contexts[1]
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        reset_settings_cache()


def _dataset(store: DataSourceStore, project: str, name: str, values: list[int]) -> dict[str, Any]:
    source_id, target = store.allocate(original_name=name + ".json", project=project)
    target.write_text(json.dumps({"samples": values}), encoding="utf-8")
    return store.profile_uploaded_file(source_id=source_id, path=target, project=project,
        original_name=target.name, fs_mhz=1.0, description="actual selected samples")


async def _create(context: ToolContext) -> tuple[dict[str, Any], RunSession | None]:
    user = ChatMessage(role="user", content="运行configs/existing.yaml，2steps，先只准备")
    context.session.active_turn_id = user.id
    context.session.add(user)
    result = await execute_tool("run.existing_model", {"config_path": "configs/existing.yaml",
        "max_iters": 2, "budget_unit": "steps", "start": False}, context)
    return result, context.orchestrator.session(str(result["run_id"])) if result.get("ok") else None


def _job_request(session: RunSession) -> dict[str, Any]:
    config = {**session.request.extra["existing_execution_job"], "command_id": "current_data_binding"}
    return command_request(LocalCommandJob(run_id=session.run.run_id, experiment_id="actual-binding",
        project=session.run.project, run_root=session.run.root, config=config, seed=2026,
        steps=2, command_id="current_data_binding"), "read-only-request", session.run.root / "execution/request")


@pytest.mark.asyncio
async def test_project_default_is_frozen_and_consumed_without_cross_project_leak(tmp_path: Path) -> None:
    with _runtime(tmp_path) as (datasets, first, other):
        foreign = _dataset(datasets, other.session.project, "other", [10, 20, 30])
        datasets.set_default(project=other.session.project, source_id=foreign["id"])
        no_default, empty = await _create(first)
        assert no_default["ok"] and empty is not None
        assert empty.request.data_source is None
        assert not (empty.run.root / "input/selected_data_source.json").exists()
        assert "data_path" not in _job_request(empty)["config"]  # Preserve the original YAML path behavior.

        selected = _dataset(datasets, first.session.project, "first", [1, 3, 6])
        datasets.set_default(project=first.session.project, source_id=selected["id"])
        result, original = await _create(first)
        assert result["ok"] and original is not None
        assert original.request.data_source is not None and original.request.data_source["id"] == selected["id"]
        snapshots = [original.run.root / name for name in (
            "input/selected_data_source.json", "context/selected_data_source.json", "context/selected_data_source.md")]
        before = {str(path): path.read_bytes() for path in snapshots}
        assert json.loads(snapshots[0].read_text())["project"] == first.session.project
        assert _job_request(original)["config"]["data_path"] == str(Path(selected["stored_path"]).resolve())

        other_result, other_run = await _create(other)
        assert other_result["ok"] and other_run is not None and other_run.request.data_source is not None
        assert other_run.request.data_source["id"] == foreign["id"]

        replacement = _dataset(datasets, first.session.project, "replacement", [2, 4, 8])
        datasets.set_default(project=first.session.project, source_id=replacement["id"])
        changed_result, changed = await _create(first)
        assert changed_result["ok"] and changed is not None and changed.request.data_source is not None
        assert changed.request.data_source["id"] == replacement["id"]
        assert {str(path): path.read_bytes() for path in snapshots} == before
        assert _job_request(original)["config"]["data_path"] == str(Path(selected["stored_path"]).resolve())
        assert _job_request(changed)["config"]["data_path"] == str(Path(replacement["stored_path"]).resolve())


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["foreign_pointer", "missing_file", "missing_profile"])
async def test_invalid_default_is_reported_before_allocating_a_run(tmp_path: Path, fault: str) -> None:
    with _runtime(tmp_path) as (datasets, first, other):
        selected = _dataset(datasets, first.session.project, "selected", [1, 2, 3])
        datasets.set_default(project=first.session.project, source_id=selected["id"])
        if fault == "foreign_pointer":
            foreign = _dataset(datasets, other.session.project, "foreign", [5, 6, 7])
            # Corrupt a real local defaults file; do not replace the store/tool.
            datasets._defaults_path(first.session.project).write_text(json.dumps({first.session.project: foreign["id"]}))
        elif fault == "missing_file":
            Path(selected["stored_path"]).unlink()
        else:
            datasets.profile_path(selected["id"]).unlink()
        result, run = await _create(first)
        assert not result["ok"] and result["status"] == "selected_data_source_invalid"
        assert run is None and not first.run_store.list()
