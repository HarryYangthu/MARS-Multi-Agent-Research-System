from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agents.base import Artifact, ContextPack, RunRequest
from app.agents.writing import agent as writing_module
from app.agents.writing.agent import WritingAgent
from app.harness.llm.model_registry import AgentConfig
from app.harness.schema.frontmatter_parser import dumps as fm_dumps


def _writing_config() -> AgentConfig:
    return AgentConfig(
        name="writing",
        enabled=True,
        output_schema="report.v1",
        model_provider="mock",
        model_name="mock-1",
        temperature=0.0,
        max_tokens=1024,
        debate_enabled=True,
        debate_rounds=1,
        debate_participants=(),
        tools=(),
        raw={},
    )


@pytest.mark.asyncio
async def test_writing_agent_keeps_full_debate_out_of_frontmatter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for ref in (
        "execution/run_log.approved.md",
        "execution/metrics.json",
        "execution/batch_summary.json",
        "execution/loss_curves_16.png",
        "diagnosis/diagnosis.v2.md",
    ):
        path = tmp_path / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("evidence", encoding="utf-8")

    metadata = {
        "schema": "report.v1",
        "project": "pimc",
        "agent": "writing",
        "deliverable_type": "research_report",
        "target_audience": "phd_advisor",
        "chain_refs": {"runs": ["execution/metrics.json"]},
    }
    final = Artifact(
        text=fm_dumps(metadata, "# Report\n\nBody"),
        schema_id="report.v1",
        metadata=dict(metadata),
        body="# Report\n\nBody",
    )
    critic_text = "---\nschema: report.v1\n---\n# Critique\n\nNeeds measured evidence."

    async def fake_run_debate(**_: object) -> SimpleNamespace:
        return SimpleNamespace(
            final_artifact=final,
            rounds=1,
            turns=[SimpleNamespace(role="critic", text=critic_text)],
            mode=SimpleNamespace(value="real_multi_model"),
            transcript_md="# Full transcript\n\n" + ("long text " * 200),
        )

    monkeypatch.setattr(writing_module, "run_debate", fake_run_debate)
    agent = WritingAgent(agent_config=_writing_config())

    artifact = await agent.draft(
        RunRequest(
            project="pimc",
            user_request="write report",
            extra={"run_root": str(tmp_path)},
        ),
        ContextPack(system="", project="pimc", task="write report"),
    )

    assert "debate_transcript_full" not in artifact.metadata
    assert "Full transcript" not in artifact.text
    assert artifact.metadata["debate_transcript_ref"] == "debate_transcript.vN.md"
    assert "execution/metrics.json" in artifact.metadata["chain_refs"]["runs"]
    assert "execution/batch_summary.json" in artifact.metadata["chain_refs"]["runs"]
    assert "execution/loss_curves_16.png" in artifact.metadata["chain_refs"]["runs"]
    critique = artifact.metadata["debate_summary"]["reviewer_critiques"][0]
    assert critique.startswith("# Critique")
    assert "schema: report.v1" not in critique
