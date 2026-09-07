"""Version selection on real local files, without model execution substitutes."""
from pathlib import Path

import pytest

from scripts.audit_idea_run import recorded_proposal


def test_audit_selects_recorded_version_and_never_falls_back_when_missing(tmp_path: Path) -> None:
    idea = tmp_path / "idea"
    idea.mkdir()
    first, second = idea / "idea_proposal.v1.md", idea / "idea_proposal.v2.md"
    first.write_text("Human-authored older file for a path-selection check")
    second.write_text("Human-authored newer file for a path-selection check")
    assert recorded_proposal(tmp_path, {"proposal_path": str(second)}) == second
    assert recorded_proposal(tmp_path, {"proposal_path": "idea/idea_proposal.v2.md"}) == second
    second.unlink()
    assert recorded_proposal(tmp_path, {"proposal_path": str(second)}) == second
    assert not recorded_proposal(tmp_path, {"proposal_path": str(second)}).exists()
    assert recorded_proposal(tmp_path, {}) == first


@pytest.mark.parametrize("path", ["../other/idea_proposal.v2.md", "summary.json", "idea/idea_proposal.v0.md", ""])
def test_audit_rejects_outside_or_unversioned_proposal_paths(tmp_path: Path, path: str) -> None:
    with pytest.raises(ValueError, match="proposal"):
        recorded_proposal(tmp_path, {"proposal_path": path})
