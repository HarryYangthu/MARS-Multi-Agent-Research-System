"""Phase E: streamed thinking + execution gating + 16-experiment grid."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.agents.base import RunRequest
from app.agents.experiment.agent import ExperimentAgent
from app.bridge.agent_runner import run_execution_batch, write_planned_experiments
from app.storage.run_store import RunStore


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, channel: str, payload: dict[str, Any]) -> None:
        self.events.append((channel, payload))


def test_planned_experiments_builds_grid(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="plan", project="moe-pimc")
    exps = write_planned_experiments(run)
    assert len(exps) == 16  # configs/execution.yaml planned_experiments
    ids = {e["experiment_id"] for e in exps}
    assert len(ids) == 16  # all unique
    assert (run.root / "execution" / "planned_experiments.json").exists()


@pytest.mark.asyncio
async def test_execution_batch_streams_aggregated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Fast config so the test doesn't wait for the demo-tuned 72s sim.
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

    run = RunStore(tmp_path).create(task="sim", project="moe-pimc")
    write_planned_experiments(run)
    bus = _FakeBus()
    await run_execution_batch(run=run, bus=bus)

    agg = [p for ch, p in bus.events if ch == f"run.{run.run_id}.execution"]
    kinds = {p.get("event") for p in agg}
    assert "execution.batch_started" in kinds
    assert "execution.curve_point" in kinds  # mirrored onto aggregated channel
    assert "execution.batch_done" in kinds
    # curve points carry experiment_id so the UI can route to the right panel
    pts = [p for p in agg if p.get("event") == "execution.curve_point"]
    assert pts and all("experiment_id" in p for p in pts)
    assert (run.root / "execution" / "metrics.json").exists()


@pytest.mark.asyncio
async def test_agent_streams_thinking_events(tmp_path: Path) -> None:
    # No API key (conftest clears env) → mock provider, which still streams
    # content deltas through the same sink path.
    captured: list[dict[str, Any]] = []

    async def sink(payload: dict[str, Any]) -> None:
        captured.append(payload)

    agent = ExperimentAgent()
    request = RunRequest(
        project="moe-pimc",
        user_request="reduce compute",
        extra={"stream_publish": sink},
    )
    context = await agent.build_context(request)
    artifact = await agent.draft(request, context)

    events = {c.get("event") for c in captured}
    assert "thinking.start" in events
    assert "thinking.delta" in events
    assert "thinking.end" in events
    assert artifact.text.strip()
