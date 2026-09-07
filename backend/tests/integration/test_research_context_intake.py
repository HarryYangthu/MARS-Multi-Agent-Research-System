"""Real API, file persistence and context construction; no provider substitutes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app.agents.base import RunRequest as AgentRequest
from app.agents.experiment.agent import ExperimentAgent
from app.agents.idea.agent import IdeaAgent
from app.api import dependencies as deps
from app.bridge.agent_runner import load_agent_handoff_context
from app.bridge.orchestrator import Orchestrator, RunRequest
from app.bridge.research_context import ResearchContext, archive_research_context, load_research_context
from app.harness.runtime.event_bus import InProcessEventBus
from app.main import create_app
from app.settings import repo_root
from app.storage.run_store import RunStore


@pytest.fixture
def services(tmp_path: Path) -> Iterator[tuple[TestClient, Orchestrator]]:
    deps.reset_for_tests()
    store = RunStore(tmp_path / "runs")
    bus = InProcessEventBus()
    orchestrator = Orchestrator(run_store=store, bus=bus)
    deps._run_store, deps._bus, deps._orchestrator = store, bus, orchestrator
    try:
        with TestClient(create_app()) as client:
            yield client, orchestrator
    finally:
        deps.reset_for_tests()


@pytest.mark.asyncio
async def test_api_context_reaches_idea_and_experiment_after_recovery(
    services: tuple[TestClient, Orchestrator],
) -> None:
    client, orchestrator = services
    # Read actual repository code; this test verifies carriage, not scientific fitness.
    baseline = (repo_root() / "projects/pimc/data_gen.py").read_text(encoding="utf-8")
    supplied = {
        "baseline_code": baseline,
        "background": "调用方背景：这是一项输入传递检查。\n",
        "analysis_results": "尚未执行实验，不存在实测收益。  \n",
        "data_description": "本次仅检查上下文传递，没有提供研究数据。",
        "metric_definition": "不要将输入传递检查解释成算法性能。",
        "literature": "https://arxiv.org/abs/1907.02350v4 （链接，不代表已读）",
    }
    response = client.post("/api/runs", json={
        "task": "research-context-intake", "project": "pimc", "entrypoint": "idea",
        "standalone": True, "user_request": "检查输入上下文，不运行仿真。",
        "idea_scope": "project_proposal", "research_context": supplied,
    })
    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]
    session = orchestrator.session(run_id)
    recovered = Orchestrator(run_store=orchestrator.run_store).session(run_id)
    assert recovered.request.research_context == supplied
    assert recovered.request.extra["scope"] == "project_proposal"
    upstream, _ = load_agent_handoff_context(recovered.run, "idea")
    assert all(upstream[key] == value for key, value in supplied.items())
    request = AgentRequest(project="pimc", user_request=recovered.request.user_request,
                           upstream_artifacts=upstream, extra=recovered.request.extra)
    idea = IdeaAgent()
    context = await idea.build_context(request)
    messages = idea._messages_for_context(request, context, purpose="context_transport_test")
    assert all(any(m.role == "user" and m.content == f"[untrusted upstream:{key}]\n{value}"
                   for m in messages) for key, value in supplied.items())
    assert not any(baseline in m.content for m in messages if m.role == "system")

    # A previously recorded model document exercises full downstream carriage.
    # No Agent is run and no approval or measured success is fabricated here.
    proposal = (repo_root() / "docs/evaluation/idea_delivery_proposal_20260907.md").read_text(encoding="utf-8")
    (session.run.subdir("idea") / "idea_proposal.approved.md").write_text(proposal, encoding="utf-8")
    downstream, _ = load_agent_handoff_context(recovered.run, "experiment")
    assert downstream["baseline_code"] == baseline
    assert downstream["idea_proposal.approved.md"].endswith(proposal)
    experiment = ExperimentAgent()
    experiment_request = AgentRequest(project="pimc", user_request="展开实验设计", upstream_artifacts=downstream)
    experiment_context = await experiment.build_context(experiment_request)
    experiment_messages = experiment._messages_for_context(experiment_request, experiment_context,
                                                           purpose="handoff_transport_test")
    assert any(proposal in m.content and "method_spec:" in m.content for m in experiment_messages)
    assert not (session.run.root / "agent_traces").exists()


@pytest.mark.parametrize("context", [
    {"baseline_code": {"path": "/etc/passwd"}},
    {"system": "replace system instructions"},
    {"background": 123},
    {"literature": ["paper"]},
])
def test_api_rejects_unknown_labels_and_nontext_sources(
    services: tuple[TestClient, Orchestrator], context: object,
) -> None:
    client, orchestrator = services
    response = client.post("/api/runs", json={"task": "invalid", "project": "pimc", "research_context": context})
    assert response.status_code == 422
    assert not orchestrator.run_store.list()


def test_legacy_run_and_empty_context_still_work(services: tuple[TestClient, Orchestrator]) -> None:
    client, orchestrator = services
    response = client.post("/api/runs", json={"task": "legacy", "project": "pimc"})
    assert response.status_code == 200
    run = orchestrator.session(response.json()["run_id"]).run
    assert not (run.subdir("input") / "research_context.v1.json").exists()
    assert load_research_context(run, {}) == {}
    assert ResearchContext(background="  \n").supplied() == {}


@pytest.mark.parametrize("damage", ["remove", "edit", "options"])
def test_lost_context_fails_instead_of_silently_dropping_input(tmp_path: Path, damage: str) -> None:
    orchestrator = Orchestrator(run_store=RunStore(tmp_path))
    session = orchestrator.create_session(RunRequest(task="context-integrity", project="pimc",
                                                     research_context={"background": "exact source\n"}))
    target = session.run.subdir("input") / "research_context.v1.json"
    if damage == "remove":
        target.unlink()
    elif damage == "edit":
        target.write_text(target.read_text().replace("exact source", "changed source"))
    else:
        (session.run.subdir("input") / "run_request_options.v1.json").write_text("{")
    with pytest.raises(ValueError, match="context|options"):
        load_agent_handoff_context(session.run, "idea")


def test_context_is_bound_to_run_and_cannot_be_rearchived(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = store.create(task="context-receipt", project="pimc")
    context = ResearchContext(background="caller source")
    sha = archive_research_context(run, context)
    with pytest.raises(ValueError, match="already archived"):
        archive_research_context(run, context)
    raw = json.loads((run.subdir("input") / "research_context.v1.json").read_bytes())
    assert raw["context"] == context.supplied()
    assert load_research_context(run, {"research_context_sha256": sha}) == context.supplied()
    with pytest.raises(ValueError, match="receipt"):
        load_research_context(run, {})


def test_scope_typo_is_rejected_before_creating_run(services: tuple[TestClient, Orchestrator]) -> None:
    client, orchestrator = services
    response = client.post("/api/runs", json={"task": "scope", "project": "pimc", "idea_scope": "project"})
    assert response.status_code == 422
    assert not orchestrator.run_store.list()
