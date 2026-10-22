"""Pure receipt rejection cases over real process-loss ledger/checkpoint files."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest

from app.bridge.run_model_recovery import recover_interrupted_run_model_requests
from app.harness.agent_loop.interrupted_model_resume import (
    checkpoint_interrupted_model_receipt, interrupted_model_receipt,
)
from tests.unit.test_commander_model_recovery import _orphan, _state
from tests.unit.test_run_model_recovery import _bound_stage


@pytest.fixture(scope="module")
def recovered_metadata_template(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    tmp_path = tmp_path_factory.mktemp("recovered-model-metadata")
    run, correlation, checkpoint = _bound_stage(tmp_path)
    _orphan(tmp_path, correlation)
    assert recover_interrupted_run_model_requests(run)["status"] == "recovered"
    state = json.loads(checkpoint.read_text())
    events = [json.loads(line) for line in checkpoint.with_name("events.jsonl").read_text().splitlines()]
    return state, events, _state(tmp_path)


@pytest.fixture()
def actual_orphan_metadata(
    recovered_metadata_template: tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    # All parser mutations use isolated copies of one actual recovered ledger.
    return deepcopy(recovered_metadata_template)


def test_verified_receipt_never_invents_response_refunds_or_replays_history(
    actual_orphan_metadata: tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]],
) -> None:
    state, events, ledger = actual_orphan_metadata
    original = deepcopy((state, events, ledger))
    receipt = interrupted_model_receipt(state, events, ledger)
    assert receipt and not receipt["budgets_reset"] and not receipt["response_created"]
    assert not receipt["tools_replayed"] and not receipt["acceptance_inherited"]
    assert (state, events, ledger) == original


@pytest.mark.parametrize("change", ["response", "tool", "observation", "reflection", "wrong_request",
    "wrong_phase", "wrong_model", "wrong_actor", "charged_refund", "attempt_refund", "unknown_usage_claimed",
    "pending_tool", "pending_batch", "unreconciled", "late_request", "wrong_correlation"])
def test_partial_or_received_output_cannot_become_resume_permission(
    actual_orphan_metadata: tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]], change: str,
) -> None:
    state, events, ledger = actual_orphan_metadata
    row = next(iter(ledger["requests"].values()))
    if change in {"response", "tool", "observation", "reflection"}:
        kind = {"response": "model_response", "tool": "tool_dispatch"}.get(change, change)
        events.append({"kind": kind, "correlation": state["correlation"]})
    elif change == "wrong_request": state["counts"]["model_requests"] += 1
    elif change == "wrong_phase": state["next_phase"] = "reflect"
    elif change == "wrong_model": row["model"] = "another-model"
    elif change == "wrong_actor": row["reconciliation"]["actor"] = "not-mars-recovery"
    elif change == "charged_refund": row["charged_tokens"] = 0
    elif change == "attempt_refund": row["charged_attempts"] = 0
    elif change == "unknown_usage_claimed": row["usage_complete"] = True
    elif change == "pending_tool": state["pending"] = "tool"
    elif change == "pending_batch": state["pending_batch"] = [{"tool": "uncertain"}]
    elif change == "unreconciled": row["status"] = "reconciliation_required"
    elif change == "late_request":
        ledger["requests"]["later"] = {**deepcopy(row), "started_at": row["started_at"] + 1}
    elif change == "wrong_correlation": row["correlation"] = {**row["correlation"], "invocation_id": "another"}
    assert interrupted_model_receipt(state, events, ledger) is None


def test_stream_deltas_remain_observations_of_incomplete_transport_and_are_not_executed(
    actual_orphan_metadata: tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]],
) -> None:
    state, events, ledger = actual_orphan_metadata
    events.append({"kind": "provider_stream_progress", "correlation": state["correlation"],
                   "visible": '{"actions":[{"tool":"run.start",'})
    original_history = deepcopy(state.get("history", []))
    receipt = interrupted_model_receipt(state, events, ledger)
    assert receipt and not receipt["tools_replayed"]
    assert state.get("history", []) == original_history


def test_filesystem_wrapper_requires_exact_checkpoint_reference(tmp_path: Path) -> None:
    run, correlation, checkpoint = _bound_stage(tmp_path)
    request_id = _orphan(tmp_path, correlation)
    assert recover_interrupted_run_model_requests(run)["status"] == "recovered"
    state = json.loads(checkpoint.read_text())
    assert checkpoint_interrupted_model_receipt(checkpoint.parent, state, run_root=run.root)
    ledger = _state(tmp_path)
    ledger["requests"][request_id]["reconciliation"]["evidence_refs"] = ["unrelated-checkpoint.json"]
    (tmp_path / "resources/model_budget.v1.json").write_text(json.dumps(ledger))
    assert checkpoint_interrupted_model_receipt(checkpoint.parent, state, run_root=run.root) is None
