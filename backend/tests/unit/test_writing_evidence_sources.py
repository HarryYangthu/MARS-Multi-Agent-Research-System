"""Actual archived evidence and host graph quality, without model/tool doubles."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest

from app.agents.writing.agent import _review_metadata_errors
from app.harness.context.runtime_pack import read_material
from app.harness.evaluation.artifacts import read_reports_for_artifact
from app.harness.evaluation.runner import EvaluationRunner
from app.harness.reporting.execution_evidence import execution_job_evidence
from app.harness.runtime.run_graph import RunGraph
from app.harness.schema.frontmatter_parser import dumps
from app.storage.artifact_store import ArtifactStore
from app.storage.run_state_store import RunStateStore
from app.storage.run_store import RunStore


@pytest.fixture
def actual_archive_copy(tmp_path: Path) -> tuple[Path, str, str]:
    configured = os.environ.get('MARS_WRITING_REAL_RUN_ROOT')
    if not configured:
        pytest.skip('Set MARS_WRITING_REAL_RUN_ROOT to an actual 50-update archive; no substitute result is generated')
    source = Path(configured).expanduser().resolve(strict=True)
    assert source.is_dir(), 'MARS_WRITING_REAL_RUN_ROOT must identify an actual run directory'
    rows = [(path, json.loads(path.read_text())) for path in (source / 'execution/jobs').glob('*.json')]
    journal, job = next((path, row) for path, row in rows if row['status'] == 'completed')
    copied = tmp_path / 'copied-actual-archive'
    copied.mkdir()
    for evidence in job['evidence']:
        path = Path(evidence['path'])
        target = copied / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        # Relocate the real journal reference only; receipt/index bytes and
        # measured results remain unchanged, with their original digests.
        evidence['path'] = target.relative_to(copied).as_posix()
        if path.name == 'artifact_index.json':
            for item in json.loads(path.read_text())['files']:
                child = path.parent / item['path']
                target_child = copied / child.relative_to(source)
                target_child.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(child, target_child)
    copied_journal = copied / journal.relative_to(source)
    copied_journal.parent.mkdir(parents=True, exist_ok=True)
    copied_journal.write_text(json.dumps(job), encoding='utf-8')
    return copied, job['run_id'], job['project']


def _records(text: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in text.splitlines() if line.startswith('{')]


def test_actual_csv_history_config_and_manifest_are_hashed_and_readable(
    actual_archive_copy: tuple[Path, str, str],
) -> None:
    root, run_id, project = actual_archive_copy
    records = _records(execution_job_evidence(root, run_id, project, admit_materials=True))
    assert len(records) == 1 and 'evidence_error' not in records[0]
    files = records[0]['files']
    assert isinstance(files, list)
    names = {Path(item['source']).name for item in files}
    assert {'steps.csv', 'history.json', 'config.json', 'manifest.json'} <= names
    assert not {'steps.jsonl', 'config.yaml'} & names
    for item in files:
        assert item['sha256'] == hashlib.sha256((root / item['source']).read_bytes()).hexdigest()
        assert item['integrity'] == 'recorded_sha256_verified'
    steps = next(item for item in files if item['source'].endswith('/steps.csv'))
    assert steps['rows'] == steps['lr_rows'] == steps['last_optimizer_step'] == 50
    content = read_material(root, steps['material_ref'], 0, 16384)
    assert not content['truncated'] and 'optimizer_step,epoch,train_start_sample' in content['content']
    manifest = next(item for item in files if Path(item['source']).name == 'manifest.json')
    manifest_text = read_material(root, manifest['material_ref'], 0, 16384)['content']
    assert '10log10(mean_channel(Perr/Pnf))' in manifest_text
    assert '0.1' not in json.loads((root / manifest['source']).read_text())['metric']


def test_modified_actual_artifact_is_rejected_before_admission(actual_archive_copy: tuple[Path, str, str]) -> None:
    root, run_id, project = actual_archive_copy
    steps = next(root.glob('execution/local_commands/*/*/baseline/steps.csv'))
    steps.write_text(steps.read_text() + 'Authored tamper-detection input\n')
    records = _records(execution_job_evidence(root, run_id, project, admit_materials=True))
    error = records[0]['evidence_error']
    assert isinstance(error, str) and 'Indexed artifact no longer matches' in error
    assert 'metrics' not in records[0] and not (root / 'context/materials').exists()


def _report(project: str, *, omit_runs: bool = False) -> str:
    chain: dict[str, object] = {'code': 'coding/code_spec.approved.md'}
    if not omit_runs:
        chain['runs'] = ['execution/metrics.json']
    return dumps({'schema': 'report.v1', 'project': project, 'agent': 'writing',
        'deliverable_type': 'research_report', 'target_audience': 'maintainer', 'chain_refs': chain,
        'skipped_stages': ['idea', 'experiment']},
        'Human-authored quality contract input. Metric units: dB. Limitation: no scientific result asserted.')


def _graph(*, skip: bool) -> RunGraph:
    graph = RunGraph()
    stages = ['idea', 'experiment', 'coding', 'execution', 'writing']
    for stage in stages:
        graph.add_node(stage, metadata={'stage': stage})
    for left, right in zip(stages, stages[1:]):
        graph.add_edge(left, right)
    graph.set_entrypoint('coding' if skip else 'idea')
    if skip:
        graph.skip('idea')
        graph.skip('experiment')
    return graph


@pytest.mark.parametrize('skip,omit_runs,expected', [(True, False, 1.0), (True, True, 0.5),
                                                   (False, False, 0.5)])
def test_only_authoritative_host_skips_change_chain_requirements(
    tmp_path: Path, skip: bool, omit_runs: bool, expected: float,
) -> None:
    run = RunStore(tmp_path).create(task='authored-quality-contract', project='classification',
                                    entrypoint='coding' if skip else 'idea')
    RunStateStore(run).write(graph=_graph(skip=skip), request={'task': run.task, 'project': run.project,
        'entrypoint': run.entrypoint}, status='running')
    # A modified compatibility projection cannot override the SQL authority.
    projection = json.loads((run.root / 'run_state.json').read_text())
    projection['graph']['nodes'][0]['state'] = 'skipped'
    projection['graph']['nodes'][1]['state'] = 'skipped'
    (run.root / 'run_state.json').write_text(json.dumps(projection))
    ref = ArtifactStore(run).write(text=_report(run.project, omit_runs=omit_runs))
    reports = read_reports_for_artifact(run_root=run.root, agent_dir='writing', stem=ref.stem, version=ref.version)
    quality = next(report['metadata'] for report in reports if report['metadata']['evaluator'] == 'artifact_quality.rubric')
    assert quality['scores']['chain_coverage'] == expected
    assert any(item['category'] == 'chain_coverage' for item in quality['findings']) == (expected < 1)


def test_artifact_self_declared_skips_do_not_change_default_quality() -> None:
    quality = next(report for report in EvaluationRunner().evaluate_text(project='classification',
        text=_report('classification'), target_ref='writing/research_report.v1.md', expected_schema='report.v1')
        if report.evaluator == 'artifact_quality.rubric')
    assert quality.scores['chain_coverage'] == 0.5


def test_retries_cannot_create_debate_review_metadata() -> None:
    assert _review_metadata_errors({'debate_summary': {'rounds': 1, 'reviewer_critiques': ['revision feedback']}},
                                    debate_enabled=False)
    assert _review_metadata_errors({'debate_summary': {'rounds': 0, 'reviewer_critiques': []}},
                                    debate_enabled=False) == []
    assert _review_metadata_errors({'debate_summary': {'rounds': 1}}, debate_enabled=True) == []
