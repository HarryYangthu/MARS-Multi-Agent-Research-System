"""Planning contracts and actual seed propagation; no model or tool substitutes."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from app.agents.base import RunRequest
from app.agents.execution.agent import ExecutionAgent
from app.bridge.agent_runner import _planned_seed
from app.harness.tools.registry import ToolContext, reset_for_tests


def test_explicit_zero_seed_is_preserved_and_invalid_seeds_fail() -> None:
    assert _planned_seed("arbitrary-name", {"seed": 0}) == 0
    assert _planned_seed("other-name", {"seed": 42}) == 42
    assert _planned_seed("same-name", {}) == _planned_seed("same-name", {})
    for value in (True, -1, 0.5, "0"):
        with pytest.raises(ValueError, match="seed"):
            _planned_seed("name", {"seed": value})


def test_plan_schema_cannot_claim_execution_or_emit_measured_metrics() -> None:
    agent = ExecutionAgent()
    schema = agent.submission_schema(RunRequest(project="regression", user_request="Use seed 0"))
    assert schema is not None
    plan = {"schema": "run_log.v1", "agent": "execution", "project": "regression", "run_id": "pending",
            "status": "interrupted", "execution_phase": "planned", "is_mock": False,
            "fingerprint_hash": schema["properties"]["fingerprint_hash"]["const"],
            "metrics": {"planned_experiments": 1},
            "planned_experiments": [{"name": "comparison", "config": {"seed": 0}}]}
    validator = Draft202012Validator(schema)
    assert validator.is_valid(plan)
    updates: list[dict[str, Any]] = [{"status": "completed"}, {"metrics": {"candidate_mse": 0.01}},
                                    {"planned_experiments": []}, {"is_mock": True}]
    for update in updates:
        candidate = deepcopy(plan)
        candidate.update(update)
        assert not validator.is_valid(candidate)
    assert "execution.simulation_runner" not in agent.config.tools
    assert "execution.batch_runner" not in agent.config.tools


@pytest.mark.asyncio
async def test_planner_cannot_launch_jobs_before_bridge_approval(tmp_path: Path) -> None:
    result = await reset_for_tests().dispatch("execution.batch_runner", {"experiments": []},
        ToolContext(run_id="plan-only", project="pimc", agent="execution", extra={"run_root": str(tmp_path)}))
    assert not result.ok and result.status == "not_allowed"
    assert not (tmp_path / "execution/metrics.json").exists()


@pytest.mark.asyncio
async def test_coding_proposal_without_actual_write_is_not_implementation() -> None:
    from app.agents.coding.agent import CodingAgent
    from app.harness.schema.frontmatter_parser import dumps
    text = dumps({"schema": "code_spec.v1", "project": "regression", "agent": "coding", "target_lang": "python",
                  "baseline_compat": {"preserved": True},
                  "files_changed": [{"path": "candidate.py", "type": "modified"}]}, "Proposed patch only.")
    errors = await CodingAgent().validate_candidate(
        RunRequest(project="regression", user_request="Implement candidate"), text, [])
    assert any("real code tools" in error for error in errors)
