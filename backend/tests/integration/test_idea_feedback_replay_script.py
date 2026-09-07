"""Validate real archived inputs and tampering refusal without calling models."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts.replay_idea_feedback_live import file_sha, load_parent, request_payload


ARCHIVE = Path(__file__).resolve().parents[3] / "docs/evaluation/idea_multiagent_20260908/roles_attempt_02"


def test_completed_real_archive_preserves_the_actual_parent_and_feedback() -> None:
    inputs = load_parent(ARCHIVE, replay_run_id="read-only-replay-validation")
    original = request_payload(json.loads((ARCHIVE / "role_calls/0005_evolution/request.json").read_text()))["requests"][0]
    assert [item.model_dump(mode="json") for item in inputs.evolution.parents] == original["parents"]
    assert inputs.evolution.operator == original["operator"]
    assert inputs.evolution.round_index == original["round_index"]
    assert {review.hypothesis_id for review in inputs.evolution.parent_reflections} == {
        parent.hypothesis_id for parent in inputs.evolution.parents}
    assert inputs.evolution.next_round_guidance
    assert inputs.context.run_id == "read-only-replay-validation"
    assert all(config.max_retries == 0 for config in inputs.configs.values())
    assert all(file_sha(ARCHIVE / name) == sha for name, sha in inputs.parent_hashes.items())


@pytest.mark.parametrize("change,message", [
    ("pending", "not pending or failed"),
    ("feedback", "review differs from the actual model response"),
    ("scenario", "scenario hash mismatch"),
])
def test_parent_mismatch_or_unfinished_run_is_rejected_without_a_model(
    tmp_path: Path, change: str, message: str,
) -> None:
    parent = tmp_path / "parent"
    shutil.copytree(ARCHIVE, parent, ignore=shutil.ignore_patterns("*.zip"))
    if change == "pending":
        path = parent / "summary.json"
        value = json.loads(path.read_text())
        value["status"] = "running"
        path.write_text(json.dumps(value))
    elif change == "feedback":
        parent_id = load_parent(parent, replay_run_id="read-only").evolution.parents[0].hypothesis_id
        path = parent / "idea/discovery/state.v1.json"
        value = json.loads(path.read_text())
        next(item for item in value["reflections"] if item["hypothesis_id"] == parent_id)["correctness"] = "Tampered record"
        path.write_text(json.dumps(value))
    else:
        path = parent / "input/scenario.yaml"
        path.write_text(path.read_text() + "\n# Modified source input\n")
    with pytest.raises(ValueError, match=message):
        load_parent(parent, replay_run_id="read-only-rejection")
