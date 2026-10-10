"""Explicit stopped-draft admission using real state and ledger files."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.bridge.orchestrator import Orchestrator, RunSession
from app.bridge.run_recovery import recovery_status
from app.bridge.stopped_draft_retry import stopped_draft_retry_blocker
from app.harness.llm.accounting import RunModelBudget
from app.harness.llm.provider_base import LLMConfig, Message
from tests.unit.test_run_recovery import checkpoint, session_at


def stopped_session(root: Path) -> tuple[Orchestrator, RunSession]:
    orch, session = session_at(root)
    session.termination = {"type": "cancelled", "scope": "owned_async_tasks",
                           "cleanup_complete": True, "interrupted_nodes": ["idea"]}
    checkpoint(session)
    return orch, session


def test_clean_stopped_draft_offers_new_attempt_only(tmp_path: Path) -> None:
    orch, session = stopped_session(tmp_path)
    view = recovery_status(orch, session.run.run_id, project="pimc")
    assert view["status"] == "recoverable"
    assert [item["action"] for item in view["actions"]] == ["retry"]
    assert session.termination is not None
    assert orch.owned_tasks.active(session.run.run_id) is None


@pytest.mark.parametrize("change", ["cleanup", "node", "tool"])
def test_ambiguous_stop_is_blocked(tmp_path: Path, change: str) -> None:
    orch, session = stopped_session(tmp_path)
    assert session.termination is not None
    if change == "cleanup":
        session.termination["cleanup_complete"] = False
    elif change == "node":
        session.termination["interrupted_nodes"].append("experiment")
    else:
        path = checkpoint(session, pending="tool")
        assert json.loads(path.read_text())["pending"] == "tool"
    assert recovery_status(orch, session.run.run_id, project="pimc")["status"] == "blocked"


def test_stopped_draft_still_preserves_unknown_and_exhausted_model_charges(tmp_path: Path) -> None:
    orch, session = stopped_session(tmp_path)
    budget = RunModelBudget(session.run.root)
    reservation = budget.reserve([Message("user", "Admission arithmetic only")],
        LLMConfig(provider="custom", model="no-model-called", max_tokens=8, max_retries=0), {})
    original = budget.path.read_bytes()
    assert recovery_status(orch, session.run.run_id, project="pimc")["status"] == "blocked"
    assert budget.path.read_bytes() == original
    budget.settle(reservation, usage=None, complete=False, outcome="cancelled")
    for _ in range(budget.configuration["limits"]["max_model_requests"] - 1):
        reserved = budget.reserve([Message("user", "Admission arithmetic only")],
            LLMConfig(provider="custom", model="no-model-called", max_tokens=8, max_retries=0), {})
        budget.settle(reserved, usage=None, complete=False, outcome="cancelled")
    before = budget.path.read_bytes()
    view = recovery_status(orch, session.run.run_id, project="pimc")
    assert view["status"] == "blocked" and "累计调用预算" in view["message"]
    assert budget.path.read_bytes() == before
