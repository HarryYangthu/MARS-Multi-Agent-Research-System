"""Real-file integrity fixtures, not provider responses or RF measurements."""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
from typing import Any

import pytest

from app.bridge.cli_research_service import CliResearchService, configuration, initialize, verify_run
from app.cli_composition import CliAgents
from app.harness.agent_loop.trace import atomic_json
from app.harness.research_selection import freeze_selection, verify_trial_archive
from app.harness.research_trial import ResearchBudget, file_sha256, read_record


def archived_fixture(root: Path, name: str = "baseline", *, enriched: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    output = root / "execution" / name / "attempt_01"
    output.mkdir(parents=True, exist_ok=True)
    raw: dict[str, Any] = {"status": "completed", "operation": "train", "output": str(output),
        "exit_code": 0, "optimizer_steps": 1, "real_parameters": 100, "elapsed_seconds": 1.0,
        "validation": {"RES_db": 4.0}, "test": {"per_channel_db": [4.0, 5.0]}}
    # These human-authored values exercise serialization only, not experiment acceptance.
    trial = {**deepcopy(raw), "candidate_id": name, "candidate_path": None,
             "worker_identity": {"operation": "train", "candidate_sha256": None}}
    path = output / "result.json"
    atomic_json(path, trial if enriched else raw)
    state = {"trials": {name: trial}, "artifacts": {path.relative_to(root).as_posix(): file_sha256(path)}}
    return state, trial


@pytest.mark.parametrize("enriched", [False, True])
def test_raw_and_enriched_worker_archives_are_compatible(tmp_path: Path, enriched: bool) -> None:
    state, trial = archived_fixture(tmp_path, enriched=enriched)
    verify_trial_archive(tmp_path, state, "baseline", trial)
    verify_trial_archive(tmp_path, state, "baseline", dict(reversed(list(trial.items()))))


@pytest.mark.parametrize("field,value", [
    ("validation", {"RES_db": -100}), ("test", {"per_channel_db": [-100, 5.0]}),
    ("real_parameters", 1), ("optimizer_steps", 2), ("optimizer_steps", True),
    ("elapsed_seconds", 0), ("exit_code", 1), ("unexpected_measurement", 1),
])
def test_cached_measurements_must_equal_archived_payload(tmp_path: Path, field: str, value: Any) -> None:
    state, trial = archived_fixture(tmp_path)
    trial[field] = value
    with pytest.raises(ValueError, match="measurements differ"):
        verify_trial_archive(tmp_path, state, "baseline", trial)


def test_missing_cached_field_is_rejected(tmp_path: Path) -> None:
    state, trial = archived_fixture(tmp_path)
    del trial["elapsed_seconds"]
    with pytest.raises(ValueError, match="measurements differ"):
        verify_trial_archive(tmp_path, state, "baseline", trial)


@pytest.mark.parametrize("field", ["candidate_id", "candidate_path", "worker_identity"])
def test_enriched_archive_does_not_hide_conflicting_envelope(tmp_path: Path, field: str) -> None:
    state, trial = archived_fixture(tmp_path, enriched=True)
    path = Path(trial["output"]) / "result.json"
    raw = read_record(path)
    raw[field] = "changed archive envelope"
    atomic_json(path, raw)
    state["artifacts"][path.relative_to(tmp_path).as_posix()] = file_sha256(path)
    with pytest.raises(ValueError, match="envelope differs"):
        verify_trial_archive(tmp_path, state, "baseline", trial)


@pytest.mark.parametrize("change", ["file", "receipt", "other_trial", "outside", "missing", "symlink"])
def test_worker_archive_requires_original_file_location_and_receipt(tmp_path: Path, change: str) -> None:
    state, trial = archived_fixture(tmp_path)
    path = Path(trial["output"]) / "result.json"
    if change == "file":
        path.write_text(path.read_text() + "\n")
    elif change == "receipt":
        state["artifacts"].clear()
    elif change == "other_trial":
        trial["output"] = str(tmp_path / "execution/other/attempt_01")
    elif change == "outside":
        trial["output"] = str(tmp_path.parent / "outside/attempt_01")
    elif change == "missing":
        path.unlink()
    else:
        actual = path.with_name("original.json")
        path.rename(actual)
        path.symlink_to(actual)
    with pytest.raises((ValueError, FileNotFoundError)):
        verify_trial_archive(tmp_path, state, "baseline", trial)


def test_unexecuted_materialization_failure_cannot_supply_measurements(tmp_path: Path) -> None:
    state: dict[str, Any] = {"artifacts": {}}
    failed = {"status": "failed", "candidate_id": "round_01", "error": "Human-authored rejection fixture"}
    verify_trial_archive(tmp_path, state, "round_01", failed)
    for field, value in (("validation", {}), ("test", {}), ("real_parameters", 1),
                         ("worker_identity", {}), ("status", "completed")):
        with pytest.raises(ValueError, match="no archived output"):
            verify_trial_archive(tmp_path, state, "round_01", {**failed, field: value})


def actual_run(tmp_path: Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    configured = os.environ.get("MARS_TEST_STATIC_REPO")
    if not configured:
        pytest.skip("Set MARS_TEST_STATIC_REPO for real static repository integration checks")
    root = tmp_path / "run"
    initialize(Path(configured).resolve(), tmp_path / "absent.pth", root,
               "Integrity-only fixture; no simulation or model invocation", "deepseek-v4-flash", ResearchBudget())
    return root, read_record(root / "input/manifest.json"), read_record(root / "state.json")


def attach_checkpoint_fixture(root: Path, state: dict[str, Any], name: str) -> dict[str, Any]:
    archived, trial = archived_fixture(root, name)
    output = Path(trial["output"])
    (output / "best.pt").write_bytes(b"Checkpoint integrity fixture, not trained weights")
    trial.update(checkpoint_sha256=file_sha256(output / "best.pt"), candidate_sha256=None)
    path = output / "result.json"
    atomic_json(path, {key: value for key, value in trial.items()
                      if key not in {"candidate_id", "candidate_path", "worker_identity"}})
    archived["artifacts"][path.relative_to(root).as_posix()] = file_sha256(path)
    state["trials"].update(archived["trials"])
    state["artifacts"].update(archived["artifacts"])
    return trial


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["goal_met_within_budget", "goal_not_met"])
async def test_terminal_service_rechecks_selected_checkpoint(tmp_path: Path, status: str) -> None:
    root, _, state = actual_run(tmp_path)
    baseline = attach_checkpoint_fixture(root, state, "baseline")
    candidate = attach_checkpoint_fixture(root, state, "round_01") if status == "goal_met_within_budget" else None
    freeze_selection(root, state, baseline, candidate)
    state["status"] = status
    atomic_json(root / "state.json", state)
    service = CliResearchService(CliAgents("deepseek-v4-flash", configuration()["coding_loop"]))
    assert await service.run(root) == state
    assert not (root / "stages").exists()
    assert not (root / "resources").exists()
    targets = (baseline, candidate) if candidate else (baseline,)
    for selected in targets:
        assert selected is not None
        path = Path(selected["output"]) / "best.pt"
        original = path.read_bytes()
        path.write_bytes(b"Actual changed checkpoint bytes")
        with pytest.raises(ValueError, match="Selected checkpoint changed"):
            await service.run(root)
        path.write_bytes(original)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["goal_met_within_budget", "goal_not_met"])
async def test_terminal_service_requires_selection_receipt(tmp_path: Path, status: str) -> None:
    root, _, state = actual_run(tmp_path)
    state["status"] = status
    atomic_json(root / "state.json", state)
    service = CliResearchService(CliAgents("deepseek-v4-flash", configuration()["coding_loop"]))
    with pytest.raises(ValueError, match="requires a frozen selection"):
        await service.run(root)
    assert not (root / "stages").exists()


def test_verify_run_rejects_state_only_metric_changes(tmp_path: Path) -> None:
    root, manifest, state = actual_run(tmp_path)
    trial = attach_checkpoint_fixture(root, state, "baseline")
    verify_run(root, manifest, state)
    trial["validation"]["RES_db"] = -100
    with pytest.raises(ValueError, match="measurements differ"):
        verify_run(root, manifest, state)
