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


@pytest.mark.asyncio
async def test_deterministic_intake_keeps_matrix_without_a_model_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.harness.schema.frontmatter_parser import dumps
    from app.harness.llm.accounting import RunModelBudget
    from app.harness.llm.provider_base import LLMConfig, Message
    monkeypatch.setenv('ZHIPU_API_KEY', '')
    rows: list[dict[str, Any]] = [{'name': f'comparison-{i}', 'config': {'seed': 0, 'scale': i}} for i in range(5)]
    plan = {'schema': 'experiment_plan.v1', 'agent': 'experiment', 'project': 'regression',
            'variables': {'independent': ['scale'], 'dependent': ['mse']}, 'metrics': {'primary': 'mse'},
            'ablations': rows, 'estimated_runs': 5}
    code = {'schema': 'code_spec.v1', 'agent': 'coding', 'project': 'regression', 'target_lang': 'python',
            'baseline_compat': {'preserved': True}, 'files_changed': [],
            'execution_jobs': [{'name': row['name'], 'config': {'command_id': 'regression'}} for row in rows]}
    budget = RunModelBudget(tmp_path)
    for _ in range(budget.configuration['limits']['max_model_requests']):
        reservation = budget.reserve([Message('user', 'Accounting only')],
            LLMConfig(provider='custom', model='ledger-only', max_tokens=8, max_retries=0), {})
        budget.settle(reservation, usage=None, complete=False, outcome='cancelled')
    before = budget.path.read_bytes()
    request = RunRequest(project='regression', user_request='Run the approved comparisons',
        upstream_artifacts={'plan': '[upstream artifact: experiment/experiment_plan.approved.md]\n' + dumps(plan, 'Human-authored matrix.'),
                            'code': dumps(code, 'Human-authored runtime binding.')},
        extra={'run_id': 'intake-no-llm', 'run_root': str(tmp_path)})
    agent = ExecutionAgent()
    output = await agent.run_loop(request, await agent.build_context(request))
    assert output.metadata['planned_experiments'] == [
        {'name': row['name'], 'config': {**row['config'], 'command_id': 'regression'}} for row in rows]
    assert output.metadata['runtime_mode'] == 'deterministic'
    assert output.metadata['status'] == 'interrupted'
    assert budget.path.read_bytes() == before
    assert not (tmp_path / 'agent_traces').exists()


@pytest.mark.asyncio
async def test_intake_cannot_override_approved_parameters() -> None:
    from app.harness.schema.frontmatter_parser import dumps
    request = RunRequest(project='regression', user_request='Run unchanged', extra={'run_id': 'controls'},
        upstream_artifacts={
            'plan': dumps({'schema': 'experiment_plan.v1', 'project': 'regression',
                'ablations': [{'name': 'candidate', 'config': {'seed': 0}}]}, 'Authored input.'),
            'code': dumps({'schema': 'code_spec.v1', 'project': 'regression',
                'execution_jobs': [{'name': 'candidate', 'config': {'seed': 42}}]}, 'Conflicting authored binding.')})
    agent = ExecutionAgent()
    with pytest.raises(ValueError, match='改写了批准参数'):
        await agent.run_loop(request, await agent.build_context(request))


def test_paper_binding_rejects_step_to_epoch_conversion_and_global_fallback(tmp_path: Path) -> None:
    from app.execution.paper_static_adapter import approved_config_path
    for cfg in ({}, {'config_path': 'configs/test.yaml', 'entrypoint': 'train_static.py', 'max_iters': 50,
        'budget_unit': 'epochs', 'budget_steps': 50}):
        with pytest.raises(ValueError):
            approved_config_path(cfg, {'config_path': 'global.yaml'}, tmp_path)
    actual = {'config_path': 'configs/test.yaml', 'entrypoint': 'train_static.py', 'max_iters': 50, 'budget_unit': 'epochs'}
    assert approved_config_path(actual, {'config_path': 'global.yaml'}, tmp_path) == tmp_path / 'configs/test.yaml'
