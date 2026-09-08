"""Pure stopping decisions and optional actual archived failure replay."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from app.agents.idea.research_stop import lead_evidence_stop
from app.harness.agent_loop.stop import LoopStopView


def test_empty_research_keeps_available_evidence_budget(tmp_path: Path) -> None:
    view = LoopStopView("before_model", "", [], {"tool_dispatches": 0}, "act")
    assert lead_evidence_stop(view, run_root=tmp_path, min_sources=2,
                              max_delegations=2, max_tool_steps=5, can_delegate=True) is None


def test_unavailable_research_cannot_be_reported_as_a_success(tmp_path: Path) -> None:
    view = LoopStopView("before_model", "", [], {"tool_dispatches": 0}, "act")
    stop = lead_evidence_stop(view, run_root=tmp_path, min_sources=2,
                              max_delegations=2, max_tool_steps=5, can_delegate=False)
    assert stop is not None and stop.status == "evidence_unavailable"
    assert stop.details["verified_report_publications"] == 0
    assert stop.details["usable_as_final_evidence"] is False
    assert not list(tmp_path.iterdir())


def test_stop_hook_does_not_replace_candidate_validation(tmp_path: Path) -> None:
    view = LoopStopView("after_validation", "", [], {"tool_dispatches": 0}, "act")
    assert lead_evidence_stop(view, run_root=tmp_path, min_sources=2,
                              max_delegations=2, max_tool_steps=5, can_delegate=False) is None


def test_actual_failed_delegations_stop_before_more_proposal_calls() -> None:
    supplied = os.environ.get("MARS_IDEA_FAILED_RESEARCH_ROOT")
    if not supplied:
        pytest.skip("set an actual failed research run directory for replay")
    root = Path(supplied)
    checkpoint = next(root.glob("agent_traces/idea/*/checkpoint.json"))
    original = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    state = json.loads(checkpoint.read_text())
    view = LoopStopView("before_model", state["candidate"], state["history"], state["counts"], "act")
    stop = lead_evidence_stop(view, run_root=root, min_sources=2,
                              max_delegations=3, max_tool_steps=5, can_delegate=True)
    assert stop is not None and stop.status == "evidence_unavailable"
    assert len(stop.details["started_delegations"]) == 3
    assert stop.details["verified_report_publications"] == 0
    assert hashlib.sha256(checkpoint.read_bytes()).hexdigest() == original
