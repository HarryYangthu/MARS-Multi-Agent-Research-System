from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.bridge.evaluation_service import (
    build_artifact_evaluation_summary,
    emit_artifact_evaluation_event,
)
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.schema.frontmatter_parser import dumps as fm_dumps
from app.storage.artifact_store import ArtifactStore
from app.storage.run_store import RunStore


def _proposal_text() -> str:
    metadata = {
        "schema": "proposal.v1",
        "project": "moe-pimc",
        "agent": "idea",
        "research_question": "How to simplify the router?",
        "hypothesis": "Hard top-2 keeps RES within 1.5 dB.",
        "novelty": "Stream-aware routing absent in surveys.",
    }
    return fm_dumps(metadata, "Body of proposal\n")


def test_build_artifact_evaluation_summary_compacts_reports(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="eval-bridge", project="moe-pimc")
    ref = ArtifactStore(run).write(text=_proposal_text())

    summary = build_artifact_evaluation_summary(
        run=run,
        ref=ref,
        node_key="idea",
    )

    assert summary["agent"] == "idea"
    assert summary["node"] == "idea"
    assert summary["artifact_ref"] == "idea/idea_proposal.v1.md"
    assert summary["decision"] in {"pass", "warn", "revise", "block", "fail"}
    assert summary["report_count"] == 3
    assert isinstance(summary["reports"], list)
    assert summary["policy"]["schema"] == "evaluation_policy_decision.v1"
    assert summary["policy"]["review_priority"] in {
        "normal",
        "elevated",
        "high",
        "critical",
    }
    assert {report["evaluator"] for report in summary["reports"]} == {
        "contract.schema_validity",
        "contract.provenance",
        "artifact_quality.rubric",
    }


@pytest.mark.asyncio
async def test_emit_artifact_evaluation_event_writes_file_and_publishes(
    tmp_path: Path,
) -> None:
    run = RunStore(tmp_path).create(task="eval-bridge", project="moe-pimc")
    ref = ArtifactStore(run).write(text=_proposal_text())
    bus = InProcessEventBus()

    async with bus.subscribe(f"run.{run.run_id}.evaluation") as queue:
        payload = await emit_artifact_evaluation_event(
            run=run,
            ref=ref,
            node_key="idea",
            bus=bus,
        )
        event = await asyncio.wait_for(queue.get(), timeout=1.0)

    assert event.payload == payload
    assert payload["event"] == "evaluation.artifact_evaluated"
    assert payload["artifact_ref"] == "idea/idea_proposal.v1.md"
    assert payload["policy"]["scope"] == "artifact"
    events_path = run.subdir("events") / "evaluation_events.jsonl"
    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
    ]
    assert events[-1]["event"] == "evaluation.artifact_evaluated"
