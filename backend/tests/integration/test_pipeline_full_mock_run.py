"""Phase 3 e2e checkpoint: orchestrator + 5 real agents (all in mock_provider)
walks the full Idea→Writing pipeline, validates each artifact, persists into
``runs/<id>/<agent>/*.approved.md``."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from app.bridge.agent_registry import get_registry, reset_registry_for_tests
from app.bridge.orchestrator import Orchestrator, RunRequest
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.schema.validator import validate_document
from app.storage.run_store import RunStore


@pytest.fixture(autouse=True)
def _clear_keys_and_register(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for env in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "QWEN_API_KEY",
        "GEMINI_API_KEY",
    ):
        monkeypatch.delenv(env, raising=False)
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
            project="moe-pimc",
            entrypoint="pipeline",
            user_request="Investigate hard top-2 router for ATK-MoE under 8L config.",
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
