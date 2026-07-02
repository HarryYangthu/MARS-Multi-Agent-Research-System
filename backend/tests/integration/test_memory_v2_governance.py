from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.bridge.agent_registry import get_registry, reset_registry_for_tests
from app.bridge.orchestrator import Orchestrator, RunRequest
from app.harness.kb.config import reset_config_cache_for_tests
from app.harness.kb.ingester import ingest_memory
from app.harness.kb.models import EvalStatus
from app.harness.kb.stores import MAIN_ZONES, reset_for_tests as reset_kb_stores
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.sedimentation.hooks import sediment_approved_artifact
from app.main import create_app
from app.storage.artifact_store import ArtifactRef
from app.storage.run_store import RunStore


@pytest.fixture(autouse=True)
def _memory_profile(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("MARS_MEMORY_PROFILE", "dev_e2e")
    reset_config_cache_for_tests()
    yield
    reset_config_cache_for_tests()


def _register_mock_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    for env in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "QWEN_API_KEY",
        "GEMINI_API_KEY",
        "DEEPSEEK_API_KEY",
    ):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("MARS_MOCK_MODE", "always")
    monkeypatch.setenv("LOCAL_VLLM_BASE_URL", "")
    import app.settings as settings_mod

    settings_mod._settings = None
    reset_registry_for_tests()
    from app.agents.coding.agent import CodingAgent
    from app.agents.execution.agent import ExecutionAgent
    from app.agents.experiment.agent import ExperimentAgent
    from app.agents.idea.agent import IdeaAgent
    from app.agents.writing.agent import WritingAgent

    reg = get_registry()
    for cls in (IdeaAgent, ExperimentAgent, CodingAgent, ExecutionAgent, WritingAgent):
        agent = cls()
        reg.register(agent.name, agent)


@pytest.mark.asyncio
async def test_mock_pipeline_sediments_only_to_quarantine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_mock_agents(monkeypatch)
    stores = reset_kb_stores(tmp_path / "knowledge")
    orch = Orchestrator(
        run_store=RunStore(tmp_path / "runs"),
        bus=InProcessEventBus(),
    )
    session = orch.create_session(
        RunRequest(
            task="memory-governance-mock",
            project="pimc",
            entrypoint="pipeline",
            user_request="Run mock pipeline and keep long-term KB clean.",
            auto_approve=True,
        )
    )

    await orch.run(session.run.run_id)

    main_records = [
        record
        for zone in MAIN_ZONES
        for record in stores.zone(zone).all(exclude_mock=False)
    ]
    quarantined = stores.zone("quarantine").all(exclude_mock=False)
    assert all(record.metadata.get("is_mock") is not True for record in main_records)
    assert quarantined
    assert all(record.metadata.get("is_mock") is True for record in quarantined)
    reset_registry_for_tests()


def test_research_profile_eval_fail_does_not_enter_main_zones(tmp_path: Path) -> None:
    stores = reset_kb_stores(tmp_path / "knowledge")
    run = RunStore(tmp_path / "runs").create(
        task="eval-fail",
        project="pimc",
        user_request="invalid artifact",
    )
    path = run.subdir("idea") / "idea_proposal.approved.md"
    path.write_text(
        """---
schema: proposal.v1
project: pimc
agent: idea
research_question: "How can routing be simplified?"
hypothesis: "Hard routing may work under switching."
---

# Proposal

This is intentionally missing required novelty.
""",
        encoding="utf-8",
    )
    ref = ArtifactRef(
        run_id=run.run_id,
        agent_dir="idea",
        stem="idea_proposal",
        version="approved",
        path=path,
    )

    result = sediment_approved_artifact(
        run=run,
        agent="idea",
        artifact_ref=ref,
        profile="research",
    )

    assert result["eval_passed"] is False
    assert sum(len(stores.zone(zone).all(exclude_mock=False)) for zone in MAIN_ZONES) == 0
    quarantined = stores.zone("quarantine").all(exclude_mock=False)
    assert quarantined
    assert quarantined[0].metadata["eval_status"]["reason"] == "eval_not_passed"


def test_knowledge_api_defaults_filter_unsafe_memory(tmp_path: Path) -> None:
    stores = reset_kb_stores(tmp_path / "knowledge")
    eval_status = EvalStatus(passed=True, decision="pass")
    good = ingest_memory(
        zone="methodology",
        text="router memory governance verified baseline",
        metadata={"project": "pimc", "kind": "methodology"},
        memory_type="procedural",
        source_path="tests/good.md",
        eval_status=eval_status,
        confidence=0.9,
        salience=0.8,
        approved=True,
        stores=stores,
    )[0]
    mock = ingest_memory(
        zone="methodology",
        text="router memory governance mock placeholder",
        metadata={"project": "pimc", "kind": "methodology"},
        memory_type="procedural",
        source_path="tests/mock.md",
        is_mock=True,
        approved=True,
        stores=stores,
    )[0]
    old = ingest_memory(
        zone="methodology",
        text="router memory governance superseded",
        metadata={"project": "pimc", "kind": "methodology"},
        memory_type="procedural",
        source_path="tests/old.md",
        approved=True,
        stores=stores,
    )[0]
    quarantined = ingest_memory(
        zone="quarantine",
        text="router memory governance quarantine",
        metadata={"project": "pimc", "kind": "methodology"},
        memory_type="procedural",
        source_path="tests/quarantine.md",
        is_mock=True,
        approved=True,
        stores=stores,
    )
    stores.update_metadata("methodology", old.id, {"superseded_by": good.id})

    client = TestClient(create_app())
    response = client.get(
        "/api/knowledge/search",
        params={"q": "router memory governance", "top_k": 10},
    )

    assert response.status_code == 200, response.text
    ids = [item["item"]["id"] for item in response.json()]
    assert ids == [good.id]
    assert mock.id not in ids
    assert old.id not in ids

    global_quarantine_response = client.get(
        "/api/knowledge/search",
        params={"q": "router memory governance", "zones": "quarantine"},
    )
    assert global_quarantine_response.status_code == 404

    quarantine_response = client.get("/api/knowledge/quarantine/items")
    assert quarantine_response.status_code == 200, quarantine_response.text
    quarantine_ids = [item["id"] for item in quarantine_response.json()]
    assert quarantine_ids == [quarantined[0].id]

    quarantine_search = client.get(
        "/api/knowledge/quarantine/search",
        params={"q": "router memory governance", "top_k": 10},
    )
    assert quarantine_search.status_code == 200, quarantine_search.text
    search_ids = [item["item"]["id"] for item in quarantine_search.json()]
    assert search_ids == [quarantined[0].id]
