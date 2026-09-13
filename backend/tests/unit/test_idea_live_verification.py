"""Read real local review files without treating an old candidate as accepted."""
from pathlib import Path

import pytest

from scripts.verify_focused_idea import revision_analysis


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
