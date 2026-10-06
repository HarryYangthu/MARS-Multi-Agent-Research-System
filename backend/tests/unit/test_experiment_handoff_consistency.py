"""Real documents, host configuration and API admission reproduce handoff failures."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from fastapi import FastAPI
import httpx
import pytest
import yaml

from app.agents.base import RunRequest
from app.agents.experiment.agent import ExperimentAgent
from app.api import dependencies
from app.api.artifacts import router
from app.bridge.agent_registry import AgentRegistry
from app.bridge.orchestrator import Orchestrator, RunRequest as SessionRequest
from app.bridge.task_runtime import admit_handoffs
from app.execution.handoff_validation import coding_handoff_errors
from app.harness.project_workspace import open_folder
from app.harness.schema.experiment_contract import document_hash, experiment_errors, execution_handoff_errors, handoff_errors
from app.harness.schema.frontmatter_parser import dumps
from app.settings import reset_settings_cache, repo_root
from app.storage.artifact_store import ArtifactStore, ArtifactValidationError
from app.storage.run_store import RunStore


def plan(project: str = 'regression', *, epochs: bool = False) -> dict[str, Any]:
    config = {'seed': 2026, **({'budget_unit': 'epochs', 'max_iters': 50} if epochs else {'budget_steps': 50})}
    return {'schema': 'experiment_plan.v1', 'project': project, 'agent': 'experiment',
        'variables': {'independent': ['scale'], 'dependent': ['mse']}, 'metrics': {'primary': 'mse'},
        'ablations': [{'name': 'baseline', 'config': config}], 'estimated_runs': 1}


def code(text: str, project: str = 'regression', **binding: Any) -> dict[str, Any]:
    return {'schema': 'code_spec.v1', 'project': project, 'agent': 'coding', 'target_lang': 'python',
        'baseline_compat': {'preserved': True}, 'files_changed': [],
        'experiment_plan_sha256': document_hash(text),
        'execution_jobs': [{'name': 'baseline', 'config': binding or {'command_id': 'registered'}}]}


@pytest.mark.asyncio
@pytest.mark.parametrize('seed', ['同基线种子', None, True, -1])
async def test_experiment_candidate_rejects_unresolved_seed_before_review(seed: Any) -> None:
    metadata = plan()
    metadata['ablations'][0]['config']['seed'] = seed
    errors = await ExperimentAgent().validate_candidate(
        RunRequest(project='regression', user_request='Use the baseline seed'), dumps(metadata, 'Authored plan.'), [])
    assert any('/seed' in error for error in errors)


@pytest.mark.parametrize('change', ['unit', 'value', 'missing_jobs', 'seed', 'stale_plan', 'name'])
def test_cross_document_changes_cannot_pass_coding_handoff(change: str) -> None:
    text = dumps(plan(), 'Approved comparison protocol.')
    metadata = code(text)
    config = metadata['execution_jobs'][0]['config']
    if change == 'unit':
        config.update(budget_unit='epochs', max_iters=50)
    elif change == 'value':
        config['budget_steps'] = 500
    elif change == 'missing_jobs':
        metadata.pop('execution_jobs')
    elif change == 'seed':
        config['seed'] = 42
    elif change == 'name':
        metadata['execution_jobs'][0]['name'] = 'other'
    else:
        text += '\nAn approved protocol change.\n'
    assert handoff_errors(text, metadata)


def test_matrix_count_and_normalized_paths_are_part_of_contract() -> None:
    metadata = plan()
    duplicate = deepcopy(metadata['ablations'][0])
    metadata['ablations'][0]['name'] = 'a.b'
    duplicate['name'] = 'a_b'
    metadata['ablations'].append(duplicate)
    errors = experiment_errors(metadata)
    assert any('/name' in error for error in errors)
    assert any('/estimated_runs' in error for error in errors)


@pytest.mark.parametrize('change', ['seed', 'budget', 'entrypoint', 'name', 'count'])
def test_execution_cannot_introduce_a_different_protocol(change: str) -> None:
    text = dumps(plan(), 'Approved protocol.')
    coding = code(text)
    execution = {'planned_experiments': [{'name': 'baseline',
        'config': {'seed': 2026, 'budget_steps': 50, 'command_id': 'registered'}}]}
    assert not execution_handoff_errors(text, coding, execution)
    job = execution['planned_experiments'][0]
    if change == 'count':
        execution['planned_experiments'].append(deepcopy(job))
    elif change == 'name':
        job['name'] = 'unapproved'
    else:
        job['config'][{'seed': 'seed', 'budget': 'budget_steps', 'entrypoint': 'command_id'}[change]] = (
            'other-command' if change == 'entrypoint' else 7)
    assert execution_handoff_errors(text, coding, execution)


def test_human_approval_cannot_bypass_seed_validation_or_mutate_approved_pointer(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task='human-validation', project='regression')
    store = ArtifactStore(run)
    good = store.approve(store.write(text=dumps(plan(), 'Valid human-authored experiment.')))
    before = good.path.read_bytes()
    bad = plan()
    bad['ablations'][0]['config']['seed'] = '同基线种子'
    source = store.write(text=dumps(bad, 'Readable but incomplete historical-style document.'))
    with pytest.raises(ArtifactValidationError, match='非负整数'):
        store.approve(source)
    assert good.path.read_bytes() == before
    assert len(list((run.root / 'experiment/.approvals/experiment_plan').glob('*.json'))) == 1
    with pytest.raises(ValueError, match='上游交接'):
        # A legacy upstream is authored directly to reproduce pre-upgrade data,
        # without pretending it passed the new approval gate.
        (run.root / 'experiment/experiment_plan.approved.md').write_text(source.path.read_text())
        admit_handoffs(run, 'coding', supplied_context={})


@pytest.mark.asyncio
async def test_api_rejects_incomplete_approval_with_actionable_response_before_any_driver(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    orch = Orchestrator(run_store=store, registry=AgentRegistry())
    session = orch.create_session(SessionRequest(task='api-admission', project='regression',
        entrypoint='experiment', standalone=True))
    bad = plan()
    bad['ablations'][0]['config']['seed'] = '同基线种子'
    artifact = ArtifactStore(session.run).write(text=dumps(bad, 'Incomplete experiment draft.'))
    previous = dependencies._run_store, dependencies._orchestrator
    dependencies._run_store, dependencies._orchestrator = store, orch
    app = FastAPI()
    app.include_router(router)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post(f'/api/artifacts/{session.run.run_id}/experiment/experiment_plan/{artifact.version}/approve')
        assert response.status_code == 422
        assert response.json()['detail']['code'] == 'handoff_inconsistent'
        assert response.json()['detail']['issues']
        assert not (session.run.root / 'experiment/experiment_plan.approved.md').exists()
        assert orch.owned_tasks.active(session.run.run_id) is None
    finally:
        dependencies._run_store, dependencies._orchestrator = previous


@pytest.mark.asyncio
async def test_stale_review_cannot_approve_a_newer_document(tmp_path: Path) -> None:
    from app.hitl.review_session import ReviewSession, get_registry
    store = RunStore(tmp_path)
    orch = Orchestrator(run_store=store, registry=AgentRegistry())
    session = orch.create_session(SessionRequest(task='stale-review', project='regression',
        entrypoint='experiment', standalone=True))
    artifacts = ArtifactStore(session.run)
    older = artifacts.write(text=dumps(plan(), 'Older document shown in the browser.'))
    newer = artifacts.write(text=dumps(plan(), 'Changed document not yet reviewed.'))
    review = ReviewSession(session.run, 'experiment', newer)
    registry = get_registry()
    await registry.register(review)
    previous = dependencies._run_store, dependencies._orchestrator
    dependencies._run_store, dependencies._orchestrator = store, orch
    app = FastAPI()
    app.include_router(router)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post(f'/api/artifacts/{session.run.run_id}/experiment/experiment_plan/{older.version}/approve')
        assert response.status_code == 409
        assert response.json()['detail']['code'] == 'stale_review'
        assert not review.approval_event.is_set() and review.decision is None
        assert not (session.run.root / 'experiment/experiment_plan.approved.md').exists()
    finally:
        await registry.unregister(session.run.run_id, 'experiment')
        dependencies._run_store, dependencies._orchestrator = previous


def test_epoch_budget_is_rejected_by_step_only_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('MARS_EXECUTION_BACKEND', 'pim_cpu')
    reset_settings_cache()
    text = dumps(plan(epochs=True), 'An epoch-based protocol cannot run as optimizer steps.')
    try:
        assert any('steps' in error for error in coding_handoff_errors(text, code(text), project='regression'))
    finally:
        reset_settings_cache()


@pytest.mark.parametrize('fault', ['seed', 'short_budget', 'missing_file', 'steps', 'none'])
def test_coding_approval_checks_actual_paper_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    registry = tmp_path / 'folder_projects.json'
    monkeypatch.setenv('MARS_FOLDER_PROJECTS_REGISTRY', str(registry))
    monkeypatch.setenv('MARS_EXECUTION_BACKEND', 'paper_static')
    reset_settings_cache()
    project = open_folder(str(tmp_path / 'research'), create=True, registry=registry)
    root = project.root
    (project.metadata_root / 'repo_link.yaml').write_text(yaml.safe_dump({'local_path': str(root)}))
    (root / 'train_static.py').write_text('# Authored file used only to validate an entrypoint binding.\n')
    (root / 'training.yaml').write_text(yaml.safe_dump({'seed': 42 if fault == 'seed' else 2026,
        'Etotal': 1, 'Epoch': 10 if fault == 'short_budget' else 50}))
    metadata = plan(project.name, epochs=fault != 'steps')
    text = dumps(metadata, 'Authored training protocol.')
    coding = code(text, project.name, entrypoint='train_static.py',
        config_path='missing.yaml' if fault == 'missing_file' else 'training.yaml')
    try:
        errors = coding_handoff_errors(text, coding, project=project.name)
        assert bool(errors) == (fault != 'none')
        run = RunStore(tmp_path / 'runs').create(task='paper-binding', project=project.name)
        artifacts = ArtifactStore(run)
        artifacts.approve(artifacts.write(text=text))
        ref = artifacts.write(text=dumps(coding, 'Coding handoff, not a simulation result.'))
        if fault == 'none':
            artifacts.approve(ref)
        else:
            with pytest.raises(ArtifactValidationError):
                artifacts.approve(ref)
            assert not (run.root / 'coding/code_spec.approved.md').exists()
    finally:
        reset_settings_cache()


@pytest.mark.parametrize('explicit', [False, True])
def test_backend_precedence_is_shared_by_settings_and_tool_configuration(tmp_path: Path, explicit: bool) -> None:
    runtime = tmp_path / 'runtime'
    for path in ('configs', 'templates/artifacts', 'backend/app/harness/schema/schemas'):
        (runtime / path).mkdir(parents=True)
    (runtime / 'configs/agents.yaml').write_text((repo_root() / 'configs/agents.yaml').read_text())
    (runtime / 'configs/execution.yaml').write_text(yaml.safe_dump({'execution': {'backend': 'paper_static'}}))
    environment = {key:value for key,value in os.environ.items() if key not in {'MARS_EXECUTION_BACKEND', 'MARS_EXECUTION_CONFIG_PATH'}}
    environment.update(MARS_RUNTIME_ROOT=str(runtime), PYTHONPATH=str(repo_root() / 'backend'))
    if explicit:
        environment['MARS_EXECUTION_BACKEND'] = 'local_command'
    output = subprocess.check_output([sys.executable, '-c',
        "import json; from app.settings import get_settings; from app.harness.tools.config import load_execution_config; "
        "s=get_settings(); c=load_execution_config()['execution']; "
        "print(json.dumps([s.mars_execution_backend,c['backend'],c['backend_source']]))"], env=environment, timeout=10)
    backend, effective, source = json.loads(output)
    assert backend == effective == ('local_command' if explicit else 'paper_static')
    assert source == ('environment' if explicit else 'execution_config')
