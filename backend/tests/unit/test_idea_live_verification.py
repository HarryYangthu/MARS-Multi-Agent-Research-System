"""Read real local review files without treating an old candidate as accepted."""
from pathlib import Path

import pytest

from scripts.verify_focused_idea import revision_analysis, is_terminal


def test_revision_input_preserves_rejected_source_files(tmp_path: Path) -> None:
    candidate = tmp_path / "idea/candidates/attempt/candidate.md"
    review = tmp_path / "idea/reviews/attempt/review.json"
    candidate.parent.mkdir(parents=True)
    review.parent.mkdir(parents=True)
    candidate.write_text("Authored unaccepted design for a file-reading test.")
    review.write_text('{"accepted":false,"issues":["Check the source"]}')
    original = {path: path.read_bytes() for path in (candidate, review)}
    text = revision_analysis(tmp_path)
    assert "未通过验收" in text and "不是已确认的方法或论文证据" in text
    assert candidate.read_text() in text and "Check the source" in text
    assert all(path.read_bytes() == data for path, data in original.items())


def test_revision_input_rejects_missing_candidate(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no candidate"):
        revision_analysis(tmp_path)


@pytest.mark.parametrize("state", ["waiting_review", "failed", "done", "cancelled"])
def test_verification_stops_at_real_terminal_states_without_approval(state: str) -> None:
    assert is_terminal({"states": {"idea": state}})


@pytest.mark.parametrize("state", ["pending", "running", "approved"])
def test_verification_does_not_confuse_started_or_approved_with_finished(state: str) -> None:
    assert not is_terminal({"states": {"idea": state}})


def test_idea_revision_receives_latest_real_draft_without_inventing_a_success(tmp_path: Path) -> None:
    from app.storage.run_store import RunStore
    from app.bridge.agent_runner import load_agent_handoff_context
    run = RunStore(tmp_path).create(task="revision-file-input", project="pimc", entrypoint="idea")
    # These are human-authored file fixtures, not a model or execution receipt.
    (run.root / "idea/idea_proposal.v1.md").write_text("First authored draft")
    (run.root / "idea/idea_proposal.v2.md").write_text("Current authored draft")
    upstream, _ = load_agent_handoff_context(run, "idea", revision_reason="Clarify acceptance split")
    assert "Current authored draft" in upstream["revision_candidate"]
    assert "First authored draft" not in upstream["revision_candidate"]
    assert "Clarify acceptance split" in upstream["human_revision_request"]
