"""Revision provenance guards and opt-in checks against an actual run archive."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.agents.idea.focused_research import focused_research_errors
from app.harness.agent_loop.revision_seed import load_revision_seed
from app.harness.schema.validator import validate_document
from app.storage.agent_context_store import load_agent_runtime_resources


def authored_candidate(root: Path) -> Path:
    path = root / "idea" / "idea_proposal.v1.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\nschema: proposal.v1\nproject: test-project\nagent: idea\n"
                    "research_question: An authored contract input\n"
                    "hypothesis: An authored contract hypothesis\n"
                    "novelty: An authored contract novelty\n---\nContract input.\n")
    return path


def test_revision_requires_actual_completed_trace(tmp_path: Path) -> None:
    candidate = authored_candidate(tmp_path)
    with pytest.raises(ValueError, match="completed, matching model trace"):
        load_revision_seed(tmp_path, project="test-project", agent="idea",
                           candidate_path=candidate, schema="proposal.v1")


def test_revision_rejects_another_project_or_task(tmp_path: Path) -> None:
    candidate = authored_candidate(tmp_path)
    with pytest.raises(ValueError, match="project binding"):
        load_revision_seed(tmp_path, project="another-project", agent="idea",
                           candidate_path=candidate, schema="proposal.v1")
    with pytest.raises(ValueError, match="belong to this task"):
        load_revision_seed(tmp_path / "another-task", project="test-project", agent="idea",
                           candidate_path=candidate, schema="proposal.v1")


def test_default_idea_references_do_not_override_project_metric_definitions() -> None:
    resources = load_agent_runtime_resources("idea")
    assert resources.manifest["files"]
    assert "当前代码" in resources.context
    for legacy in ("APE(°)", "APE（°）", "residual phase error", "RES <= -26",
                   "RES ≤ -26", "Paper_Total_0327", "提高动态切换下的消除能力"):
        assert legacy not in resources.context


def test_real_revision_keeps_source_receipts_without_replaying_tool_calls() -> None:
    configured = os.environ.get("MARS_TEST_REVISION_ARCHIVE_ROOT")
    if not configured:
        pytest.skip("requires an actual completed Idea archive; no substitute tool records are generated")
    root = Path(configured).resolve()
    candidate = root / "idea" / "idea_proposal.v1.md"
    parsed = validate_document(candidate.read_text(), expected_schema="proposal.v1")
    seed = load_revision_seed(root, project=parsed.metadata["project"], agent="idea",
                              candidate_path=candidate, schema="proposal.v1")
    assert seed.observations
    assert seed.receipt["acceptance_inherited"] is False
    assert seed.receipt["tool_calls_replayed"] is False
    for row in seed.observations:
        assert row["tool"].startswith("search.")
        assert not any(key.startswith("native_") for key in row)
        original = json.loads(Path(row["raw_ref"]).read_text())
        assert row["output"] == original["output"]
        assert row["args"] == original["args"]
        assert row["evidence_origin"]["source_ref"] == Path(row["raw_ref"]).relative_to(root).as_posix()
    assert focused_research_errors(parsed.metadata, list(seed.observations), root) == []
