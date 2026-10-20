"""Bounded logs and cleanup verified with actual local subprocesses."""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import time
import tracemalloc

import pytest
import yaml

from app.harness.tools.execution.local_command import LocalCommandJob, run_local_command
from app.harness.tools.process_runtime import (
    CapturedProcessStream, ProcessOutputCapture, start_process, stream_process_output,
)
from app.settings import repo_root, reset_settings_cache


def _capture(root: Path, *, log_bytes: int = 4096, tail_bytes: int = 128) -> ProcessOutputCapture:
    return ProcessOutputCapture(CapturedProcessStream(root / 'stdout.raw', log_bytes, tail_bytes),
                                CapturedProcessStream(root / 'stderr.raw', log_bytes, tail_bytes))


@contextmanager
def _registered_process(
    root: Path, source: str, *, timeout: float = 5, log_bytes: int = 4096, tail_bytes: int = 128,
) -> Iterator[None]:
    script = root / 'actual_process.py'
    script.write_text(source, encoding='utf-8')
    argv = [sys.executable, str(script)]
    tools = yaml.safe_load((repo_root() / 'configs/tools.yaml').read_text())
    tools['tools']['execution.simulation_runner']['command_allowlist'] = [argv]
    tools_path = root / 'tools.yaml'
    tools_path.write_text(yaml.safe_dump(tools), encoding='utf-8')
    execution_path = root / 'execution.yaml'
    execution_path.write_text(yaml.safe_dump({'execution': {
        'backend': 'local_command', 'command_timeout_seconds': timeout,
        'command_log_max_bytes': log_bytes, 'command_output_tail_bytes': tail_bytes,
        'local_commands': [{'id': 'actual-output', 'argv': argv, 'required_metrics': ['mse']}],
    }}), encoding='utf-8')
    configured = {'MARS_EXECUTION_CONFIG_PATH': str(execution_path),
                  'MARS_TOOLS_CONFIG_PATH': str(tools_path)}
    saved = {name: os.environ.get(name) for name in configured}
    os.environ.update(configured)
    reset_settings_cache()
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        reset_settings_cache()


def _job(root: Path) -> LocalCommandJob:
    return LocalCommandJob(run_id='actual-output', experiment_id='stdout-stderr', project='output-test',
                           run_root=root / 'run', config={'command_id': 'actual-output'})


def _receipt(root: Path) -> tuple[Path, dict[str, object]]:
    paths = list((root / 'run/execution/local_commands').glob('*/*/execution_receipt.json'))
    assert len(paths) == 1
    return paths[0], json.loads(paths[0].read_text())


@pytest.mark.asyncio
async def test_actual_process_drains_both_pipes_and_preserves_stdin_eof(tmp_path: Path) -> None:
    source = r"import os,sys; sys.stdin.buffer.read(); os.write(1,b'A'*2000+b'\nOUT\n'); os.write(2,b'B'*3000+b'\nERR\n')"
    process = await start_process([sys.executable, '-c', source])
    capture = _capture(tmp_path)
    stdout, stderr = await stream_process_output(process, capture=capture, timeout=5)
    assert process.returncode == 0
    assert capture.stdout.path.read_bytes() == b'A' * 2000 + b'\nOUT\n'
    assert capture.stderr.path.read_bytes() == b'B' * 3000 + b'\nERR\n'
    assert stdout == (b'A' * 2000 + b'\nOUT\n')[-128:]
    assert stderr == (b'B' * 3000 + b'\nERR\n')[-128:]
    assert capture.stdout.complete and capture.stderr.complete
    assert not capture.stdout.truncated and not capture.stderr.truncated
    if os.name == 'posix':
        assert capture.stdout.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_large_actual_output_caps_files_tails_and_peak_memory(tmp_path: Path) -> None:
    source = "import os\nfor _ in range(512):\n os.write(1,b'A'*32768)\n os.write(2,b'B'*32768)\n" + r"os.write(1,b'\nOUT\n'); os.write(2,b'\nERR\n')"
    process = await start_process([sys.executable, '-c', source])
    capture = _capture(tmp_path)
    tracemalloc.start()
    try:
        stdout, stderr = await stream_process_output(process, capture=capture, timeout=10)
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert process.returncode == 0
    # Each actual pipe emitted 16 MiB; neither accumulated in parent memory.
    assert peak_bytes < 2 * 1024 * 1024
    for stream in (capture.stdout, capture.stderr):
        assert stream.observed_bytes == 512 * 32768 + 5
        assert stream.path.stat().st_size == 4096
        assert b'log truncated' in stream.path.read_bytes()
        assert stream.truncated and stream.complete and len(stream.tail) == 128
    assert stdout.endswith(b'\nOUT\n') and stderr.endswith(b'\nERR\n')


@pytest.mark.asyncio
async def test_timeout_drains_busy_pipes_while_killing_actual_process(tmp_path: Path) -> None:
    source = "import os\nwhile True:\n os.write(1,b'A'*65536)\n os.write(2,b'B'*65536)\n"
    process = await start_process([sys.executable, '-c', source])
    capture = _capture(tmp_path)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(stream_process_output(process, capture=capture, timeout=0.2), timeout=5)
    assert process.returncode is not None
    assert capture.stdout.observed_bytes > 4096 and capture.stderr.observed_bytes > 4096
    assert capture.stdout.truncated and capture.stderr.truncated
    assert not capture.stdout.complete and not capture.stderr.complete
    assert capture.stdout.path.stat().st_size <= 4096 and capture.stderr.path.stat().st_size <= 4096


@pytest.mark.asyncio
async def test_actual_log_open_failure_still_terminates_process_with_busy_pipes(tmp_path: Path) -> None:
    source = "import os\nwhile True:\n os.write(1,b'A'*65536)\n os.write(2,b'B'*65536)\n"
    process = await start_process([sys.executable, '-c', source])
    capture = _capture(tmp_path)
    capture.stdout.path.write_text('preexisting private path', encoding='utf-8')
    with pytest.raises(FileExistsError):
        await asyncio.wait_for(stream_process_output(process, capture=capture, timeout=30), timeout=5)
    assert process.returncode is not None
    assert not capture.stdout.complete and not capture.stderr.complete
    assert len(capture.stdout.tail) <= 128 and len(capture.stderr.tail) <= 128
    assert capture.stdout.path.read_text() == 'preexisting private path'


@pytest.mark.parametrize('log_bytes,tail_bytes', [(0, 1), (1023, 1), (16777217, 1),
                                                (4096, 0), (4096, 4097), (True, 1), (4096, True)])
def test_invalid_output_capacity_rejected_without_process(log_bytes: int, tail_bytes: int, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match='process log limit'):
        _capture(tmp_path, log_bytes=log_bytes, tail_bytes=tail_bytes)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_actual_failure_keeps_redacted_logs_and_truncation_receipt(tmp_path: Path) -> None:
    source = (
        "import os\nfrom pathlib import Path\n"
        "os.write(1,b'Authorization: Basic TEST_ONLY_ENCODED_VALUE\\n'+b'X'*20000+b'\\n')\n"
        "os.write(2,b'Cookie: session=TEST_ONLY_COOKIE\\n'+b'Y'*20000+b'\\n')\n"
        "Path('unavailable_actual_measurement.json').read_text()\n"
    )
    with _registered_process(tmp_path, source, tail_bytes=1024):
        result = await run_local_command(_job(tmp_path))
    assert result.status == 'failed' and result.metrics == {} and result.loss_curve == []
    assert 'FileNotFoundError' in result.error and 'unavailable_actual_measurement.json' in result.error
    receipt_path, receipt = _receipt(tmp_path)
    assert receipt['returncode'] == 1 and receipt['status'] == 'failed'
    log_capture = receipt['log_capture']
    assert isinstance(log_capture, dict)
    assert log_capture['max_log_bytes_per_stream'] == 4096
    for name in ('stdout', 'stderr'):
        stream = log_capture[name]
        assert stream['truncated'] and stream['complete'] and stream['observed_bytes'] > 20000
        path = receipt_path.parent / f'{name}.log'
        assert path.stat().st_size <= 4096
        content = path.read_text()
        assert '[hidden]' in content and 'log truncated' in content
        assert 'TEST_ONLY_ENCODED_VALUE' not in content and 'TEST_ONLY_COOKIE' not in content
        assert stream['path'] == str(path) and stream['written_bytes'] == path.stat().st_size
    assert not any(path.suffix == '.raw' for path in (tmp_path / 'run').rglob('*'))


@pytest.mark.asyncio
async def test_actual_timeout_kills_process_and_preserves_output(tmp_path: Path) -> None:
    source = "import os,time\nos.write(1,b'actual worker started\\n')\ntime.sleep(30)\n"
    with _registered_process(tmp_path, source, timeout=0.3):
        result = await asyncio.wait_for(run_local_command(_job(tmp_path)), timeout=5)
    assert result.status == 'failed' and result.metrics == {}
    assert 'TimeoutError' in result.error and '0.3 seconds' in result.error
    receipt_path, receipt = _receipt(tmp_path)
    assert receipt['returncode'] is not None
    assert 'actual worker started' in (receipt_path.parent / 'stdout.log').read_text()
    assert isinstance(receipt['log_capture'], dict)
    assert not receipt['log_capture']['stdout']['complete']
    assert not receipt['log_capture']['stderr']['complete']


@pytest.mark.asyncio
async def test_actual_cancellation_publishes_receipt_and_keeps_tail(tmp_path: Path) -> None:
    source = (
        "import os,time\nfrom pathlib import Path\n"
        "os.write(1,b'actual worker ready\\n')\nPath('worker-ready').write_text(str(os.getpid()))\n"
        "time.sleep(30)\n"
    )
    with _registered_process(tmp_path, source, timeout=30):
        operation = asyncio.create_task(run_local_command(_job(tmp_path)))
        deadline = time.monotonic() + 5
        while not list((tmp_path / 'run').rglob('worker-ready')):
            assert time.monotonic() < deadline and not operation.done()
            await asyncio.sleep(0.01)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(operation, timeout=5)
    receipt_path, receipt = _receipt(tmp_path)
    assert receipt['status'] == 'cancelled' and receipt['error'] == 'CancelledError'
    assert receipt['returncode'] is not None
    assert 'actual worker ready' in (receipt_path.parent / 'stdout.log').read_text()
    assert isinstance(receipt['log_capture'], dict)
    assert not receipt['log_capture']['stdout']['complete']
    assert not receipt['log_capture']['stderr']['complete']


@pytest.mark.asyncio
async def test_large_single_line_credential_fragment_cannot_escape_in_error_tail(tmp_path: Path) -> None:
    source = "import os\nos.write(2,b'Authorization: Bearer '+b'A'*20000)\nos._exit(2)\n"
    with _registered_process(tmp_path, source):
        result = await run_local_command(_job(tmp_path))
    assert result.status == 'failed' and result.metrics == {}
    assert 'status 2' in result.error and 'no complete line' in result.error
    assert 'AAAA' not in result.error
    receipt_path, _ = _receipt(tmp_path)
    assert 'AAAA' not in (receipt_path.parent / 'stderr.log').read_text()
