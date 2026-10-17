"""Real API reads retain bounded causes from actual invalid local records."""
from __future__ import annotations

from collections.abc import Iterator
import json
import os
from pathlib import Path
import sys

from fastapi import FastAPI
import httpx
from loguru import logger
import pytest
import yaml

from app.api import dependencies
from app.api.runs import router
from app.bridge.agent_registry import AgentRegistry
from app.bridge.orchestrator import Orchestrator, RunRequest, RunSession
from app.harness.schema.frontmatter_parser import dumps
from app.harness.tools.registry import ToolContext, get_registry
from app.settings import repo_root, reset_settings_cache
from app.storage.artifact_store import ArtifactStore
from app.storage.run_store import RunStore


@pytest.fixture
def genuine_api(tmp_path: Path) -> Iterator[tuple[FastAPI, RunSession, Path, list[str]]]:
    execution_path = tmp_path / "execution.yaml"
    execution_path.write_text(yaml.safe_dump({"execution": {
        "backend": "local_command", "max_concurrency": 1, "batch_steps": 1,
        "command_timeout_seconds": 5, "local_commands": [{
            "id": "version-only", "argv": [sys.executable, "--version"],
        }],
    }}), encoding="utf-8")
    tools = yaml.safe_load((repo_root() / "configs/tools.yaml").read_text())
    tools["tools"]["execution.simulation_runner"]["command_allowlist"] = [[sys.executable, "--version"]]
    tools_path = tmp_path / "tools.yaml"
    tools_path.write_text(yaml.safe_dump(tools), encoding="utf-8")
    configuration = {
        "MARS_EXECUTION_BACKEND": "local_command", "MARS_EXECUTION_CONFIG_PATH": str(execution_path),
        "MARS_TOOLS_CONFIG_PATH": str(tools_path), "MARS_FOLDER_PROJECTS_REGISTRY": str(tmp_path / "registry.json"),
    }
    previous_env = {name: os.environ.get(name) for name in configuration}
    previous_dependencies = dependencies._run_store, dependencies._orchestrator
    os.environ.update(configuration)
    reset_settings_cache()
    from app.harness.project_workspace import open_folder
    project = open_folder(str(tmp_path / "project"), create=True)
    store = RunStore(tmp_path / "runs")
    orch = Orchestrator(run_store=store, registry=AgentRegistry())
    session = orch.create_session(RunRequest(task="read actual invalid records", project=project.name,
        entrypoint="execution", standalone=True, auto_approve=False))
    plan = {"schema": "run_log.v1", "project": project.name, "agent": "execution", "run_id": session.run.run_id,
        "status": "interrupted", "metrics": {"planned_experiments": 1}, "fingerprint_hash": "sha256:00",
        "planned_experiments": [{"name": "candidate", "config": {
            "command_id": "version-only", "seed": 2026, "budget_steps": 1,
        }}]}
    artifacts = ArtifactStore(session.run)
    artifacts.approve(artifacts.write(text=dumps(plan, "Human-authored plan; no process or measurements asserted.")))
    dependencies._run_store, dependencies._orchestrator = store, orch
    app = FastAPI()
    app.include_router(router)
    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(str(message)), level="WARNING")
    root = tmp_path / "actual-code"
    root.mkdir()
    try:
        yield app, session, root, messages
    finally:
        logger.remove(sink)
        dependencies._run_store, dependencies._orchestrator = previous_dependencies
        for name, value in previous_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        reset_settings_cache()


@pytest.mark.asyncio
async def test_execution_get_retains_real_json_cause_and_owned_run_log_context(
    genuine_api: tuple[FastAPI, RunSession, Path, list[str]],
) -> None:
    app, session, _, messages = genuine_api
    path = session.run.root / "execution/confirmation_revisions/execution.json"
    path.parent.mkdir(parents=True)
    path.write_text("not valid JSON", encoding="utf-8")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/runs/{session.run.run_id}/execution-configuration",
                                    params={"project": session.run.project})
    assert response.status_code == 409
    assert "Expecting value" in response.json()["detail"]
    assert any(session.run.run_id in line and "JSONDecodeError" in line for line in messages)
    assert path.read_text() == "not valid JSON"
    assert not (session.run.root / "execution/local_commands").exists()


@pytest.mark.asyncio
async def test_code_changes_get_retains_project_mismatch_without_logging_untrusted_query(
    genuine_api: tuple[FastAPI, RunSession, Path, list[str]],
) -> None:
    app, session, _, messages = genuine_api
    private_query = "password=private-query-credential"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/runs/{session.run.run_id}/code-changes", params={"project": private_query})
    assert response.status_code == 409
    assert "任务所属项目不匹配" in response.json()["detail"]
    assert messages and any(session.run.run_id in line and "ValueError" in line for line in messages)
    assert private_query not in response.text and private_query not in "\n".join(messages)
    assert not (session.run.root / "execution/local_commands").exists()


@pytest.mark.asyncio
async def test_code_changes_get_explains_corrupted_real_write_record_key(
    genuine_api: tuple[FastAPI, RunSession, Path, list[str]],
) -> None:
    app, session, root, messages = genuine_api
    context = ToolContext(run_id=session.run.run_id, project=session.run.project, agent="coding",
        project_repo_root=str(root), extra={"run_root": str(session.run.root)})
    result = await get_registry().dispatch("code.write_file", {"path": "actual.py", "content": "VALUE = 7\n"}, context)
    assert result.ok, result.error
    assert (root / "actual.py").read_text() == "VALUE = 7\n"
    path = next(path for path in (session.run.root / "coding/tool_applications").glob("*.json")
                if not path.name.startswith("rollback_"))
    record = json.loads(path.read_text())
    assert record["status"] == "success" and record["tool"] == "code.write_file"
    record.pop("call_id")
    path.write_text(json.dumps(record), encoding="utf-8")  # Corrupt genuine local metadata, never fabricate tool success.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/runs/{session.run.run_id}/code-changes", params={"project": session.run.project})
    assert response.status_code == 404
    assert "call_id" in response.json()["detail"]
    assert any(session.run.run_id in line and "KeyError" in line for line in messages)
    assert "call_id" not in json.loads(path.read_text())
    assert (root / "actual.py").read_text() == "VALUE = 7\n"
