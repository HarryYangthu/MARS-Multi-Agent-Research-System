"""Actual configuration files and registry dispatch; no tool/provider doubles."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterator

import pytest
import yaml

from app.harness.project_workspace import open_folder
from app.harness.tools.code.yaml_copy import YamlCopyError, render_yaml_copy
from app.harness.tools.registry import ToolContext, get_registry
from app.settings import reset_settings_cache
from app.storage.run_store import RunStore


def setup(tmp_path: Path) -> tuple[Path, ToolContext]:
    root = tmp_path / 'repo'
    root.mkdir()
    run = RunStore(tmp_path / 'runs').create(task='copy-configurations', project='yaml-copy-local')
    return root, ToolContext(agent='coding', run_id=run.run_id, project=run.project,
                             project_repo_root=str(root), extra={'run_root': str(run.root)})


@contextmanager
def folder_registry(tmp_path: Path) -> Iterator[None]:
    name = 'MARS_FOLDER_PROJECTS_REGISTRY'
    previous = os.environ.get(name)
    os.environ[name] = str(tmp_path / 'registry.json')
    reset_settings_cache()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous
        reset_settings_cache()


@pytest.mark.parametrize('newline,bom', [('\n', ''), ('\r\n', ''), ('\r\n', '\ufeff')])
def test_scalar_updates_preserve_original_comments_format_and_unmodified_bytes(newline: str, bom: str) -> None:
    original = bom + '# baseline\ntrain:\n  lr: 4.0e-4  # tune\n  epochs: 1\nname: "unchanged"\n'.replace('\n', newline)
    result = render_yaml_copy(original, {'train': {'lr': 0.0002}})
    assert result == original.replace('4.0e-4', '0.0002')
    assert yaml.safe_load(result)['train']['lr'] == 0.0002


@pytest.mark.parametrize('text,expected', [
    ('train: {lr: 0.004, batch: 512} # keep\n', 'train: {lr: 0.002, batch: 512} # keep\n'),
    ('train:\n  lr: 0.004\nnotes: |\n  preserve this\n  whole block\n', 'train:\n  lr: 0.002\nnotes: |\n  preserve this\n  whole block\n'),
])
def test_flow_and_unmodified_multiline_syntax_are_supported(text: str, expected: str) -> None:
    assert render_yaml_copy(text, {'train': {'lr': 0.002}}) == expected


@pytest.mark.parametrize('text,updates', [
    ('train:\n  lr: 0.004 # old\nother: 7\n', {'train': {'seed': 2026}, 'Epoch': 1}),
    ('train: {} # empty\n', {'train': {'seed': 2026}}),
    ('train: {lr: 0.004} # flow\n', {'train': {'seed': 2026}}),
    ('\ufeffname: original', {'seed': 2026, 'nested': {'batch': 256}}),
])
def test_explicit_new_fields_create_only_requested_mappings(text: str, updates: dict[str, Any]) -> None:
    result = render_yaml_copy(text, updates, allow_new_keys=True)
    parsed = yaml.safe_load(result)
    for key, value in updates.items():
        if isinstance(value, dict):
            assert all(parsed[key][k] == v for k, v in value.items())
        else:
            assert parsed[key] == value
    if '# old' in text:
        assert '# old' in result and 'other: 7' in result


@pytest.mark.parametrize('text,updates,error', [
    ('value: 1\n', {'unknown': 2}, 'unknown configuration field'),
    ('value: 1\nvalue: 2\n', {}, 'duplicate YAML key'),
    ('train:\n  lr: 1\n  lr: 2\n', {}, 'duplicate YAML key'),
    ('one: &shared {value: 1}\ntwo: *shared\n', {}, 'anchors/aliases'),
    ('value: {<<: {nested: 1}}\n', {}, 'merge keys'),
    ('values: [1, 2]\n', {'values': 3}, 'not a scalar'),
    ('value: 1\n', {'value': [1, 2]}, 'scalar or nested mapping'),
    ('value: 1\n', {'value': float('nan')}, 'finite'),
    ('value: |\n  multiline\n', {'value': 'single'}, 'multiline scalar'),
])
def test_ambiguous_or_unrequested_structural_changes_have_actionable_failure(text: str, updates: dict[str, Any], error: str) -> None:
    with pytest.raises((YamlCopyError, yaml.YAMLError), match=error):
        render_yaml_copy(text, updates)


@pytest.mark.asyncio
async def test_twelve_variants_share_common_updates_and_archive_exact_postimages(tmp_path: Path) -> None:
    root, ctx = setup(tmp_path)
    original = '# keep\ntrain:\n  lr: 0.0004  # tuned\n  batch: 512\nseed: 1\nEpoch: 3\n'
    (root / 'source.yaml').write_text(original)
    (root / 'v0.yaml').write_bytes(b'previous: file\r\n')
    variants = [{'path': f'v{i}.yaml', 'updates': {'train': {'lr': 0.0001 * (i + 1)}}} for i in range(12)]
    result = await get_registry().dispatch('code.copy_yaml', {
        'source': 'source.yaml', 'variants': variants, 'common_updates': {'seed': 2026, 'Epoch': 1}}, ctx)
    assert result.ok, result.error
    assert len(result.output['files']) == 12 and result.output['applied']
    assert (root / 'source.yaml').read_text() == original
    saved = json.loads(Path(result.rollback_ref or '').read_text())
    assert len(saved['snapshots']) == len(saved['after_snapshots']) == 12
    for item in result.output['files']:
        raw = (root / item['path']).read_bytes()
        data = yaml.safe_load(raw)
        assert data['seed'] == 2026 and data['Epoch'] == 1
        assert item['sha256'] == 'sha256:' + hashlib.sha256(raw).hexdigest()
        assert '# keep' in raw.decode() and '# tuned' in raw.decode()
        after = next(snapshot for snapshot in saved['after_snapshots'] if snapshot['path'] == item['path'])
        assert after['content'].encode() == raw and after['sha256'] == item['sha256']
    rolled = await get_registry().dispatch('code.rollback_patch', {'rollback_ref': result.rollback_ref}, ctx)
    assert rolled.ok, rolled.error
    assert (root / 'v0.yaml').read_bytes() == b'previous: file\r\n'
    assert not any((root / f'v{i}.yaml').exists() for i in range(1, 12))


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', [
    {'path': 'bad.yaml', 'updates': {'missing': 1}},
    {'path': '../outside.yaml', 'updates': {}},
    {'path': 'source.yaml', 'updates': {}},
    {'path': 'first.yaml', 'updates': {}},
    {'path': 'not_yaml.py', 'updates': {}},
])
async def test_entire_batch_is_validated_before_any_write(tmp_path: Path, bad: dict[str, Any]) -> None:
    root, ctx = setup(tmp_path)
    (root / 'source.yaml').write_text('value: 1\n')
    (root / 'first.yaml').write_text('previous: 9\n')
    result = await get_registry().dispatch('code.copy_yaml', {'source': 'source.yaml', 'variants': [
        {'path': 'first.yaml', 'updates': {'value': 2}}, bad]}, ctx)
    assert not result.ok
    assert (root / 'first.yaml').read_text() == 'previous: 9\n'
    assert not (tmp_path / 'outside.yaml').exists()


@pytest.mark.asyncio
async def test_actual_parent_creation_failure_keeps_all_destinations_unchanged(tmp_path: Path) -> None:
    root, ctx = setup(tmp_path)
    (root / 'source.yaml').write_text('value: 1\n')
    (root / 'first.yaml').write_text('previous: 9\n')
    (root / 'blocked').write_text('a real regular file cannot become a directory')
    result = await get_registry().dispatch('code.copy_yaml', {'source': 'source.yaml', 'variants': [
        {'path': 'first.yaml'}, {'path': 'blocked/last.yaml'}]}, ctx)
    assert not result.ok
    assert (root / 'first.yaml').read_text() == 'previous: 9\n'
    assert not list(root.glob('.mars-yaml-*'))


@pytest.mark.asyncio
@pytest.mark.parametrize('location', ['source', 'destination', 'parent'])
async def test_symlink_paths_do_not_redirect_copy(tmp_path: Path, location: str) -> None:
    root, ctx = setup(tmp_path)
    (root / 'source.yaml').write_text('value: 1\n')
    (root / 'actual.yaml').write_text('previous: 7\n')
    source, target = 'source.yaml', 'next.yaml'
    if location == 'source':
        (root / 'alias.yaml').symlink_to(root / source)
        source = 'alias.yaml'
    elif location == 'destination':
        (root / 'alias.yaml').symlink_to(root / 'actual.yaml')
        target = 'alias.yaml'
    else:
        (root / 'actual').mkdir()
        (root / 'alias').symlink_to(root / 'actual', target_is_directory=True)
        target = 'alias/next.yaml'
    result = await get_registry().dispatch('code.copy_yaml', {'source': source, 'variants': [{'path': target}]}, ctx)
    assert not result.ok
    assert (root / 'actual.yaml').read_text() == 'previous: 7\n'
    assert not (root / 'next.yaml').exists() and not (root / 'actual/next.yaml').exists()


@pytest.mark.asyncio
async def test_readonly_and_protected_paths_remain_blocked_for_real_project(tmp_path: Path) -> None:
    with folder_registry(tmp_path):
        project = open_folder(str(tmp_path / 'project'), create=True)
        run = RunStore(tmp_path / 'runs').create(task='yaml-permissions', project=project.name)
        (project.root / 'source.yaml').write_text('value: 1\n')
        ctx = ToolContext(agent='coding', project=project.name, run_id=run.run_id,
                          extra={'run_root': str(run.root)})
        args = {'source': 'source.yaml', 'variants': [{'path': 'baseline/candidate.yaml'}]}
        protected = await get_registry().dispatch('code.copy_yaml', args, ctx)
        assert not protected.ok
        assert not (project.root / 'baseline/candidate.yaml').exists()
        link = project.metadata_root / 'repo_link.yaml'
        data = yaml.safe_load(link.read_text())
        data['read_only'] = True
        link.write_text(yaml.safe_dump(data))
        denied = await get_registry().dispatch('code.copy_yaml', {
            'source': 'source.yaml', 'variants': [{'path': 'candidate.yaml'}]}, ctx)
        assert not denied.ok and 'read_only' in (denied.error or '')
        assert not (project.root / 'candidate.yaml').exists()


@pytest.mark.asyncio
async def test_dry_run_has_no_receipt_or_file_side_effect(tmp_path: Path) -> None:
    root, ctx = setup(tmp_path)
    (root / 'source.yaml').write_text('value: 1\n')
    result = await get_registry().dispatch('code.copy_yaml', {'source': 'source.yaml',
        'variants': [{'path': 'nested/candidate.yaml', 'updates': {'value': 2}}]}, replace(ctx, dry_run=True))
    assert result.ok and not result.output['applied'] and not result.rollback_ref
    assert not (root / 'nested').exists()


@pytest.mark.asyncio
async def test_actual_task_branch_records_and_checkpoints_all_yaml_postimages(tmp_path: Path) -> None:
    from app.bridge.research_branch import research_branch_scope
    from app.harness.tools.git_branch import changes_path, git, receipt_path
    with folder_registry(tmp_path):
        project = open_folder(str(tmp_path / 'project'), create=True)
        source = project.root / 'code/example'
        source.mkdir(parents=True)
        (source / 'base.yaml').write_text('# preserve\nvalue: 1\n')
        git(source, 'init', '-b', 'main')
        git(source, 'add', '.')
        git(source, '-c', 'user.name=Test', '-c', 'user.email=test@localhost',
            '-c', 'commit.gpgsign=false', 'commit', '-m', 'Baseline configuration')
        baseline = git(source, 'rev-parse', 'HEAD')
        (project.metadata_root / 'repo_link.yaml').write_text(yaml.safe_dump({
            'repo_path': str(source), 'repo_mode': 'local_path', 'read_only': True,
            'allowed_paths': ['*.yaml'], 'ignore_patterns': ['.git/']}))
        run = RunStore(tmp_path / 'runs').create(task='copy-branch', project=project.name, entrypoint='coding')
        ctx = ToolContext(agent='coding', project=project.name, run_id=run.run_id, extra={'run_root': str(run.root)})
        with research_branch_scope(run, 'coding'):
            result = await get_registry().dispatch('code.copy_yaml', {'source': 'base.yaml',
                'variants': [{'path': 'candidate.yaml', 'updates': {'value': 2}}]}, ctx)
            assert result.ok, result.error
            ledger = json.loads(changes_path(run.root).read_text())
            assert 'sha256:' + ledger['files']['candidate.yaml'] == result.output['files'][0]['sha256']
        bound = json.loads(receipt_path(run.root).read_text())
        candidate_root = Path(bound['repo_path'])
        assert git(candidate_root, 'status', '--porcelain') == ''
        assert (candidate_root / 'candidate.yaml').read_text() == '# preserve\nvalue: 2\n'
        assert git(source, 'rev-parse', 'HEAD') == baseline and git(source, 'branch', '--show-current') == 'main'
        assert not (source / 'candidate.yaml').exists()


def contract_setup(tmp_path: Path) -> tuple[Any, Any, ToolContext]:
    from app.bridge.research_contract_service import freeze_research_task
    from app.bridge.research_run_service import persist_run_research_contract
    from app.bridge.research_project_scope import prepare_project_scope
    from app.harness.discovery.snapshots import SnapshotPolicy
    from app.harness.runtime.research_budget_ledger import ResearchBudgetLedger
    from app.harness.runtime.research_contract import ProjectContract, ResearchBudget
    from app.harness.runtime.research_execution_scope import ResearchExecutionScope
    from app.harness.runtime.run_graph import RunGraph
    from app.harness.runtime.state_journal import StateJournal
    from app.storage.run_state_store import RunStateStore

    source = tmp_path / 'source'
    source.mkdir()
    (source / 'base.yaml').write_text('# baseline\nvalue: 1\n')
    budget_raw = yaml.safe_load((Path(__file__).resolve().parents[3] / 'configs/research_defaults.yaml').read_text())['budget']
    budget = ResearchBudget.model_validate(budget_raw)
    project = ProjectContract.model_validate({'project_id': 'generic', 'display_name': 'YAML copy scope',
        'paths': {'code': str(source), 'data': [], 'knowledge': [], 'output': str(tmp_path / 'output')},
        'commands': [{'name': purpose, 'purpose': purpose, 'executable': sys.executable,
                      'arguments': ['base.yaml'], 'entrypoint_files': ['base.yaml']} for purpose in ('check', 'train', 'evaluate')],
        'metrics': [{'name': 'mse', 'unit': 'unitless', 'direction': 'minimize', 'target': 0, 'tolerance': 0}],
        'baseline_files': ['base.yaml'], 'allowed_paths': ['candidate.yaml'], 'protected_paths': [],
        'execution': {'kind': 'local', 'device': 'cpu'}})
    frozen = freeze_research_task(project, goal='Real scoped YAML copy', mode='manual', budget=budget)
    run = RunStore(tmp_path / 'runs').create(task='yaml-contract', project='generic', entrypoint='pipeline')
    persist_run_research_contract(run, frozen)
    (run.root / 'input/run_request_options.v1.json').write_text(json.dumps({
        'schema_id': 'run_request_options.v1', 'extra': {'research_task_sha256': frozen.task_sha256}}))
    graph = RunGraph()
    graph.add_node('coding')
    RunStateStore(run).write(graph=graph, request={'extra': {'research_task_sha256': frozen.task_sha256}},
                            status='created', expected_revision=0)
    journal = StateJournal.from_authority(run.root, run_id=run.run_id)
    assert journal is not None
    ledger = ResearchBudgetLedger(journal, task_sha256=frozen.task_sha256, budget=budget)
    ledger.initialize()
    scope = prepare_project_scope(run, candidate_id='one', snapshot_policy=SnapshotPolicy(allowed_paths=('*',)))
    execution = ResearchExecutionScope(ledger, stage='coding', invocation_id='yaml-copy')
    ctx = ToolContext(run_id=run.run_id, project=run.project, agent='coding', extra={'run_root': str(run.root)})
    return scope, execution, ctx


@pytest.mark.asyncio
async def test_scoped_copy_records_actual_budget_and_read_source_digest(tmp_path: Path) -> None:
    from app.harness.runtime.project_scope import bind_project_scope
    from app.harness.runtime.research_execution_scope import bind_research_execution
    scope, execution, ctx = contract_setup(tmp_path)
    with bind_research_execution(execution), bind_project_scope(scope):
        result = await get_registry().dispatch('code.copy_yaml', {'source': 'base.yaml',
            'variants': [{'path': 'candidate.yaml', 'updates': {'value': 2}}]}, ctx)
    assert result.ok, result.error
    assert result.metadata['changed_paths'] == ['candidate.yaml']
    assert execution.ledger.snapshot().used['tool_executions'] == 1
    assert execution.ledger.snapshot().used['implemented_candidates'] == 1
    assert (tmp_path / 'source/base.yaml').read_text() == '# baseline\nvalue: 1\n'
    assert (scope.candidate_root / 'candidate.yaml').read_text() == '# baseline\nvalue: 2\n'
    receipts = list((scope.run_root / 'resources/tool_receipts').glob('*.json'))
    receipt = json.loads(receipts[0].read_text())
    assert receipt['outcome'] == 'observed_success'
    assert receipt['read_inputs']['base.yaml']['sha256'] == 'sha256:' + hashlib.sha256(b'# baseline\nvalue: 1\n').hexdigest()


@pytest.mark.asyncio
async def test_source_changed_after_contract_preparation_is_blocked_before_write(tmp_path: Path) -> None:
    from app.harness.runtime.project_scope import bind_project_scope
    from app.harness.runtime.research_execution_scope import bind_research_execution
    from app.harness.tools.code import copy_yaml_tool
    from app.harness.tools.research_accounting import execute_tool, prepare_tool
    scope, execution, ctx = contract_setup(tmp_path)
    with bind_research_execution(execution), bind_project_scope(scope):
        plan = prepare_tool(execution, scope, 'code.copy_yaml', {'source': 'base.yaml',
            'variants': [{'path': 'candidate.yaml', 'updates': {'value': 2}}]}, ctx, copy_yaml_tool)
        (scope.candidate_root / 'base.yaml').write_text('value: 99\n')
        result = await execute_tool(plan, copy_yaml_tool, ctx, {'type': 'object'}, 10)
    assert not result.ok and 'source changed' in (result.error or '')
    assert execution.ledger.snapshot().used['tool_executions'] == 0
    assert not (scope.candidate_root / 'candidate.yaml').exists()
