"""Real context files and authored metric records; no model/job success substitutes."""
from __future__ import annotations

from collections.abc import Iterator
import json
from pathlib import Path
from typing import Any

import pytest

from app.agents.base import BaseAgent, RunRequest
from app.agents.coding.agent import CodingAgent
from app.agents.execution.agent import ExecutionAgent
from app.agents.experiment.agent import ExperimentAgent
from app.agents.writing.agent import WritingAgent
from app.bridge.agent_runner import _execution_result_handoffs, _summarize_execution_metrics
from app.harness.project_workspace import open_folder
from app.harness.schema.validator import validate_document
from app.settings import repo_root, reset_settings_cache
from app.storage.run_store import RunStore

AGENTS: tuple[type[BaseAgent], ...] = (ExperimentAgent, CodingAgent, WritingAgent)


@pytest.mark.asyncio
async def test_execution_intake_keeps_approved_context_without_new_research(tmp_path: Path) -> None:
    agent = ExecutionAgent()
    request = request_for("classification", tmp_path / "run")
    request.upstream_artifacts["approved_plan"] = "Authored intake context input"
    context = await agent.build_context(request)
    assert agent.requires_model is False
    assert context.upstream == request.upstream_artifacts
    assert "project_knowledge" not in context.metadata
    assert "references" not in context.metadata


@pytest.fixture
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    # Configure a real isolated registry; do not replace providers, services or tools.
    path = tmp_path / "registry.json"
    monkeypatch.setenv("MARS_FOLDER_PROJECTS_REGISTRY", str(path))
    reset_settings_cache()
    yield path
    reset_settings_cache()


def request_for(project: str, root: Path, *, references: bool = True) -> RunRequest:
    return RunRequest(project=project, user_request="Use this project's declared metric and protected evaluator.",
        extra={"run_root": str(root), "context_sources": {
            "code_repositories": False, "memory": False, "project_references": references}})


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_type", AGENTS)
async def test_real_folder_context_and_compiled_defaults_do_not_inherit_another_domain(
    tmp_path: Path, registry: Path, agent_type: type[BaseAgent],
) -> None:
    project = open_folder(str(tmp_path / "classification"), create=True)
    (project.root / "AGENTS.md").write_text("Protect evaluation/scorer.py; write candidates only under src/.")
    (project.root / "context/metrics.md").write_text("Primary metric: macro_f1, maximize, unit fraction. Preserve held-out labels.")
    agent = agent_type()
    request = request_for(project.name, tmp_path / "run")
    context = await agent.build_context(request)
    messages = agent._messages_for_context(request, context, purpose="loop")
    rendered = "\n".join(message.content for message in messages)
    assert "macro_f1" in rendered and "maximize" in rendered and "evaluation/scorer.py" in rendered
    assert "明确阻断" in rendered
    for foreign_default in ("pimc", "PIMC", "RES", "-26", "Paper_Total_0327", "stream_label", "mock_simulation"):
        assert foreign_default not in rendered
    snapshot = json.loads((tmp_path / "run/input/folder_context.v1.json").read_text())
    assert snapshot["project"] == project.name
    assert "project_knowledge" not in context.metadata
    assert not (tmp_path / "run/input/project_knowledge.v1.json").exists()


@pytest.mark.asyncio
async def test_downstream_agents_reuse_one_real_project_knowledge_snapshot(tmp_path: Path, registry: Path) -> None:
    snapshots: list[str] = []
    for agent_type in AGENTS:
        agent = agent_type()
        context = await agent.build_context(request_for("pimc", tmp_path / "run"))
        references = "\n".join(item["text"] for item in context.metadata["references"])
        assert "下游 Agent 的适用边界" in references
        assert "Paper_Total_0327" in references
        assert "不能保证泛化改善" in references
        assert "下游 Agent 的适用边界" not in context.project
        snapshots.append(context.metadata["project_knowledge"]["sha256"])
    assert len(set(snapshots)) == 1
    path = tmp_path / "run/input/project_knowledge.v1.json"
    snapshot = json.loads(path.read_text())
    assert snapshot["source"] == "context/public_context.md"
    snapshot["content"] += "unaudited edit"
    path.write_text(json.dumps(snapshot))
    with pytest.raises(ValueError, match="snapshot is invalid"):
        await WritingAgent().build_context(request_for("pimc", tmp_path / "run"))


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_type", AGENTS)
async def test_disabled_project_references_do_not_load_domain_knowledge(
    tmp_path: Path, registry: Path, agent_type: type[BaseAgent],
) -> None:
    context = await agent_type().build_context(request_for("pimc", tmp_path / "run", references=False))
    assert "project_knowledge" not in context.metadata
    assert "下游 Agent 的适用边界" not in context.project
    assert not (tmp_path / "run/input/project_knowledge.v1.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_type", AGENTS)
async def test_project_without_knowledge_file_has_no_implicit_domain_defaults(
    tmp_path: Path, registry: Path, agent_type: type[BaseAgent],
) -> None:
    agent = agent_type()
    request = request_for("synthetic_regression", tmp_path / "run")
    context = await agent.build_context(request)
    rendered = "\n".join(message.content for message in agent._messages_for_context(request, context, purpose="loop"))
    assert "project_knowledge" not in context.metadata
    assert "明确阻断" in rendered
    assert "PIMC" not in rendered and "pimc" not in rendered and "Paper_Total_0327" not in rendered
    assert not (tmp_path / "run/input/project_knowledge.v1.json").exists()


@pytest.mark.parametrize("schema_id", ["experiment_plan.v1", "code_spec.v1", "run_log.v1", "report.v1"])
def test_generic_templates_validate_without_claiming_performance(schema_id: str) -> None:
    text = (repo_root() / "templates/artifacts" / (schema_id + ".md")).read_text()
    result = validate_document(text, expected_schema=schema_id)
    assert result.valid, result.errors
    assert result.metadata["project"] == "PROJECT_ID_FROM_TASK"
    assert "明确阻断" in text
    assert "pimc" not in text and "RES" not in text
    if schema_id == "code_spec.v1":
        assert result.metadata["test_coverage"]["baseline_smoke_test"] == "skipped"
        assert result.metadata["files_changed"] == []
    if schema_id == "run_log.v1":
        assert result.metadata["status"] == "interrupted"
        assert result.metadata["execution_phase"] == "planned"
        assert set(result.metadata["metrics"]) == {"planned_experiments"}
        assert "gpu_used" not in result.metadata and "duration_seconds" not in result.metadata


def test_summary_preserves_authored_cross_domain_values_and_source_order(tmp_path: Path) -> None:
    # These are explicitly authored parser inputs, not an executed research job.
    run = RunStore(tmp_path).create(task="authored-metric-records", project="classification")
    rows = [{"run_id": "candidate-z", "metrics": {"macro_f1": 0.6, "latency_ms": 2.0}},
            {"run_id": "candidate-a", "metrics": {"macro_f1": 0.9, "latency_ms": 3.0}}]
    (run.subdir("execution") / "metrics.json").write_text(json.dumps(rows))
    summary = _execution_result_handoffs(run)["execution.metrics.json"]
    assert "source: execution/metrics.json" in summary
    assert "macro_f1: min=0.6, max=0.9, mean=0.75, count=2" in summary
    assert "latency_ms: min=2, max=3, mean=2.5, count=2" in summary
    assert summary.index('"candidate-z"') < summary.index('"candidate-a"')
    assert "not ranked" in summary and "approved contract" in summary
    assert "RES" not in summary and "best_" not in summary
    assert not list(run.subdir("writing").glob("*.md"))


def test_summary_rejects_non_finite_metadata_and_never_uses_metric_name_as_direction() -> None:
    rows: list[Any] = [None, {"metrics": "invalid"}, {"run_id": "authored", "metrics": {
        "RES": -2.0, "nan_metric": float("nan"), "infinite_metric": float("inf"),
        "flag": True, "numeric_text": "3.1", "missing": None, "huge_integer": 10**1000}},
        {"metrics": {"large": 1e308}}, {"metrics": {"large": 1e308}}]
    summary = _summarize_execution_metrics(rows=rows, source_ref="authored.json")
    assert "omitted_non_numeric_or_non_finite_values: 6" in summary
    assert "large: min=1e+308, max=1e+308, mean=1e+308, count=2" in summary
    assert "RES: min=-2, max=-2, mean=-2, count=1" in summary
    assert "lower RES" not in summary and "best_RES" not in summary
    assert "nan_metric" not in summary and "infinite_metric" not in summary


def test_empty_and_long_metric_files_do_not_imply_outcomes_or_hide_omission() -> None:
    empty = _summarize_execution_metrics(rows=[{"metrics": {"mse": None}}], source_ref="empty.json")
    assert "no outcome can be established" in empty
    rows: list[Any] = [{"run_id": f"row-{n}", "metrics": {"mse": float(n)}} for n in range(7)]
    summary = _summarize_execution_metrics(rows=rows, source_ref="all.json")
    assert "mean=3, count=7" in summary
    assert '"row-0"' in summary and '"row-6"' not in summary
    assert "additional rows: 2; inspect all.json" in summary
