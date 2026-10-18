"""Actual preparation subprocess receipts bound into the pure review contract."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
from typing import Any
import uuid

import pytest
import yaml

from app.agents.base import Artifact, RunRequest
from app.bridge.coding_reuse import prepare_reused_artifact
from app.bridge.coding_reuse_evidence import _sha
from app.bridge.managed_coding_evidence import coding_preparation_review_evidence
from app.bridge.managed_review import parse_decision
from app.bridge.research_branch import research_branch_scope
from app.storage.run_store import RunHandle, RunStore
from app.storage.artifact_store import ArtifactStore
from tests.unit.test_coding_reuse_evidence import _completed_source, _request, real_project


async def _prepared(project_fixture: tuple[RunStore, str, Path]) -> tuple[RunHandle, Artifact]:
    store, project, data = project_fixture
    run = store.create(task='review_current_preparation', project=project, entrypoint='coding',
        data_source={'id': 'current_values', 'stored_path': str(data)})
    request = RunRequest(project=project, user_request='Run existing configuration for 7 steps seed 2031',
        extra={'invocation_id': uuid.uuid4().hex, 'existing_execution_job': _request(max_iters=7, seed=2031)})
    with research_branch_scope(run, 'coding'):
        artifact = await prepare_reused_artifact(run, request)
        assert artifact is not None
    return run, artifact


@pytest.mark.asyncio
async def test_actual_host_receipts_are_explicit_citable_review_evidence(
        real_project: tuple[RunStore, str, Path]) -> None:
    run, artifact = await _prepared(real_project)
    before = {path: path.read_bytes() for path in run.root.rglob('*.json')}
    evidence = coding_preparation_review_evidence(run, artifact.metadata)
    reuse_ref = artifact.metadata['execution_reuse']['receipt_ref']
    validation_ref = artifact.metadata['execution_reuse']['current_preflight_ref']
    assert set(evidence) == {reuse_ref, validation_ref}
    assert evidence[reuse_ref]['model_calls'] == 0
    assert evidence[reuse_ref]['training_started'] is False and evidence[reuse_ref]['approved'] is False
    assert evidence[reuse_ref]['requested'] == _request(max_iters=7, seed=2031)
    assert evidence[validation_ref]['pid'] > 0 and evidence[validation_ref]['returncode'] == 0
    assert evidence[validation_ref]['checks'][0]['verified_request'] == {
        'budget_unit': 'steps', 'budget_value': 7, 'seed': 2031, 'training_started': False}
    assert len(json.dumps(evidence, ensure_ascii=False)) < 12000
    assert evidence[validation_ref]['verified_input_count'] == 3
    assert all(evidence[ref]['sha256'] == _sha(run.root / ref) for ref in evidence)
    # Pure parsing verifies the same exact allowlist contract without calling a
    # provider, approving a task, or inventing a model review response.
    decision = parse_decision(json.dumps({'decision': 'approve', 'reason': 'Authored parser contract example',
        'evidence_refs': [reuse_ref, validation_ref]}), ['coding/code_spec.v1.md', *evidence])
    assert decision.evidence_refs == [reuse_ref, validation_ref]
    with pytest.raises(ValueError):
        parse_decision(json.dumps({'decision': 'approve', 'reason': 'Unprovided file must remain disallowed',
            'evidence_refs': ['execution/preflight/invented/validation_receipt.json']}), list(evidence))
    assert all(path.read_bytes() == value for path, value in before.items())
    assert not (run.root / 'coding/code_spec.approved.md').exists()
    assert not list((run.root / 'execution/local_commands').rglob('execution_receipt.json'))


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [
    'outside_ref', 'symlink', 'reuse_identity', 'validation_hash', 'validation_identity',
    'job_hash', 'result_hash', 'job_identity', 'artifact_seed', 'changed_input', 'registration',
    'preflight_symlink', 'source_command', 'producer_identity',
])
async def test_changed_or_unbound_receipts_cannot_enter_the_review_allowlist(
        real_project: tuple[RunStore, str, Path], change: str) -> None:
    run, artifact = await _prepared(real_project)
    metadata = deepcopy(artifact.metadata)
    reuse_path = run.root / metadata['execution_reuse']['receipt_ref']
    reuse = json.loads(reuse_path.read_text())
    validation_path = run.root / reuse['current_preflight']['ref']
    if change == 'outside_ref':
        metadata['execution_reuse']['receipt_ref'] = '../other/coding/reuse/' + reuse_path.name
    elif change == 'symlink':
        outside = run.root.parent / ('outside_' + reuse_path.name)
        outside.write_bytes(reuse_path.read_bytes())
        reuse_path.unlink()
        reuse_path.symlink_to(outside)
    elif change == 'reuse_identity':
        reuse['run_id'] = 'different_run'
        reuse_path.write_text(json.dumps(reuse))
    elif change == 'producer_identity':
        reuse['invocation_id'] = uuid.uuid4().hex
        reuse_path.write_text(json.dumps(reuse))
    elif change == 'source_command':
        reuse['source']['command_files'] = []
        reuse_path.write_text(json.dumps(reuse))
    elif change == 'preflight_symlink':
        outside = run.root.parent / ('outside_validation_' + uuid.uuid4().hex + '.json')
        outside.write_bytes(validation_path.read_bytes())
        validation_path.unlink()
        validation_path.symlink_to(outside)
    elif change == 'validation_hash':
        validation_path.write_text(validation_path.read_text() + '\n')
    elif change == 'job_hash':
        path = validation_path.with_name('job.json')
        path.write_text(path.read_text() + '\n')
    elif change == 'result_hash':
        path = validation_path.with_name('result.json')
        path.write_text(path.read_text() + '\n')
    elif change in {'validation_identity', 'job_identity'}:
        # Recompute content checksums around deliberately damaged genuine
        # receipts, proving identity checks are independent of checksum checks.
        validation = json.loads(validation_path.read_text())
        if change == 'validation_identity':
            validation['run_id'] = 'different_run'
        else:
            path = validation_path.with_name('job.json')
            job = json.loads(path.read_text())
            job['experiment_id'] = 'different_experiment'
            path.write_text(json.dumps(job))
            validation['request_sha256'] = 'sha256:' + _sha(path)
        validation_path.write_text(json.dumps(validation))
        reuse['current_preflight']['sha256'] = _sha(validation_path)
        reuse_path.write_text(json.dumps(reuse))
    elif change == 'artifact_seed':
        metadata['execution_jobs'][0]['config']['seed'] = 2032
    elif change == 'changed_input':
        _, _, data = real_project
        data.write_text(json.dumps({'values': [2, 3, 100]}))
    elif change == 'registration':
        path = Path(os.environ['MARS_EXECUTION_CONFIG_PATH'])
        profile = yaml.safe_load(path.read_text())
        profile['execution']['local_commands'][0]['label'] = 'Changed host capability'
        path.write_text(yaml.safe_dump(profile))
    with pytest.raises((ValueError, OSError, KeyError, TypeError)):
        coding_preparation_review_evidence(run, metadata)
    assert not (run.root / 'coding/code_spec.approved.md').exists()


def test_normal_coding_has_no_invented_preparation_evidence(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(project='synthetic_regression', task='ordinary coding')
    assert coding_preparation_review_evidence(run, {}) == {}


@pytest.mark.asyncio
async def test_execution_review_exposes_same_verified_receipts_from_approved_existing_code(
        real_project: tuple[RunStore, str, Path]) -> None:
    from app.bridge.managed_review import execution_review_payload

    run, artifact = await _prepared(real_project)
    store = ArtifactStore(run)
    ref = store.write(text=artifact.text)
    store.approve(ref)
    configuration = {'confirmed': False, 'jobs': [], 'experiments': artifact.metadata['execution_jobs']}
    payload = execution_review_payload(run, goal='Run existing model for 7 steps seed 2031', configuration=configuration)
    evidence = coding_preparation_review_evidence(run, artifact.metadata)
    assert payload['execution_preparation_evidence'] == evidence
    assert set(payload['evidence_refs']) == {'execution_configuration', 'coding/code_spec.approved.md', *evidence}
    assert payload['configuration'] == configuration
    assert payload['approved_artifacts'][0]['content'] == artifact.text
    assert payload['execution_preparation_evidence'][artifact.metadata['execution_reuse']['receipt_ref']]['model_calls'] == 0
    decision = parse_decision(json.dumps({'decision': 'approve', 'reason': 'Authored execution-parser contract example',
        'evidence_refs': list(evidence)}), payload['evidence_refs'])
    assert decision.evidence_refs == list(evidence)
    assert not list((run.root / 'execution/local_commands').rglob('execution_receipt.json'))
    # An invalid claimed receipt fails before a review model receives any path.
    receipt = run.root / artifact.metadata['execution_reuse']['current_preflight_ref']
    receipt.write_text(receipt.read_text() + '\n')
    with pytest.raises(ValueError):
        execution_review_payload(run, goal='Run existing model', configuration=configuration)


@pytest.mark.asyncio
async def test_explicit_verified_history_receipts_remain_citable_with_legacy_hash_encoding(
        real_project: tuple[RunStore, str, Path]) -> None:
    store, project, data = real_project
    profile_path = Path(os.environ['MARS_EXECUTION_CONFIG_PATH'])
    profile = yaml.safe_load(profile_path.read_text())
    profile['execution']['local_commands'][0]['label'] = '实际统计命令'
    profile_path.write_text(yaml.safe_dump(profile, allow_unicode=True))
    source = await _completed_source(store, project, data)
    run = store.create(task='explicit_verified_history', project=project, entrypoint='coding',
        data_source={'stored_path': str(data)})
    request = RunRequest(project=project, user_request='Reuse explicitly verified source',
        extra={'invocation_id': uuid.uuid4().hex,
               'existing_execution_job': _request(source_run_id=source.run_id)})
    with research_branch_scope(run, 'coding'):
        artifact = await prepare_reused_artifact(run, request)
        assert artifact is not None
    evidence = coding_preparation_review_evidence(run, artifact.metadata)
    assert len(evidence) == 2
    assert evidence[artifact.metadata['execution_reuse']['receipt_ref']]['mode'] == 'host_verified_existing_execution'
