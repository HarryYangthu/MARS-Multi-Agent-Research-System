"""Read-only proof for a fresh generation after a lost model owner was acknowledged."""
from __future__ import annotations

from datetime import datetime
from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from app.harness.agent_loop.trace import audit_trace, digest
from app.harness.llm.accounting import (
    ResourceBudgetError, RunModelBudget, charged_model_attempts, reserved_model_attempts,
)


def recoverable_model_metadata_state(state: dict[str, Any], facts: dict[str, Any],
                                     events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Plan only an SDK-metadata snapshot repair; caller must prove owner loss."""
    if (state.get("pending") != "model" or state.get("pending_batch")
            or state.get("status") not in {"running", "interrupted", "model_error"}
            or facts.get("trace_mode") != "full" or facts.get("resume_available") is not True):
        return None
    try:
        if (any(state.get(key) != facts.get(key) for key in
                ("status", "pending", "fingerprint", "correlation", "usage"))
                or [event["event_seq"] for event in events] != list(range(1, len(events) + 1))):
            return None
        seq = facts["event_seq"]
        if type(seq) is not int or not 0 <= seq <= len(events):
            return None
        metadata_kinds = {"provider_request", "sdk_attempt_started", "sdk_attempt_succeeded", "sdk_attempt_failed",
                          "sdk_retry_scheduled", "sdk_retry_deferred", "sdk_stream_progress"}
        correlation = state["correlation"]
        if any(event.get("kind") not in metadata_kinds or event.get("correlation") != correlation
               for event in events[seq:]):
            return None
        index = max(i for i, event in enumerate(events) if event.get("kind") == "model_request")
        if (events[index].get("request") != state["counts"]["model_requests"]
                or events[index].get("correlation") != correlation
                or any(event.get("kind") not in metadata_kinds for event in events[index + 1:])):
            return None
        count_kinds = {"model_requests": "model_request", "model_responses": "model_response",
                       "tool_dispatches": "tool_dispatch", "observations": "observation",
                       "sdk_attempts": "sdk_attempt_started", "reflections": "reflection"}
        actual = {key: sum(event.get("kind") == kind for event in events) for key, kind in count_kinds.items()}
        for key, count in actual.items():
            values = (state["counts"].get(key, 0), facts["counts"].get(key, 0))
            if (any(type(value) is not int or value < 0 for value in values)
                    or key != "sdk_attempts" and any(value != count for value in values)
                    or key == "sdk_attempts" and any(value > count for value in values)):
                return None
        restored = deepcopy(state)
        restored["counts"]["sdk_attempts"] = actual["sdk_attempts"]
        restored["usage_complete"] = False
        return restored
    except (KeyError, ValueError, TypeError, AttributeError):
        return None


def interrupted_model_receipt(state: dict[str, Any], events: list[dict[str, Any]],
                              ledger: dict[str, Any]) -> dict[str, Any] | None:
    """No response is inherited; all prior counters and unknown charges remain."""
    if (state.get("status") not in {"running", "interrupted", "model_error"}
            or state.get("pending") != "model" or state.get("pending_batch")):
        return None
    try:
        correlation = state["correlation"]
        if any(not isinstance(correlation.get(key), str) or not correlation[key]
               for key in ("invocation_id", "node_id", "task_id", "parent_task_id", "trace_id")):
            return None
        index = max(i for i, event in enumerate(events) if event.get("kind") == "model_request")
        request, tail = events[index], events[index + 1:]
        if (request.get("correlation") != correlation
                or request.get("request") != state["counts"]["model_requests"]
                or request.get("phase") != state.get("next_phase")
                or any(event.get("kind") in {"model_response", "tool_dispatch", "observation", "reflection"}
                       or event.get("correlation") != correlation for event in tail)):
            return None
        if ledger.get("schema") != "runtime.model_budget.v1":
            return None
        rows = ledger["requests"]
        if any(row.get("status") in {"in_flight", "reconciliation_required"} for row in rows.values()):
            return None
        request_time = datetime.fromisoformat(request["time"]).timestamp()
        matched = [(identifier, row) for identifier, row in rows.items()
                   if row.get("correlation") == correlation and row.get("started_at", -1) >= request_time]
        if len(matched) != 1:
            return None
        identifier, row = matched[0]
        receipt = row.get("reconciliation", {})
        if (row.get("usage_complete") is not False or row.get("usage") is not None
                or row.get("provider") != request.get("provider") or row.get("model") != request.get("model")
                or row.get("charged_tokens") != row.get("reserved_tokens")
                or row.get("charged_cost") != row.get("reserved_cost")):
            return None
        recovery_kind = "interrupted_model_acknowledged"
        if row.get("status") == "abandoned":
            if (receipt.get("actor") != "mars-run-recovery" or not receipt.get("reason")
                    or not receipt.get("evidence_refs")
                    or row.get("charged_attempts") != reserved_model_attempts(row)
                    or not request_time <= row["started_at"] <= row["owner_lost_at"] <= row["reconciled_at"]):
                return None
        elif row.get("status") in {"failed", "cancelled"}:
            network_errors = {"APITimeoutError", "TimeoutError", "ReadTimeout", "ConnectTimeout",
                              "APIConnectionError", "ConnectError", "NetworkError", "TransportError"}
            failure_events = [event for event in tail if (
                row["status"] == "failed" and event.get("kind") == "model_error"
                and event.get("error_type") in network_errors and event.get("reason") is None) or (
                row["status"] == "cancelled" and event.get("kind") in {"model_cancelled", "interrupted"}
                and event.get("pending", "model") == "model")]
            if len(failure_events) != 1:
                return None
            failure_time = datetime.fromisoformat(failure_events[0]["time"]).timestamp()
            actual_attempts = sum(event.get("kind") == "sdk_attempt_started" for event in tail)
            if (not request_time <= row["started_at"] <= row.get("finished_at", -1) <= failure_time
                    or charged_model_attempts(row) != row.get("charged_attempts")
                    or row.get("observed_attempts") != actual_attempts
                    or row.get("attempts_complete") is True and row["charged_attempts"] != actual_attempts):
                return None
            recovery_kind = "settled_model_failure"
        else:
            return None
        for component in ("input", "output"):
            reserved = f"reserved_{component}_tokens"
            if reserved in row and row.get(f"charged_{component}_tokens") != row[reserved]:
                return None
        return {"recovery_kind": recovery_kind, "request": request["request"],
                "request_event_seq": request["event_seq"], "reservation_id": identifier,
                "reservation_sha256": digest(row), "charged_attempts": row["charged_attempts"],
                "budgets_reset": False, "response_created": False, "acceptance_inherited": False,
                "tools_replayed": False, "usage_complete": False}
    except (ValueError, KeyError, TypeError, AttributeError, ResourceBudgetError):
        return None


def checkpoint_interrupted_model_receipt(root: Path, state: dict[str, Any], *,
                                         run_root: Path) -> dict[str, Any] | None:
    """Verify actual checkpoint/events/ledger bytes before resume admission."""
    if state.get("pending") != "model":
        return None
    try:
        if (not root.resolve().is_relative_to(run_root.resolve())
                or state.get("correlation", {}).get("invocation_id") != root.name
                or state.get("correlation", {}).get("trace_id") != run_root.name):
            return None
        audit = audit_trace(root)
        if not audit.get("consistent") or any(audit["facts"].get(key) != state.get(key)
                for key in ("status", "counts", "usage", "usage_complete", "fingerprint", "pending", "correlation")):
            return None
        events = [json.loads(line) for line in (root / "events.jsonl").read_text().splitlines()]
        ledger = RunModelBudget(run_root).recovery_snapshot()
        receipt = interrupted_model_receipt(state, events, ledger)
        if receipt is None:
            return None
        if receipt["recovery_kind"] == "interrupted_model_acknowledged":
            refs = ledger["requests"][receipt["reservation_id"]]["reconciliation"]["evidence_refs"]
            if ((root / "checkpoint.json").relative_to(run_root).as_posix() not in refs
                    or f'resources/model_budget.v1.json#requests/{receipt["reservation_id"]}' not in refs):
                return None
        return receipt
    except (OSError, ValueError, KeyError, TypeError, ResourceBudgetError):
        return None
