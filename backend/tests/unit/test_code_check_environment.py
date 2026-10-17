"""Real isolated Git checkouts, project data, and actual check subprocesses."""
from __future__ import annotations

from collections.abc import Iterator
import json
from pathlib import Path
import sys

import pytest
import yaml

from app.bridge.research_branch import research_branch_scope
from app.harness.project_workspace import open_folder
from app.harness.runtime.git_runtime import resolve_git
from app.harness.tools.code import lint_tool, test_runner_tool as run_tests_tool
from app.harness.tools.git_branch import git
from app.harness.tools.project_repo import load_project_repo
from app.harness.tools.registry import ToolContext
from app.settings import reset_settings_cache
from app.storage.run_store import RunHandle

_CHECK = '''import json, os, sys
from pathlib import Path
root = Path(os.environ['MARS_CODE_ROOT'])
project = Path(os.environ.get('MARS_TEST_PROJECT_ROOT') or os.environ['MARS_PROJECT_ROOT'])
assert root == Path.cwd() and root.is_relative_to(project / 'code')
selected = os.environ.get('MARS_TEST_DATA_PATH') or os.environ.get('MARS_DATA_PATH')
data = Path(selected or str(root / '../../data/sample.json')).resolve()
assert data.is_relative_to(project), 'project boundary remains enforced'
values = json.loads(data.read_text())['values']
run_root = Path(os.environ['MARS_RUN_ROOT'])
(run_root / 'check_started').write_text('real subprocess')
print(json.dumps({'sum': sum(values), 'python': sys.executable, 'code_root': str(root),
                  'project_root': str(project), 'data': str(data),
                  'selected': selected is not None,
                  'runtime_project': os.environ.get('MARS_PROJECT_ROOT'),
                  'runtime_data': os.environ.get('MARS_DATA_PATH'),
                  'request': os.environ.get('MARS_JOB_REQUEST'),
                  'result': os.environ.get('MARS_RESULT_PATH'),
                  'validate_only': os.environ.get('MARS_VALIDATE_ONLY'),
                  'secret': 'COMPANY_API_KEY' in os.environ}))
'''


@pytest.fixture
def bound_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[RunHandle, Path]]:
    # Keep the genuinely verified host Git while later narrowing PATH to test
    # Python resolution. Do not turn that interpreter test into a missing-Git test.
    real_git = resolve_git().executable
    monkeypatch.setenv('MARS_GIT_EXECUTABLE', real_git)
    monkeypatch.setenv('MARS_FOLDER_PROJECTS_REGISTRY', str(tmp_path / 'registry.json'))
    reset_settings_cache()
    project = open_folder(str(tmp_path / 'project'), create=True)
    source = project.root / 'code/baseline'; source.mkdir(parents=True)
    (source / 'check_inputs.py').write_text(_CHECK)
    git(source, 'init', '-b', 'main')
    git(source, 'add', '.')
    git(source, '-c', 'user.name=Test', '-c', 'user.email=test@localhost',
        '-c', 'commit.gpgsign=false', 'commit', '-m', 'Actual input-reading check')
    (project.metadata_root / 'repo_link.yaml').write_text(yaml.safe_dump({
        'repo_path': str(source), 'repo_mode': 'local_path', 'read_only': True,
        'allowed_paths': ['check_inputs.py'], 'ignore_patterns': ['.git/', '.env*'],
    }))
    data = project.root / 'data/sample.json'; data.parent.mkdir(parents=True, exist_ok=True)
    data.write_text(json.dumps({'values': [2, 3, 5]}))
    run_root = project.root / 'runs/check_run'; run_root.mkdir(parents=True)
    run = RunHandle('check_run', run_root, project.name, 'check', 'coding', '2026-10-14T00:00:00Z')
    execution = tmp_path / 'execution.yaml'
    execution.write_text(yaml.safe_dump({
        'execution': {'backend': 'local_command', 'command_timeout_seconds': 10},
        'code_checks': {kind: {'commands': [{'id': 'inputs', 'argv': ['python', 'check_inputs.py']}]}
                        for kind in ('test', 'lint')},
    }))
    tools = tmp_path / 'tools.yaml'
    tools.write_text(yaml.safe_dump({'tools': {name: {
        'enabled': True, 'process_backend': 'local_process',
        'command_allowlist': [['python', 'check_inputs.py']],
    } for name in ('code.test_runner', 'code.lint')}}))
    monkeypatch.setenv('MARS_EXECUTION_CONFIG_PATH', str(execution))
    monkeypatch.setenv('MARS_TOOLS_CONFIG_PATH', str(tools))
    # Resolve the host's Python before the native Git child PATH is installed.
    monkeypatch.setenv('PATH', str(Path(sys.executable).parent))
    reset_settings_cache()
    try:
        yield run, data
    finally:
        reset_settings_cache()


def _context(run: RunHandle) -> ToolContext:
    return ToolContext(run_id=run.run_id, project=run.project, agent='coding',
                       extra={'run_root': str(run.root)})


def _select(run: RunHandle, path: Path | str) -> None:
    selected = run.root / 'input/selected_data_source.json'
    selected.parent.mkdir(parents=True, exist_ok=True)
    selected.write_text(json.dumps({'stored_path': str(path)}))


@pytest.mark.asyncio
async def test_real_check_reads_project_data_beside_isolated_checkout(
    bound_project: tuple[RunHandle, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, data = bound_project
    for name in ('MARS_CODE_ROOT', 'MARS_PROJECT_ROOT', 'MARS_DATA_PATH', 'MARS_TEST_PROJECT_ROOT',
                 'MARS_TEST_DATA_PATH', 'MARS_JOB_REQUEST',
                 'MARS_RESULT_PATH', 'MARS_VALIDATE_ONLY'):
        monkeypatch.setenv(name, '/stale/other/research')
    monkeypatch.setenv('COMPANY_API_KEY', 'test-secret-must-not-reach-child')
    with research_branch_scope(run, 'coding'):
        root = load_project_repo(run.project).root
        outcome = await run_tests_tool({'command_id': 'inputs', 'env': {'MARS_PROJECT_ROOT': '/outside'},
                                        'data_path': '/outside/not-granted', 'project_binding': 'runtime'}, _context(run))
        assert outcome.ok, outcome.error or outcome.output
        actual = json.loads(outcome.output['results'][0]['stdout'])
        assert actual['sum'] == 10 and actual['code_root'] == str(root)
        assert Path(actual['data']) == data and Path(actual['project_root']) == data.parent.parent
        assert actual['python'] == sys.executable
        assert actual['selected'] is False and actual['secret'] is False
        assert actual['runtime_project'] is None and actual['runtime_data'] is None
        assert actual['request'] is None and actual['result'] is None and actual['validate_only'] is None
        assert (run.root / 'check_started').read_text() == 'real subprocess'


@pytest.mark.asyncio
async def test_only_host_command_can_select_runtime_binding(
    bound_project: tuple[RunHandle, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os
    run, data = bound_project
    config_path = Path(os.environ['MARS_EXECUTION_CONFIG_PATH'])
    config = yaml.safe_load(config_path.read_text())
    config['code_checks']['test']['commands'][0]['project_binding'] = 'runtime'
    config_path.write_text(yaml.safe_dump(config))
    _select(run, data)
    with research_branch_scope(run, 'coding'):
        outcome = await run_tests_tool({'command_id': 'inputs'}, _context(run))
        assert outcome.ok, outcome.error or outcome.output
        actual = json.loads(outcome.output['results'][0]['stdout'])
        assert actual['runtime_project'] == str(data.parent.parent)
        assert actual['runtime_data'] == str(data) and actual['sum'] == 10


@pytest.mark.asyncio
async def test_real_pytest_temp_fixtures_remain_separate_from_opted_in_project_child(
    bound_project: tuple[RunHandle, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os
    run, data = bound_project
    source = data.parent.parent / 'code/baseline'
    (source / 'test_binding.py').write_text('''import json, os, subprocess, sys
from pathlib import Path

def test_temporary_project_fixture(tmp_path):
    assert 'MARS_PROJECT_ROOT' not in os.environ
    assert 'MARS_DATA_PATH' not in os.environ
    local = tmp_path / 'fixture.json'
    local.write_text('local fixture')
    assert local.resolve().is_relative_to(tmp_path)
    assert not local.resolve().is_relative_to(Path(os.environ['MARS_TEST_PROJECT_ROOT']))

def test_only_project_child_opts_into_real_data():
    env = dict(os.environ)
    env['MARS_PROJECT_ROOT'] = env.pop('MARS_TEST_PROJECT_ROOT')
    env['MARS_DATA_PATH'] = env.pop('MARS_TEST_DATA_PATH')
    child = subprocess.run([sys.executable, 'check_inputs.py'], env=env, capture_output=True, text=True)
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout)['sum'] == 10
''')
    git(source, 'add', 'test_binding.py')
    git(source, '-c', 'user.name=Test', '-c', 'user.email=test@localhost',
        '-c', 'commit.gpgsign=false', 'commit', '-m', 'Real fixture isolation regression')
    config_path = Path(os.environ['MARS_EXECUTION_CONFIG_PATH'])
    config = yaml.safe_load(config_path.read_text())
    config['code_checks']['test']['commands'][0]['argv'] = [
        'python', '-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'test_binding.py']
    config_path.write_text(yaml.safe_dump(config))
    tools_path = Path(os.environ['MARS_TOOLS_CONFIG_PATH'])
    tools = yaml.safe_load(tools_path.read_text())
    tools['tools']['code.test_runner']['command_allowlist'] = [['python', '-m', 'pytest']]
    tools_path.write_text(yaml.safe_dump(tools))
    _select(run, data)
    monkeypatch.setenv('MARS_PROJECT_ROOT', '/stale/runtime/project')
    with research_branch_scope(run, 'coding'):
        outcome = await run_tests_tool({'command_id': 'inputs'}, _context(run))
        assert outcome.ok, outcome.error or outcome.output
        assert '2 passed' in outcome.output['results'][0]['stdout']
        assert (run.root / 'check_started').read_text() == 'real subprocess'


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['test', 'lint'])
async def test_selected_data_and_interpreter_match_genuine_execution_bindings(
    bound_project: tuple[RunHandle, Path], kind: str,
) -> None:
    from app.harness.tools.execution.local_command import LocalCommandJob, _bound_workspace
    from app.harness.tools.execution.command_preflight import _checked_path
    run, data = bound_project
    alternate = data.with_name('selected.json'); alternate.write_text(json.dumps({'values': [13, 17]}))
    # Host intake permits a repository-relative locator into project-owned data.
    _select(run, '../../data/selected.json')
    with research_branch_scope(run, 'coding'):
        tool = run_tests_tool if kind == 'test' else lint_tool
        outcome = await tool({'command_id': 'inputs'}, _context(run))
        assert outcome.ok, outcome.error or outcome.output
        actual = json.loads(outcome.output['results'][0]['stdout'])
        job = LocalCommandJob(run.run_id, 'check', run.project, run.root)
        code, project = _bound_workspace(job)
        assert actual['code_root'] == str(code) and actual['project_root'] == str(project)
        assert actual['data'] == str(_checked_path(job, str(alternate)))
        assert actual['sum'] == 30 and actual['selected'] is True and actual['python'] == sys.executable
        assert actual['runtime_project'] is None and actual['runtime_data'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['outside', 'credential', 'symlink', 'missing', 'malformed'])
async def test_invalid_host_selected_data_is_rejected_before_child_launch(
    bound_project: tuple[RunHandle, Path], invalid: str, tmp_path: Path,
) -> None:
    run, data = bound_project
    if invalid == 'outside':
        outside = tmp_path / 'outside.json'; outside.write_text(data.read_text())
        _select(run, outside)
    elif invalid == 'credential':
        credential = data.parent / '.env'; credential.write_text(data.read_text())
        _select(run, credential)
    elif invalid == 'symlink':
        linked = data.with_name('linked.json'); linked.symlink_to(data)
        _select(run, linked)
    elif invalid == 'missing':
        _select(run, data.with_name('missing.json'))
    else:
        _select(run, data)
        (run.root / 'input/selected_data_source.json').write_text('{broken')
    with research_branch_scope(run, 'coding'):
        outcome = await run_tests_tool({'command_id': 'inputs'}, _context(run))
        assert not outcome.ok and 'input binding failed' in str(outcome.error)
        assert outcome.output == {'executed': False}
        assert not (run.root / 'check_started').exists()
