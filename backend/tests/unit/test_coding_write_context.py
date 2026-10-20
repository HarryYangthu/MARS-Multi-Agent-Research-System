"""First-draft manifest from real registry writes and immutable postimages."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterator

import yaml

import pytest

from app.bridge.coding_write_context import coding_write_context
from app.harness.tools.registry import ToolContext, get_registry
from app.storage.run_store import RunHandle, RunStore


@pytest.fixture
def real_project(tmp_path: Path) -> Iterator[tuple[RunHandle, Path, ToolContext]]:
    from app.harness.project_workspace import open_folder
    from app.settings import reset_settings_cache
    name = 'MARS_FOLDER_PROJECTS_REGISTRY'
    previous = os.environ.get(name)
    os.environ[name] = str(tmp_path / 'project-registry.json')
    reset_settings_cache()
    try:
        project = open_folder(str(tmp_path / 'project'), create=True)
        root = project.root / 'code/example'
        root.mkdir(parents=True)
        (project.metadata_root / 'repo_link.yaml').write_text(yaml.safe_dump({
            'repo_path': str(root), 'repo_mode': 'local_path', 'read_only': False,
            'allowed_paths': [], 'ignore_patterns': ['.git/']}))
        run = RunStore(tmp_path / 'runs').create(task='manifest-context', project=project.name)
        ctx = ToolContext(run_id=run.run_id, project=run.project, agent='coding',
                          extra={'run_root': str(run.root)})
        yield run, root, ctx
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous
        reset_settings_cache()


@pytest.mark.asyncio
async def test_retry_context_includes_all_prior_net_writes_and_copy_postimages(real_project: tuple[RunHandle, Path, ToolContext]) -> None:
    from app.bridge.code_repository import BrowsePolicy, CodeRepository
    from app.harness.tools.project_repo import ProjectRepo
    from app.bridge.completed_code_changes import completed_code_changes
    run, root, ctx = real_project
    (root / 'base.yaml').write_text('# kept\nvalue: 1\n')
    old = await get_registry().dispatch('code.write_file', {'path': 'old.yaml', 'content': 'old: 1\n'}, ctx)
    new = await get_registry().dispatch('code.copy_yaml', {'source': 'base.yaml',
        'variants': [{'path': 'new.yaml', 'updates': {'value': 2}}]}, ctx)
    assert old.ok and new.ok
    first = json.loads(coding_write_context(run) or '{}')
    assert first['complete'] and first['verified']
    assert first['files_changed'] == [{'path': 'new.yaml', 'type': 'added'}, {'path': 'old.yaml', 'type': 'added'}]
    assert first['run_id'] == run.run_id and first['project'] == run.project
    assert 'diff' not in first and 'old: 1' not in json.dumps(first)
    from app.bridge.agent_runner import load_agent_handoff_context
    supplied, _ = load_agent_handoff_context(run, 'coding')
    assert json.loads(supplied['current_written_code_manifest']) == first
    repeated = await get_registry().dispatch('code.write_file', {'path': 'old.yaml', 'content': 'old: 1\n'}, ctx)
    assert repeated.ok
    retry = json.loads(coding_write_context(run) or '{}')
    assert retry['files_changed'] == first['files_changed'] and retry['write_records'] == 3
    browser = CodeRepository(ProjectRepo(run.project, root, 'local_path', False, ('',), (), ()), BrowsePolicy(200, 500, 8388608))
    assert len(completed_code_changes(run, project=run.project, repository=browser)['items']) == 2



def test_no_successful_writes_does_not_supply_empty_manifest(real_project: tuple[RunHandle, Path, ToolContext]) -> None:
    run, _, _ = real_project
    assert coding_write_context(run) is None


@pytest.mark.asyncio
async def test_current_tampering_is_warning_and_not_a_verified_partial_manifest(real_project: tuple[RunHandle, Path, ToolContext]) -> None:
    run, root, ctx = real_project
    result = await get_registry().dispatch('code.write_file', {'path': 'kept.yaml', 'content': 'value: 1\n'}, ctx)
    assert result.ok
    (root / 'kept.yaml').write_text('value: 99\n')
    value = json.loads(coding_write_context(run) or '{}')
    assert not value['verified'] and not value['complete'] and value['warnings']
    assert value['files_changed'] == [] and '不可声称核验通过' in value['instruction']


@pytest.mark.asyncio
async def test_unchanged_rewrite_not_in_net_manifest_and_context_source_is_recorded(real_project: tuple[RunHandle, Path, ToolContext]) -> None:
    from app.harness.context.compiler import compile_agent_context
    run, root, ctx = real_project
    (root / 'reused.yaml').write_text('value: 1\n')
    result = await get_registry().dispatch('code.write_file', {'path': 'reused.yaml', 'content': 'value: 1\n'}, ctx)
    assert result.ok
    text = coding_write_context(run)
    assert text is not None
    value = json.loads(text)
    assert value['files_changed'] == [] and value['unchanged'][0]['path'] == 'reused.yaml'
    compiled = compile_agent_context(system='Coding', project='Real files', task='Continue',
        upstream={'current_written_code_manifest': text}, agent_name='coding', output_schema='code_spec.v1', preserve_upstream=True)
    assert any(ref['source'] == 'upstream:current_written_code_manifest' for ref in compiled.manifest['messages'])
    assert any(text in str(message.content) for message in compiled.messages)
