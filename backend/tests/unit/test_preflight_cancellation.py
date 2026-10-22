"""Cancel an actual registered checker and its descendant, without training."""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from typing import Any

import pytest
import yaml

from app.harness.project_workspace import open_folder
from app.harness.tools.execution.command_preflight import preflight_state, run_command_preflight
from app.harness.tools.execution.local_command import LocalCommandJob
from app.settings import repo_root, reset_settings_cache


@contextmanager
def _host_environment(values: Mapping[str, str]) -> Iterator[None]:
    previous = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    reset_settings_cache()
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        reset_settings_cache()


@pytest.fixture
def sleeping_checker(tmp_path: Path) -> Iterator[tuple[LocalCommandJob, Path]]:
    execution_config = tmp_path / 'host-execution.yaml'
    tools_config = tmp_path / 'host-tools.yaml'
    with _host_environment({
        'MARS_FOLDER_PROJECTS_REGISTRY': str(tmp_path / 'registry.json'),
        'MARS_EXECUTION_BACKEND': 'local_command',
        'MARS_EXECUTION_CONFIG_PATH': str(execution_config),
        'MARS_TOOLS_CONFIG_PATH': str(tools_config),
        'COLUMNS': '80', 'LINES': '24',
    }):
        project = open_folder(str(tmp_path / 'project'), create=True)
        source = tmp_path / 'actual_sleeping_checker.py'
        source.write_text(
            "import json,os,pathlib,subprocess,sys,time\n"
            "root=pathlib.Path(os.environ['MARS_PROJECT_ROOT'])\n"
            "if os.environ.get('MARS_VALIDATE_ONLY')!='1':\n"
            " (root/'non_validation_branch_entered.txt').write_text('unexpected execution mode')\n"
            " raise RuntimeError('This checker must only validate inputs')\n"
            "request=json.loads(pathlib.Path(os.environ['MARS_JOB_REQUEST']).read_text())\n"
            "data=pathlib.Path(request['config']['data_path'])\n"
            "assert data==pathlib.Path(os.environ['MARS_DATA_PATH']) and data.is_relative_to(root)\n"
            "assert json.loads(data.read_text())==[1,2,3] and request['steps']==3\n"
            "ready=root/'descendant-ready.txt'\n"
            "child_program='import os,pathlib,sys,time;pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));time.sleep(60)'\n"
            "child=subprocess.Popen([sys.executable,'-c',child_program,str(ready)],stdin=subprocess.DEVNULL)\n"
            "deadline=time.monotonic()+5\n"
            "while not ready.is_file():\n"
            " assert child.poll() is None and time.monotonic()<deadline\n"
            " time.sleep(0.01)\n"
            "pending=root/'checker-pids.tmp'\n"
            "pending.write_text(json.dumps({'checker':os.getpid(),'descendant':child.pid}))\n"
            "os.replace(pending,root/'checker-pids.json')\n"
            "time.sleep(60)\n",
            encoding='utf-8',
        )
        data = project.root / 'samples.json'
        data.write_text('[1,2,3]', encoding='utf-8')
        command: dict[str, Any] = {
            'id': 'sleeping_checker', 'argv': [sys.executable, str(source)],
            'project_ids': [project.name], 'entrypoints': ['tools/check.py'],
            'config_paths': ['configs/*.yaml'], 'consumes_data_path': True,
            'required_metrics': ['mean'],
            'validation': {'environment': {'MARS_VALIDATE_ONLY': '1'},
                           'timeout_seconds': 30, 'max_output_bytes': 65536},
        }
        execution_config.write_text(yaml.safe_dump({'execution': {
            'backend': 'local_command', 'command_timeout_seconds': 30,
            'local_commands': [command],
        }}), encoding='utf-8')
        tools = yaml.safe_load((repo_root() / 'configs/tools.yaml').read_text())
        tools['tools']['execution.simulation_runner']['command_allowlist'] = [[sys.executable, str(source)]]
        tools_config.write_text(yaml.safe_dump(tools), encoding='utf-8')
        reset_settings_cache()
        yield LocalCommandJob(
            run_id='cancel-checker', experiment_id='actual-inputs', project=project.name,
            run_root=project.root / 'runs/cancel-checker', command_id='sleeping_checker',
            seed=2026, steps=3,
            config={'command_id': 'sleeping_checker', 'entrypoint': 'tools/check.py',
                    'config_path': 'configs/actual.yaml', 'data_path': str(data),
                    'budget_steps': 3, 'seed': 2026},
        ), project.root


def _pid_is_alive(pid: int) -> bool:
    if os.name == 'nt':
        found = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/NH', '/FO', 'CSV'],
                               stdin=subprocess.DEVNULL, capture_output=True, text=True,
                               timeout=5, check=True)
        return f'"{pid}"' in found.stdout
    found = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=5, check=False)
    # A killed orphan can briefly await the OS reaper as a zombie; it cannot run.
    return found.returncode == 0 and bool(found.stdout.strip()) and not found.stdout.lstrip().startswith('Z')


def _cleanup_process_group(pid: int) -> None:
    if os.name == 'nt':
        if _pid_is_alive(pid):
            subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
    else:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_cancellation_stops_actual_checker_group_and_persists_failure_without_training(
    sleeping_checker: tuple[LocalCommandJob, Path],
) -> None:
    job, project = sleeping_checker
    cancel = threading.Event()
    results: list[dict[str, Any]] = []
    errors: list[BaseException] = []

    def check() -> None:
        try:
            results.append(run_command_preflight(job, 'cancel-inputs', cancel_event=cancel))
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=check, name='real-command-preflight')
    pids: dict[str, int] = {}
    worker.start()
    try:
        started = project / 'checker-pids.json'
        deadline = time.monotonic() + 10
        while not started.is_file() and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert started.is_file(), errors
        pids = json.loads(started.read_text())
        assert _pid_is_alive(pids['checker'])
        assert _pid_is_alive(pids['descendant'])
        cancel.set()
        worker.join(timeout=8)
        assert not worker.is_alive(), 'Cancelling validation must stop the actual checker promptly'
        assert not errors, errors
        assert len(results) == 1
        state = results[0]
        assert state['status'] == 'failed'
        assert 'cancelled' in state['error'].casefold()
        receipt_path = Path(state['receipt_path'])
        receipt = json.loads(receipt_path.read_text())
        assert receipt['status'] == 'failed'
        assert 'cancelled' in receipt['error'].casefold()
        assert receipt['pid'] == pids['checker']
        assert isinstance(receipt['returncode'], int) and receipt['returncode'] != 0
        assert preflight_state(job, 'cancel-inputs')['status'] == 'failed'
        deadline = time.monotonic() + 3
        while any(_pid_is_alive(pid) for pid in pids.values()) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not any(_pid_is_alive(pid) for pid in pids.values()), pids
        assert not receipt_path.with_name('result.json').exists()
        assert not (project / 'non_validation_branch_entered.txt').exists()
        assert not (job.run_root / 'execution/local_commands').exists()
        assert (project / 'samples.json').read_text() == '[1,2,3]'
    finally:
        cancel.set()
        if pids:
            _cleanup_process_group(pids['checker'])
            if _pid_is_alive(pids['descendant']):
                if os.name == 'nt':
                    _cleanup_process_group(pids['descendant'])
                else:
                    try:
                        os.kill(pids['descendant'], signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        worker.join(timeout=10)


def test_failed_checker_exit_stops_its_actual_sleeping_descendant(
    sleeping_checker: tuple[LocalCommandJob, Path],
) -> None:
    job, project = sleeping_checker
    configuration = yaml.safe_load(Path(os.environ['MARS_EXECUTION_CONFIG_PATH']).read_text())
    source = Path(configuration['execution']['local_commands'][0]['argv'][1])
    original = source.read_text()
    assert original.endswith('time.sleep(60)\n')
    source.write_text(original.removesuffix('time.sleep(60)\n') + 'raise SystemExit(7)\n',
                      encoding='utf-8')
    pids: dict[str, int] = {}
    try:
        state = run_command_preflight(job, 'exited-checker-inputs')
        started = project / 'checker-pids.json'
        assert started.is_file()
        pids = json.loads(started.read_text())
        assert state['status'] == 'failed'
        assert 'status 7' in state['error']
        receipt_path = Path(state['receipt_path'])
        receipt = json.loads(receipt_path.read_text())
        assert receipt['pid'] == pids['checker'] and receipt['returncode'] == 7
        assert receipt['status'] == 'failed' and receipt['checks'] == []
        assert preflight_state(job, 'exited-checker-inputs')['status'] == 'failed'
        deadline = time.monotonic() + 3
        while _pid_is_alive(pids['descendant']) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not _pid_is_alive(pids['checker'])
        assert not _pid_is_alive(pids['descendant']), 'An exited checker must not leave its descendant running'
        assert not receipt_path.with_name('result.json').exists()
        assert not (project / 'non_validation_branch_entered.txt').exists()
        assert not (job.run_root / 'execution/local_commands').exists()
        assert (project / 'samples.json').read_text() == '[1,2,3]'
    finally:
        if not pids and (project / 'checker-pids.json').is_file():
            pids = json.loads((project / 'checker-pids.json').read_text())
        if pids:
            _cleanup_process_group(pids['checker'])
            if _pid_is_alive(pids['descendant']):
                if os.name == 'nt':
                    _cleanup_process_group(pids['descendant'])
                else:
                    try:
                        os.kill(pids['descendant'], signal.SIGKILL)
                    except ProcessLookupError:
                        pass
