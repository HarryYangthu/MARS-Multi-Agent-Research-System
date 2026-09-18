"""Actual files exercise sealing and identities; no agents, tools or services are replaced."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from app.agents.base import RunRequest
from app.agents.research_cli import ResearchExperimentAgent
from app.harness.research_selection import freeze_selection, load_selection, worker_identity
from app.harness.research_trial import file_sha256, read_record


def evidence(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    records = []
    for name in ("baseline", "round_01"):
        output = root / "execution" / name
        output.mkdir(parents=True)
        # File integrity fixtures, explicitly not purported trained checkpoints.
        (output / "best.pt").write_bytes(f"checkpoint integrity fixture: {name}".encode())
        record = {"candidate_id": name, "output": str(output),
                  "checkpoint_sha256": file_sha256(output / "best.pt"),
                  "candidate_sha256": None, "validation": {"RES_db": 4.0}}
        if name != "baseline":
            source = root / "candidate.py"
            source.write_text("# human-authored source integrity fixture\n")
            record.update(candidate_path=str(source), candidate_sha256=file_sha256(source))
        records.append(record)
    baseline, candidate = records
    state: dict[str, Any] = {"artifacts": {}, "trials": {"baseline": baseline, "round_01": candidate}}
    return state, baseline, candidate


@pytest.mark.parametrize("has_candidate", [True, False])
def test_selection_is_durable_and_cannot_be_reselected(tmp_path: Path, has_candidate: bool) -> None:
    state, baseline, candidate = evidence(tmp_path)
    assert load_selection(tmp_path, state) is None
    selected = candidate if has_candidate else None
    freeze_selection(tmp_path, state, baseline, selected)
    persisted = read_record(tmp_path / "state.json")
    selection = load_selection(tmp_path, persisted)
    assert selection == {"schema": "research.selection.v1", "basis": "validation only",
                         "baseline": baseline, "candidate": selected}
    assert persisted == state and state["status"] == "finalizing"
    assert state["selected"] == ("round_01" if has_candidate else None)
    with pytest.raises(ValueError, match="immutable"):
        freeze_selection(tmp_path, state, baseline, selected)


@pytest.mark.parametrize("target,error", [
    ("seal", "Frozen selection changed"), ("checkpoint", "Selected checkpoint changed"),
    ("source", "Selected source changed"), ("evidence", "Selected training evidence changed"),
    ("identity", "Selected candidate identity changed"),
])
def test_selection_rejects_actual_file_and_state_tampering(tmp_path: Path, target: str, error: str) -> None:
    state, baseline, candidate = evidence(tmp_path)
    freeze_selection(tmp_path, state, baseline, candidate)
    if target == "seal":
        path = tmp_path / "experiment/selection.json"
        path.write_text(path.read_text() + "\n")
    elif target == "checkpoint":
        (Path(candidate["output"]) / "best.pt").write_bytes(b"changed checkpoint")
    elif target == "source":
        Path(candidate["candidate_path"]).write_text("# changed source\n")
    elif target == "evidence":
        state["trials"]["round_01"]["validation"]["RES_db"] = -100
    else:
        state["selected"] = "another_candidate"
    with pytest.raises(ValueError, match=error):
        load_selection(tmp_path, state)


@pytest.mark.parametrize("final_state", [
    {"selected": None, "trials": {}}, {"selected": "round_01", "trials": {}},
    {"trials": {"final_baseline": {"status": "failed"}}},
    {"trials": {"final_candidate": {"status": "completed"}}},
])
def test_finalization_cannot_resume_search_without_a_seal(tmp_path: Path, final_state: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="requires a frozen selection"):
        load_selection(tmp_path, {"artifacts": {}, **final_state})


def test_worker_identity_changes_with_actual_inputs(tmp_path: Path) -> None:
    _, _, candidate = evidence(tmp_path)
    source = Path(candidate["candidate_path"])
    manifest = {"source_snapshot": str(tmp_path / "snapshot"), "protocol_sha256": "protocol-v1"}
    initial = worker_identity(manifest, source, None, "train")
    assert initial == {"operation": "train", "source_snapshot": manifest["source_snapshot"],
                       "protocol_sha256": "protocol-v1", "candidate_sha256": file_sha256(source),
                       "checkpoint_sha256": None}
    assert worker_identity(manifest, source, None, "preflight") != initial
    assert worker_identity({**manifest, "protocol_sha256": "protocol-v2"}, source, None, "train") != initial
    assert worker_identity({**manifest, "source_snapshot": "another_snapshot"}, source, None, "train") != initial
    assert worker_identity(manifest, None, None, "train") != initial
    final = worker_identity(manifest, source, candidate, "train")
    assert final["operation"] == "finalize" and final["checkpoint_sha256"] == candidate["checkpoint_sha256"]
    source.write_text("# changed implementation\n")
    assert worker_identity(manifest, source, None, "train") != initial
    with pytest.raises(ValueError, match="Selected candidate code changed"):
        worker_identity(manifest, source, candidate, "train")


def test_worker_identity_rejects_modified_checkpoint(tmp_path: Path) -> None:
    _, baseline, _ = evidence(tmp_path)
    manifest = {"source_snapshot": str(tmp_path / "snapshot"), "protocol_sha256": "protocol-v1"}
    assert worker_identity(manifest, None, baseline, "train")["operation"] == "finalize"
    (Path(baseline["output"]) / "best.pt").write_bytes(b"different checkpoint")
    with pytest.raises(ValueError, match="Selected checkpoint changed"):
        worker_identity(manifest, None, baseline, "train")


def test_experiment_protocol_ack_requires_exact_frozen_values() -> None:
    expected = {"seed": 2026, "max_steps": 50, "split_guard": 512,
                "train_fraction": .8, "validation_fraction": .1, "scale": 32768.0,
                "fs": 245.76, "band": [-29.74, -24.74],
                "metric": "10log10(mean_channel(Perr/Pnf)); lower is better",
                "selection": "validation only; final test evaluated only after candidate selection",
                "initialization": "from scratch with the fixed seed", "data_sha256": "a" * 64,
                "batch_samples": 8192, "context": 144}
    frozen = dict(expected)
    assert ResearchExperimentAgent.protocol_ack(frozen) == expected
    # Construct the real agent solely to inspect its delivery schema; no API invocation.
    agent = ResearchExperimentAgent("deepseek-v4-flash", {})
    request = RunRequest(project="pimc", user_request="Design a bounded experiment",
                         upstream_artifacts={"frozen_protocol": json.dumps(frozen), "goal": '{"rounds":1}'})
    schema = agent.submission_schema(request)
    assert "protocol_ack" in schema["required"]
    ack_schema = schema["properties"]["protocol_ack"]
    assert ack_schema == {"const": expected}
    jsonschema.validate(expected, ack_schema)
    for key in expected:
        altered = deepcopy(expected)
        altered[key] = "changed frozen value"
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(altered, ack_schema)
        del altered[key]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(altered, ack_schema)
