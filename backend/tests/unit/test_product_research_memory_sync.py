"""Real temporary evidence, SQLite, tools and ASGI reads; no training substitutes."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from fastapi.testclient import TestClient
import pytest
import yaml

from app.harness.runtime.project_scope import ProjectScope, bind_project_scope
from app.harness.tools.knowledge import baseline_match_tool, research_memory_read_tool, research_memory_tool
from app.harness.tools.registry import ToolContext
from app.main import create_app
from app.storage.research_memory import ResearchMemory, compatibility, memory_context, sha_file


def _scope(tmp_path: Path) -> ProjectScope:
    run = (tmp_path / 'host-scope').resolve()
    children = [run / name for name in ('metadata', 'snapshot', 'candidate')]
    for directory in children:
        directory.mkdir(parents=True)
    (children[0] / 'project.yaml').write_text('display_name: Numeric evidence test\n')
    return ProjectScope(run_id=run.name, project='numeric-evidence', task_sha256='0' * 64,
        run_root=run, metadata_root=children[0], snapshot_root=children[1], candidate_root=children[2],
        snapshot_id='host-authored-test-scope', readable_files=(), allowed_write_paths=(), protected_paths=())


def _measure(tmp_path: Path, coefficient: int = 1) -> tuple[Path, dict[str, Any], float]:
    """Execute actual arithmetic on disk, not a model/provider or claimed training."""
    source = tmp_path / ('arithmetic-' + str(coefficient))
    source.mkdir()
    data = source / 'input.csv'
    data.write_text('0,0\n1,1\n2,4\n')
    script = source / 'measure.py'
    script.write_text('''import json, sys
from pathlib import Path
rows=[tuple(map(float,line.split(','))) for line in Path('input.csv').read_text().splitlines()]
coefficient=int(sys.argv[1])
errors=[(coefficient*x-y)**2 for x,y in rows]
Path('summary.json').write_text(json.dumps({'status':'completed','metrics':{'mse':sum(errors)/len(errors)}}))
''')
    completed = subprocess.run([sys.executable, str(script), str(coefficient)], cwd=source,
                               capture_output=True, text=True, timeout=10, check=True)
    assert completed.returncode == 0 and not completed.stderr
    (source / 'config.yaml').write_text(yaml.safe_dump({'model_name': 'ArithmeticCheck',
        'model': {'coefficient': coefficient}, 'lr_init': .01, 'seed': 2026}))
    protocol: dict[str, Any] = {'data_sha256': sha_file(data), 'split': {'kind': 'whole_three_rows'},
        'preprocessing': {'kind': 'identity'}, 'evaluation': {'source_sha256': sha_file(script), 'metric': 'mse'}}
    (source / 'manifest.json').write_text(json.dumps({'status': 'completed',
        'returncode': completed.returncode, 'source_sha256': {'measure.py': sha_file(script)},
        'data_sha256': protocol['data_sha256']}))
    actual = json.loads((source / 'summary.json').read_text())['metrics']['mse']
    return source, protocol, float(actual)


def _import(memory: ResearchMemory, source: Path, protocol: dict[str, Any]) -> dict[str, Any]:
    result = memory.import_directory(str(source), protocol=protocol)
    assert result['added'] == 1 and result['warnings'] == []
    return memory.get(result['ids'][0])


def test_actual_measurements_are_retained_idempotently_and_protocol_is_exact(tmp_path: Path) -> None:
    scope = _scope(tmp_path)
    source, protocol, measured = _measure(tmp_path)
    with bind_project_scope(scope):
        memory = ResearchMemory(scope.project)
        row = _import(memory, source, protocol)
        assert measured == pytest.approx(4 / 3) and row['metrics']['mse'] == measured
        assert row['result_quality'] == 'measured' and row['integrity']['intact']
        assert memory.import_directory(str(source), label='another label', protocol=protocol)['added'] == 0
        assert memory.retrieve('ArithmeticCheck', protocol=protocol)['hits'][0]['comparison']['reuse_allowed']
        assert not memory.retrieve('ArithmeticCheck')['hits'][0]['comparison']['reuse_allowed']
        for key in protocol:
            changed = {**protocol, key: 'different-condition'}
            check = memory.retrieve('ArithmeticCheck', protocol=changed)['hits'][0]['comparison']
            assert not check['reuse_allowed'] and key in check['conflicts']
        # Only evaluation conditions participate; parameter proposals are not
        # silently mistaken for changed data/evaluator evidence.
        assert compatibility(protocol, {**protocol, 'lr_init': .02})['compatible']
        shutil.rmtree(source)
        preserved = memory.get(row['id'])
        assert preserved['metrics']['mse'] == measured and preserved['integrity']['intact']


def test_baseline_selection_is_explicit_frozen_and_measurements_are_immutable(tmp_path: Path) -> None:
    scope = _scope(tmp_path)
    original, protocol, measured = _measure(tmp_path)
    candidate, candidate_protocol, candidate_measurement = _measure(tmp_path, 2)
    with bind_project_scope(scope):
        memory = ResearchMemory(scope.project)
        first = _import(memory, original, protocol)
        second = _import(memory, candidate, candidate_protocol)
        assert candidate_measurement == pytest.approx(1 / 3)
        assert memory.overview()['pointers'] == {}
        memory.select(first['id'], 'original')
        with pytest.raises(ValueError, match='原始基线已冻结'):
            memory.select(second['id'], 'original')
        memory.select(second['id'], 'current_best')
        memory.annotate(first['id'], {'conclusion': 'Arithmetic measurement only; no training conclusion'})
        with pytest.raises(ValueError, match='不能修改原始测量'):
            memory.annotate(first['id'], {'metrics': {'mse': 0}})
        assert memory.get(first['id'])['metrics']['mse'] == measured
        assert memory.import_directory(str(original), protocol=protocol)['added'] == 0
        assert memory.get(first['id'])['annotations']['conclusion'].startswith('Arithmetic measurement only')
        assert memory.overview()['pointers'] == {'original': first['id'], 'current_best': second['id']}


def test_real_process_failure_is_not_a_negative_scientific_result(tmp_path: Path) -> None:
    scope = _scope(tmp_path)
    source = tmp_path / 'actual-command-failure'
    jobs = source / 'jobs'
    jobs.mkdir(parents=True)
    failed = subprocess.run([sys.executable, '-c', "raise SystemExit('deliberate command failure')"],
                            capture_output=True, text=True, timeout=10)
    assert failed.returncode != 0
    path = jobs / 'job.json'
    raw = {'status': 'failed', 'experiment_id': 'arithmetic-failure', 'returncode': failed.returncode,
           'error': failed.stderr.strip()}
    path.write_text(json.dumps(raw))
    with bind_project_scope(scope):
        memory = ResearchMemory(scope.project)
        result = memory.import_directory(str(source))
        row = memory.get(result['ids'][0])
        assert row['result_quality'] == 'execution_failure' and row['metrics'] == {}
        with pytest.raises(ValueError, match='不能设为基线'):
            memory.select(row['id'], 'original')
        path.write_text(json.dumps({**raw, 'updated_at': 'later archive time', 'research_memory': {'added': 1}}))
        assert memory.import_directory(str(source))['added'] == 0


def test_corrupted_copied_evidence_blocks_reuse_and_cross_project_access(tmp_path: Path) -> None:
    scope = _scope(tmp_path)
    source, protocol, _ = _measure(tmp_path)
    with bind_project_scope(scope):
        memory = ResearchMemory(scope.project)
        row = _import(memory, source, protocol)
        memory.asset_path(row['evidence'][0]).write_text('changed after archival')
        assert not memory.get(row['id'])['integrity']['intact']
        assert not memory.retrieve('ArithmeticCheck', protocol=protocol)['hits'][0]['comparison']['reuse_allowed']
        with pytest.raises(ValueError, match='不能设为基线'):
            memory.select(row['id'], 'current_best')
        with pytest.raises(ValueError):
            memory.get('../outside')
    other = tmp_path / 'other-project'
    other.mkdir()
    with pytest.raises(ValueError, match='不存在'):
        ResearchMemory('other-project', root=other).get(row['id'])


def test_history_notes_and_external_links_never_become_valid_measurements(tmp_path: Path) -> None:
    scope = _scope(tmp_path)
    source = tmp_path / 'authored-history'
    source.mkdir()
    (source / 'research_history.json').write_text(json.dumps({'experiments': [
        {'title': 'Unverified human note', 'conclusion': 'No original measurement was supplied'}]}))
    outside = tmp_path / 'outside-summary.json'
    outside.write_text('{"metrics":{"mse":0}}')
    (source / 'summary.json').symlink_to(outside)
    with bind_project_scope(scope):
        memory = ResearchMemory(scope.project)
        result = memory.import_directory(str(source))
        assert result['added'] == 1
        row = memory.get(result['ids'][0])
        assert row['kind'] == 'human_history' and row['result_quality'] == 'human_reported'
        assert row['metrics'] == {}
        with pytest.raises(ValueError, match='不能设为基线'):
            memory.select(row['id'], 'original')


def test_current_project_tools_and_context_read_only_frozen_evidence(tmp_path: Path) -> None:
    scope = _scope(tmp_path)
    source, protocol, _ = _measure(tmp_path)
    with bind_project_scope(scope):
        memory = ResearchMemory(scope.project)
        row = _import(memory, source, protocol)
        ctx = ToolContext('actual-tool-read', scope.project, 'commander')
        no_pointer = asyncio.run(baseline_match_tool({'plan': {'protocol': protocol}}, ctx))
        assert no_pointer.ok and not no_pointer.output['reuse_recommended']
        memory.select(row['id'], 'original')
        matched = asyncio.run(baseline_match_tool({'plan': {'protocol': protocol}}, ctx))
        assert matched.ok and matched.output['reuse_recommended']
        missing_protocol = asyncio.run(baseline_match_tool({'plan': {}}, ctx))
        assert missing_protocol.ok and not missing_protocol.output['reuse_recommended']
        assert missing_protocol.output['recommended_action'] == 'inspect_missing_conditions'
        retrieved = asyncio.run(research_memory_tool({'query': 'ArithmeticCheck', 'protocol': protocol}, ctx))
        assert retrieved.ok and retrieved.output['hits'][0]['id'] == row['id']
        detail = asyncio.run(research_memory_read_tool({'record_id': row['id']}, ctx))
        assert detail.ok and detail.output['metrics'] == row['metrics']
        invalid = asyncio.run(research_memory_read_tool({'record_id': '../outside'}, ctx))
        assert not invalid.ok
        first = memory_context(scope.project, 'ArithmeticCheck', scope.run_root, 'invocation-1')
        memory.annotate(row['id'], {'conclusion': 'new retained annotation'})
        assert memory_context(scope.project, 'ArithmeticCheck', scope.run_root, 'invocation-1') == first
        assert 'new retained annotation' in memory_context(scope.project, 'ArithmeticCheck', scope.run_root, 'invocation-2')


def test_main_app_exposes_real_memory_routes_for_only_the_bound_temporary_project(tmp_path: Path) -> None:
    scope = _scope(tmp_path)
    source, protocol, _ = _measure(tmp_path)
    with bind_project_scope(scope):
        memory = ResearchMemory(scope.project)
        row = _import(memory, source, protocol)
        # TestClient sends genuine ASGI requests; lifespan is not entered, so
        # the test never starts background services, models or user tasks.
        client = TestClient(create_app())
        prefix = '/api/projects/' + scope.project + '/research-memory'
        assert client.get(prefix).json()['records'][0]['id'] == row['id']
        assert client.get(prefix + '/' + row['id']).json()['metrics'] == row['metrics']
        evidence = client.get(prefix + '/' + row['id'] + '/evidence/0')
        assert evidence.status_code == 200 and evidence.content == memory.asset_path(row['evidence'][0]).read_bytes()
        assert client.patch(prefix + '/' + row['id'], json={'metrics': {'mse': 0}}).status_code == 422
        assert client.get(prefix + '/' + row['id'] + '/evidence/-1').status_code == 404
        assert client.get('/api/projects/different-project/research-memory').status_code == 422
