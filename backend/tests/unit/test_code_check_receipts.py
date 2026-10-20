"""Genuine Git snapshots and check subprocesses; no provider/tool substitutes."""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import venv

import pytest

from app.harness.tools.code.check_receipts import (
    CheckIdentity, _dependencies, build_check_identity, record_check, reuse_check,
)
from app.harness.tools.config import CommandSpec, ToolConfig
from app.harness.tools.git_branch import git
from app.harness.tools.process_runtime import sanitized_subprocess_environment
from app.harness.tools.registry import ToolContext


@dataclass
class Fixture:
    root: Path
    run: Path
    data: Path
    ctx: ToolContext
    command: CommandSpec
    argv: tuple[str, ...]
    environment: dict[str, str]
    policy: ToolConfig

    def identity(self) -> CheckIdentity | None:
        return build_check_identity(self.ctx, self.root, 'test', self.command, self.argv,
                                    self.environment, self.policy, 10)


@pytest.fixture
def actual(tmp_path: Path) -> Fixture:
    prefix = tmp_path / 'registered-check-venv'
    venv.EnvBuilder(with_pip=False).create(prefix)
    python = prefix / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    project = tmp_path / 'project'
    root = project / 'code'; root.mkdir(parents=True)
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\ngenerated.log\n')
    (root / 'config.json').write_text('{"steps": 3}')
    (root / 'check.py').write_text('''import json, os, sys
from pathlib import Path
config = json.loads(Path('config.json').read_text())
data = json.loads(Path(os.environ['MARS_TEST_DATA_PATH']).read_text())
counter = Path(os.environ['MARS_RUN_ROOT']) / 'real_process_count'
counter.write_text(str(int(counter.read_text()) + 1) if counter.exists() else '1')
assert config['steps'] == 3 and data['values'] == [2,3,5]
print(json.dumps({'sum':sum(data['values']),'steps':config['steps']}))
sys.exit(int(os.environ.get('CHECK_EXIT', '0')))
''')
    git(root, 'init', '-b', 'main')
    git(root, 'add', '.')
    git(root, '-c', 'user.name=Test', '-c', 'user.email=test@localhost', '-c',
        'commit.gpgsign=false', 'commit', '-m', 'Actual check fixture')
    run = project / 'runs/check_run'; (run / 'input').mkdir(parents=True)
    data = project / 'data/sample.json'; data.parent.mkdir()
    data.write_text('{"values":[2,3,5]}')
    (run / 'input/selected_data_source.json').write_text(json.dumps({'stored_path':str(data)}))
    ctx = ToolContext(run_id=run.name, project='actual-project', agent='coding', extra={'run_root':str(run)})
    argv = (str(python), 'check.py')
    env = sanitized_subprocess_environment(overrides={'MARS_CODE_ROOT':str(root),
        'MARS_RUN_ROOT':str(run),'MARS_RUN_ID':run.name,'MARS_PROJECT':ctx.project,
        'MARS_TEST_PROJECT_ROOT':str(project),'MARS_TEST_DATA_PATH':str(data)})
    return Fixture(root,run,data,ctx,CommandSpec('actual_check','Actual check',argv),argv,env,
                   ToolConfig(command_allowlist=(argv,)))


def execute(fixture: Fixture, identity: CheckIdentity | None) -> dict[str, object]:
    started = time.monotonic()
    process = subprocess.Popen(fixture.argv,cwd=fixture.root,env=fixture.environment,
                               stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    stdout,stderr = process.communicate(timeout=10)
    result = {'id':fixture.command.id,'label':fixture.command.label,'argv':list(fixture.argv),
              'returncode':process.returncode,'stdout':stdout.decode(),'stderr':stderr.decode()}
    return record_check(identity,result,pid=process.pid,duration_seconds=time.monotonic()-started)


def test_identical_check_reuses_real_receipt_without_second_process(actual: Fixture) -> None:
    identity = actual.identity(); assert identity is not None
    assert reuse_check(identity) is None
    result = execute(actual,identity)
    assert result['returncode'] == 0 and result['reused'] is False
    cached = reuse_check(actual.identity())
    assert cached is not None and cached['reused'] is True and cached['check_started'] is False
    assert cached['stdout'] == result['stdout'] and json.loads(str(cached['stdout']))['sum'] == 10
    assert (actual.run/'real_process_count').read_text() == '1'
    receipt = json.loads(Path(str(cached['receipt_path'])).read_text())
    assert receipt['pid'] > 0 and receipt['duration_seconds'] > 0


@pytest.mark.parametrize('change',['source','untracked','deleted','config','data','selection','environment','command','policy','interpreter'])
def test_changed_real_inputs_cannot_reuse(actual: Fixture, change: str) -> None:
    first = actual.identity(); assert first is not None
    assert execute(actual,first)['returncode'] == 0
    if change == 'source':
        with (actual.root/'check.py').open('a') as stream: stream.write('\n# actual source edit\n')
    elif change == 'untracked': (actual.root/'new_module.py').write_text('VALUE=1\n')
    elif change == 'deleted': (actual.root/'check.py').unlink()
    elif change == 'config': (actual.root/'config.json').write_text('{"steps":4}')
    elif change == 'data': actual.data.write_text('{"values":[7,11]}')
    elif change == 'selection':
        (actual.run/'input/selected_data_source.json').write_text(json.dumps({'stored_path':str(actual.data),'version':2}))
    elif change == 'environment': actual.environment['ACTUAL_CHECK_OPTION']='changed'
    elif change == 'command': actual.command=replace(actual.command,id='other_check')
    elif change == 'policy': actual.policy=replace(actual.policy,timeout_seconds=31)
    else: actual.argv=(str(Path(getattr(sys, '_base_executable', sys.executable)).resolve()),'-B','check.py')
    changed = actual.identity()
    if change in {'interpreter', 'deleted'}:
        # Neither an unbound base interpreter nor a missing registered script
        # can certify a reusable check, even when an earlier run succeeded.
        assert changed is None
    else:
        assert changed is not None and changed.fingerprint != first.fingerprint
    assert reuse_check(changed) is None


def test_git_ignored_generated_log_does_not_change_source_identity(actual: Fixture) -> None:
    identity=actual.identity(); assert identity is not None
    execute(actual,identity)
    (actual.root/'generated.log').write_text('actual ignored generated output')
    current=actual.identity(); assert current is not None
    assert current.fingerprint==identity.fingerprint and reuse_check(current) is not None


def test_failed_real_check_is_not_cached(actual: Fixture) -> None:
    actual.environment['CHECK_EXIT']='7'
    identity=actual.identity(); assert identity is not None
    result=execute(actual,identity)
    assert result['returncode']==7 and reuse_check(actual.identity()) is None
    assert not list((actual.run/'coding/checks').glob('*/receipt.json'))


def test_source_written_by_check_does_not_become_reusable(actual: Fixture) -> None:
    with (actual.root/'check.py').open('a') as stream:
        stream.write("\nPath('new_generated_source.py').write_text('VALUE=1')\n")
    # Put the actual write before sys.exit, not in unreachable code.
    path=actual.root/'check.py';path.write_text(path.read_text().replace("sys.exit(int(os.environ.get('CHECK_EXIT', '0')))",''))
    identity=actual.identity(); assert identity is not None
    result=execute(actual,identity)
    assert result['returncode']==0 and (actual.root/'new_generated_source.py').exists()
    assert reuse_check(actual.identity()) is None


def test_different_run_never_reuses_previous_run(actual: Fixture) -> None:
    identity=actual.identity(); assert identity is not None
    execute(actual,identity)
    other=actual.run.with_name('other_run');(other/'input').mkdir(parents=True)
    (other/'input/selected_data_source.json').write_bytes((actual.run/'input/selected_data_source.json').read_bytes())
    actual.run=other;actual.ctx=ToolContext(run_id=other.name,project=actual.ctx.project,agent='coding',extra={'run_root':str(other)})
    actual.environment.update(MARS_RUN_ROOT=str(other),MARS_RUN_ID=other.name)
    current=actual.identity();assert current is not None
    assert reuse_check(current) is None


def test_changed_receipt_is_not_reused(actual: Fixture) -> None:
    identity=actual.identity(); assert identity is not None
    result=execute(actual,identity)
    receipt=Path(str(result['receipt_path']));receipt.write_text(receipt.read_text()+' ')
    assert reuse_check(actual.identity()) is None


def test_symbolic_or_unbound_project_input_falls_back_to_real_check(actual: Fixture) -> None:
    (actual.root/'alias.py').symlink_to(actual.root/'check.py')
    assert actual.identity() is None
    (actual.root/'alias.py').unlink()
    (actual.run/'input/selected_data_source.json').unlink()
    assert actual.identity() is None


def test_coding_guidance_prefers_batch_yaml_without_extra_tests() -> None:
    from app.agents.coding.agent import CodingAgent
    prompt=(Path(__file__).parents[2]/'app/agents/coding/prompts/code_spec.md').read_text()
    assert 'code.copy_yaml' in CodingAgent.agent_brief and 'common_updates' in CodingAgent.agent_brief
    assert '不新增测试文件' in prompt and '不训练预检' in prompt and 'reused=true' in prompt
    assert '不要为清理旧配置而删除或迁移文件' in prompt and '不要为清理旧配置而删除或迁移文件' in CodingAgent.agent_brief


def test_actual_venv_dependency_metadata_changes_are_bound(tmp_path: Path) -> None:
    prefix=tmp_path/'actual-venv'
    venv.EnvBuilder(with_pip=False).create(prefix)
    python=prefix/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    before=_dependencies(str(python))
    site=next(prefix.glob('lib/python*/site-packages')) if os.name!='nt' else prefix/'Lib/site-packages'
    metadata=site/'check_fixture-1.0.dist-info';metadata.mkdir()
    (metadata/'METADATA').write_text('Metadata-Version: 2.1\nName: check-fixture\nVersion: 1.0\n')
    (metadata/'RECORD').write_text('check_fixture.py,,\n')
    installed=_dependencies(str(python))
    assert installed!=before
    (metadata/'RECORD').write_text('check_fixture.py,sha256=changed,12\n')
    assert _dependencies(str(python))!=installed
    (metadata/'direct_url.json').write_text('{"dir_info":{"editable":true}}')
    with pytest.raises(ValueError,match='editable'):
        _dependencies(str(python))


def test_symbolic_receipt_directory_is_not_written(actual: Fixture,tmp_path: Path) -> None:
    identity=actual.identity();assert identity is not None
    (actual.run/'coding').mkdir()
    outside=tmp_path/'outside';outside.mkdir()
    (actual.run/'coding/checks').symlink_to(outside,target_is_directory=True)
    result=execute(actual,identity)
    assert result['returncode']==0 and not list(outside.iterdir())


def test_external_script_edit_never_reuses_old_success(actual: Fixture) -> None:
    external=actual.root.parent/'host-check.py'
    external.write_text('print("actual first external check")\n')
    actual.argv=(actual.argv[0],str(external))
    actual.command=replace(actual.command,argv=actual.argv)
    first=actual.identity()
    assert first is None  # Its sibling imports cannot be certified by this repo.
    assert execute(actual,first)['returncode']==0
    external.write_text('import sys\nsys.exit(9)\n')
    current=actual.identity()
    assert reuse_check(current) is None
    assert execute(actual,current)['returncode']==9
