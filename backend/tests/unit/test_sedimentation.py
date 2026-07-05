from __future__ import annotations

from pathlib import Path

import pytest

from app.harness.kb.stores import reset_for_tests
from app.harness.sedimentation.hooks import on_agent_completed


PROPOSAL_TEXT = """---
schema: proposal.v1
project: pimc
agent: idea
research_question: "How to simplify the router?"
hypothesis: "Hard top-2 keeps RES within 1.5 dB."
novelty: "Stream-aware hard routing not in survey."
---

Body of proposal.
"""


def test_idea_extractor_writes_to_literature_and_methodology(tmp_path: Path) -> None:
    stores = reset_for_tests(base=tmp_path)
    result = on_agent_completed(
        agent="idea",
        project="pimc",
        run_id="run-1",
        artifact_text=PROPOSAL_TEXT,
    )
    assert result["chunks_written"] >= 1

    lit = stores.zone("literature").all()
    method = stores.zone("methodology").all()
    assert lit, "idea extractor should populate literature zone"
    assert method, "idea extractor should populate methodology zone"


RUN_LOG_TEXT = """---
schema: run_log.v1
project: pimc
agent: execution
run_id: "rid_x"
status: completed
metrics: { RES: -42.0 }
fingerprint_hash: "sha256:abc1234"
---

Body
"""


def test_execution_extractor_writes_run_archive(tmp_path: Path) -> None:
    stores = reset_for_tests(base=tmp_path)
    on_agent_completed(
        agent="execution",
        project="pimc",
        run_id="run-2",
        artifact_text=RUN_LOG_TEXT,
    )
    archive = stores.zone("run_archive").all()
    assert archive
    assert archive[0].metadata.get("fingerprint_hash") == "sha256:abc1234"
