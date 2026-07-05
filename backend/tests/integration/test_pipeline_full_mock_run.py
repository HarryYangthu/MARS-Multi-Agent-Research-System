"""Phase 3 e2e checkpoint: orchestrator + 5 real agents (all in mock_provider)
walks the full Idea→Writing pipeline, validates each artifact, persists into
``runs/<id>/<agent>/*.approved.md``."""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.bridge.agent_registry import get_registry, reset_registry_for_tests
from app.bridge.orchestrator import Orchestrator, RunRequest
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.schema.frontmatter_parser import parse as parse_fm
from app.harness.schema.validator import validate_document
from app.storage.run_store import RunStore


@pytest.fixture(autouse=True)
def _clear_keys_and_register(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
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

    # Fast execution config so the post-approval sim batch doesn't run the
    # demo-tuned ~72s simulation during the test.
    from app.execution.config import ExecutionConfig

    fast = ExecutionConfig(
        max_concurrency=4,
        default_steps=3,
        agent_batch_steps=3,
        backend="mock",
        job_timeout_seconds=10.0,
        feedback_max_attempts=2,
        planned_experiments=4,
        tick_seconds=0.0,
    )
    monkeypatch.setattr("app.execution.config.get_execution_config", lambda: fast)

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
    yield
    reset_registry_for_tests()


@pytest.mark.asyncio
async def test_full_pipeline_under_mock(tmp_path: Path) -> None:
    bus = InProcessEventBus()
    orch = Orchestrator(run_store=RunStore(tmp_path), bus=bus)

    session = orch.create_session(
        RunRequest(
            task="phase3-mock-e2e",
            project="pimc",
            entrypoint="pipeline",
            user_request="Investigate hard top-2 router for PIMC under 8L config.",
            auto_approve=True,
        )
    )
    await orch.run(session.run.run_id)

    # Each agent writes a .v1.md and is auto-approved (Phase 3 default).
    schema_by_agent = {
        "idea": "proposal.v1",
        "experiment": "experiment_plan.v1",
        "coding": "code_spec.v1",
        "execution": "run_log.v1",
        "writing": "report.v1",
    }

    for agent_dir, schema in schema_by_agent.items():
        d = session.run.subdir(agent_dir)
        approved = list(d.glob("*.approved.md"))
        assert approved, f"missing approved artifact for {agent_dir}"
        text = approved[0].read_text(encoding="utf-8")
        res = validate_document(text, expected_schema=schema)
        assert res.valid, f"{agent_dir}: {res.errors}"

    diagnoses = sorted(session.run.subdir("diagnosis").glob("diagnosis.v*.md"))
    diagnosis_docs: list[str] = []
    for path in diagnoses:
        suffix = path.stem.removeprefix("diagnosis.v")
        if not suffix.isdigit():
            continue
        text = path.read_text(encoding="utf-8")
        if parse_fm(text).metadata.get("schema") == "diagnosis.v1":
            diagnosis_docs.append(text)
    assert diagnosis_docs
    for text in diagnosis_docs:
        diagnosis = validate_document(text, expected_schema="diagnosis.v1")
        assert diagnosis.valid
    assert parse_fm(diagnosis_docs[-1]).metadata["passed"] is True
    feedback_packet = session.run.subdir("diagnosis") / "feedback_packet.attempt_2.md"
    if len(diagnosis_docs) > 1:
        assert feedback_packet.exists()
        assert (session.run.subdir("coding") / "patch.v2.diff").exists()
    assert (session.run.root / "run_state.json").exists()
    assert (session.run.subdir("context") / "trace_manifest.v2.json").exists()
    assert (session.run.subdir("context") / "context_manifest.v2.json").exists()
    manifests = sorted(session.run.subdir("context").glob("context_manifest.v2.*.json"))
    assert len(manifests) >= 5

    evaluation_events_path = session.run.subdir("events") / "evaluation_events.jsonl"
    assert evaluation_events_path.exists()
    evaluation_events = [
        json.loads(line)
        for line in evaluation_events_path.read_text(encoding="utf-8").splitlines()
    ]
    artifact_events = [
        event
        for event in evaluation_events
        if event["event"] == "evaluation.artifact_evaluated"
    ]
    assert len(artifact_events) >= 5
    assert {event["agent"] for event in artifact_events}.issuperset(schema_by_agent)
    assert all(event["report_count"] >= 1 for event in artifact_events)
    assert all("policy" in event for event in artifact_events)
    assert evaluation_events[-1]["event"] == "evaluation.scorecard_written"
    assert "quality_gate" in evaluation_events[-1]
    assert (session.run.subdir("events") / "evaluation_quality_gate.json").exists()
