"""Authored files exercise edit, evaluation, and approval boundaries without models."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json

from fastapi import FastAPI
import httpx
import pytest

from app.bridge.evaluation_service import build_artifact_evaluation_summary
from app.harness.schema.experiment_contract import experiment_errors
from app.harness.schema.frontmatter_parser import dumps
from app.hitl.revision_loop import apply_human_edit
from app.storage.artifact_store import ArtifactStore, ArtifactValidationError
from app.storage.run_store import RunStore


def metadata() -> dict:
    return {'schema': 'experiment_plan.v1', 'project': 'edit-check', 'agent': 'experiment',
        'variables': {'independent': ['learning rate'], 'dependent': ['RES']},
        'metrics': {'primary': 'RES', 'secondary': ['loss']},
        'baseline_ref': {'reuse_decision': 'rerun'},
        'ablations': [{'name': name, 'config': {'seed': 2026, 'budget_steps': 50,
            'cfg': f'configs/{name}.yaml'}} for name in ('baseline', 'candidate')],
        'estimated_runs': 2}


def test_edit_evaluates_new_text_and_invalid_edit_cannot_advance_approval(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task='edit', project='edit-check')
    store = ArtifactStore(run)
    base = store.write(text=dumps(metadata(), 'Authored comparison protocol.'))
    approved = store.approve(base)
    old_approval = approved.path.read_bytes()
    edited, validation = apply_human_edit(art_store=store, base=base,
        body='Check the actual steps.jsonl and summary.json.', expected_schema='experiment_plan.v1')
    assert validation.valid and edited.version == 'v2'
    summary = build_artifact_evaluation_summary(run=run, ref=edited)
    assert summary['report_count'] == 3 and summary['evaluation_status'] == 'evaluated'
    assert summary['missing_evaluators'] == [] and summary['decision'] == 'pass'
    assert all(report['target_ref'].endswith('experiment_plan.v2.md') for report in summary['reports'])
    broken, validation = apply_human_edit(art_store=store, base=edited,
        metadata_patch={'estimated_runs': 'invalid'}, expected_schema='experiment_plan.v1')
    assert not validation.valid and broken.path.exists()
    failed = build_artifact_evaluation_summary(run=run, ref=broken)
    assert failed['evaluation_status'] == 'evaluated' and failed['blocking']
    with pytest.raises(ArtifactValidationError):
        store.approve(broken)
    assert approved.path.read_bytes() == old_approval
    assert base.path.read_text() == dumps(metadata(), 'Authored comparison protocol.')


def test_concurrent_edits_preserve_each_version_and_its_reports(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task='concurrent-edit', project='edit-check')
    store = ArtifactStore(run)
    base = store.write(text=dumps(metadata(), 'Original authored protocol.'))
    with ThreadPoolExecutor(max_workers=4) as pool:
        edits = list(pool.map(lambda n: apply_human_edit(art_store=store, base=base,
            body=f'Independent reviewer edit {n}.', expected_schema='experiment_plan.v1')[0], range(4)))
    assert {ref.version for ref in edits} == {'v2', 'v3', 'v4', 'v5'}
    assert len({ref.path.read_text() for ref in edits}) == 4
    assert all(build_artifact_evaluation_summary(run=run, ref=ref)['report_count'] == 3 for ref in edits)


@pytest.mark.parametrize('value', ['configs/baseline.yaml（完整复制基线）',
    'configs/baseline.yaml\n--max-steps 50', {'path': 'configs/baseline.yaml'}])
def test_experiment_paths_cannot_embed_descriptions_or_commands(value: object) -> None:
    plan = metadata()
    plan['ablations'][0]['config']['cfg'] = value
    assert any('/config/cfg' in error for error in experiment_errors(plan))


def test_plain_paths_and_registered_commands_remain_usable() -> None:
    plan = metadata()
    assert experiment_errors(plan) == []
    plan['ablations'][0]['config']['config_path'] = 'configs/different.yaml'
    assert any('同一个配置文件' in error for error in experiment_errors(plan))
    plan['ablations'][0]['config'].pop('cfg')
    plan['ablations'][0]['config'].pop('config_path')
    plan['ablations'][0]['config']['command_id'] = 'registered-command'
    assert experiment_errors(plan) == []


@pytest.mark.asyncio
async def test_edit_after_restart_is_audited_and_stale_approval_stays_blocked(tmp_path: Path) -> None:
    from app.api import dependencies
    from app.api.artifacts import router
    from app.bridge.agent_registry import AgentRegistry
    from app.bridge.orchestrator import Orchestrator, RunRequest
    store = RunStore(tmp_path)
    orch = Orchestrator(run_store=store, registry=AgentRegistry())
    session = orch.create_session(RunRequest(task='review-without-memory', project='edit-check',
        entrypoint='experiment', standalone=True))
    base = ArtifactStore(session.run).write(text=dumps(metadata(), 'An authored protocol, not an executed experiment.'))
    original = base.path.read_bytes()
    previous = dependencies._run_store, dependencies._orchestrator
    dependencies._run_store, dependencies._orchestrator = store, orch
    app = FastAPI()
    app.include_router(router)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            prefix = f'/api/artifacts/{session.run.run_id}/experiment/experiment_plan'
            edited = await client.post(prefix + '/v1/edit', json={'body': 'Check actual steps.jsonl.'})
            assert edited.status_code == 200 and edited.json()['version'] == 'v2'
            stale = await client.post(prefix + '/v1/approve')
            assert stale.status_code == 409 and stale.json()['detail']['code'] == 'stale_review'
        audits = [json.loads(line) for line in (session.run.root / 'hitl/review_log.jsonl').read_text().splitlines()]
        assert len(audits) == 1 and audits[0]['action'] == 'edit'
        assert audits[0]['detail']['version'] == 'v2'
        events = [json.loads(line) for line in (session.run.root / 'events/evaluation_events.jsonl').read_text().splitlines()]
        assert events[-1]['version'] == 'v2' and events[-1]['evaluation_status'] == 'evaluated'
        assert not (session.run.root / 'experiment/experiment_plan.approved.md').exists()
        assert orch.owned_tasks.active(session.run.run_id) is None
        assert base.path.read_bytes() == original
    finally:
        dependencies._run_store, dependencies._orchestrator = previous
