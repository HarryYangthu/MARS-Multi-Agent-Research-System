"""Host-configured, bounded validation of a command's actual job inputs.

Validation is a real subprocess and is never started by a read-only preview.
Only the host registration can declare its no-training environment or argv.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from typing import Any, TYPE_CHECKING
import uuid

from jsonschema import validate

from app.harness.persistence import atomic_write_json, atomic_write_text, path_lock
from app.harness.tools.process_runtime import require_process_backend, sanitized_subprocess_environment

if TYPE_CHECKING:
    from app.harness.tools.execution.local_command import LocalCommandJob


def bounded_error(value: str | bytes, max_chars: int = 2000) -> str:
    """Keep useful diagnostics while removing credentials and terminal controls."""
    text = value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value
    # Redact before truncating, so a truncated credential does not escape matching.
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
    text = re.sub(r'(?im)((?:proxy-authorization|authorization|set-cookie|cookie)[\s\"\']*:\s*)[^\r\n]+',
                  r'\1[hidden]', text)
    text = re.sub(r'(?i)((?:bearer|basic)\s+)[A-Za-z0-9._~+/=-]+', r'\1[hidden]', text)
    text = re.sub(r'(?i)((?:api[_-]?key|password|passwd|secret|token|authorization|credential)'
                  r'[\w-]*[\s\"\']*[:=]\s*[\"\']?)[^\s\"\',;]+', r'\1[hidden]', text)
    text = re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@', r'\1[hidden]@', text)
    for name, secret in os.environ.items():
        if len(secret) >= 8 and any(word in name.lower() for word in
                ('password', 'secret', 'token', 'credential', 'private_key', 'api_key', 'authorization', 'cookie', 'session_key')):
            text = text.replace(secret, '[hidden]')
    text = ''.join(char for char in text if char in '\n\t' or ord(char) >= 32)
    return text.strip()[-max_chars:]


_VALIDATION_SCHEMA: dict[str, Any] = {
    'type': 'object', 'additionalProperties': False,
    'required': ['schema', 'invocation_id', 'run_id', 'experiment_id', 'status', 'checks'],
    'properties': {
        'schema': {'const': 'local_command_validation.v1'},
        'invocation_id': {'type': 'string'}, 'run_id': {'type': 'string'},
        'experiment_id': {'type': 'string'}, 'status': {'const': 'ready'},
        'checks': {'type': 'array', 'minItems': 1, 'maxItems': 100, 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['name', 'ready'],
            'properties': {'name': {'type': 'string', 'minLength': 1}, 'ready': {'const': True},
                           'details': {'type': 'object', 'properties': {
                               'input_files': {'type': 'array', 'maxItems': 256, 'items': {
                                   'type': 'object', 'required': ['path', 'sha256'],
                                   'properties': {
                                       'path': {'type': 'string', 'minLength': 1},
                                       'sha256': {'type': 'string', 'pattern': r'^(sha256:)?[0-9a-f]{64}$'},
                                       'bytes': {'type': 'integer', 'minimum': 0},
                                   },
                               }},
                           }}}}},
    },
}


def validation_protocol() -> dict[str, Any]:
    return {'result_schema': deepcopy(_VALIDATION_SCHEMA),
            'input_evidence': {
                'location': 'checks[].details.input_files',
                'required_fields': ['path', 'sha256'],
                'semantics': '每个 path 必须是本次实际读取的代码、配置或数据文件的绝对路径；'
                             'sha256 是该文件内容的实际 SHA-256，不是路径/mtime 的摘要。'
                             '宿主会独立复核路径范围、内容哈希和文件版本。'
                             'files 或顶层 input_files 不会被当作此输入证据；'
                             '没有 input_files 的旧协议检查不提供输入指纹保证。',
            },
            'instruction': '验证模式只检查本次实际解释器、代码、配置、数据与预算，不训练；'
                           '从 MARS_JOB_REQUEST 读取实际请求，向 MARS_RESULT_PATH 写验证结果。'
                           '逐项检查实际内容是否满足模型要求，不能只以文件存在宣称兼容。'
                           '用 checks[].details.input_files 提交输入证据；缓存配置/模型检查结果时，'
                           '数据、配置或相关源码变化必须使旧结论失效。'}


def validation_configuration(selected: Mapping[str, Any]) -> dict[str, Any] | None:
    raw = selected.get('validation')
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError('registered command validation must be a mapping')
    env = raw.get('environment', {})
    if (not isinstance(env, dict) or not env
            or any(not isinstance(k, str) or not isinstance(v, str) or not k or not v for k, v in env.items())):
        raise ValueError('validation must declare a host-owned no-training environment')
    if any(key in env for key in ('MARS_JOB_REQUEST', 'MARS_RESULT_PATH', 'MARS_CODE_ROOT',
                                  'MARS_PROJECT_ROOT', 'MARS_DATA_PATH', 'MARS_RUN_ID', 'MARS_PROJECT')):
        raise ValueError('validation cannot replace host-owned job or project bindings')
    if any(any(marker in key.lower() for marker in ('secret', 'password', 'token', 'credential', 'api_key')) for key in env):
        raise ValueError('validation cannot supply credentials to research commands')
    timeout = float(raw.get('timeout_seconds', 30))
    if not math.isfinite(timeout) or not 0 < timeout <= 60:
        raise ValueError('validation timeout must be between zero and 60 seconds')
    max_bytes = raw.get('max_output_bytes', 1048576)
    if type(max_bytes) is not int or not 1024 <= max_bytes <= 4194304:
        raise ValueError('validation max_output_bytes must be 1024-4194304')
    input_bytes = raw.get('max_input_bytes', 1073741824)
    if type(input_bytes) is not int or not 1024 <= input_bytes <= 8589934592:
        raise ValueError('validation max_input_bytes must be 1024-8589934592')
    argv = raw.get('argv')
    if argv is not None and (not isinstance(argv, list) or not argv
                            or any(not isinstance(part, str) or not part for part in argv)):
        raise ValueError('validation argv must be a nonempty host-owned string list')
    return {'environment': env, 'timeout_seconds': timeout, 'max_output_bytes': max_bytes, 'max_input_bytes': input_bytes,
            **({'argv': argv} if argv is not None else {})}


def _identity(job: LocalCommandJob, inputs_token: str) -> str:
    from app.harness.schema.experiment_contract import budget
    steps = budget(job.config)[1] if any(key in job.config for key in ('budget_steps', 'budget_unit', 'max_iters')) else job.steps
    payload = {'run_id': job.run_id, 'project': job.project, 'experiment_id': job.experiment_id,
               'config': {key: value for key, value in job.config.items()
                          if key not in {'confirmation_token', 'confirmation_inputs_token'}},
               'seed': job.seed, 'steps': steps,
               'command_id': job.command_id, 'inputs_token': inputs_token}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _input_signature(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {'path': str(path), 'bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
            'inode': stat.st_ino, 'device': stat.st_dev}


def _checked_roots(job: LocalCommandJob) -> tuple[Path, ...]:
    """Resolve host grants once per operation, never cache across admissions."""
    from app.harness.tools.execution.local_command import _bound_workspace
    return tuple(root.resolve() for root in _bound_workspace(job) if root is not None)


def _path_in_roots(name: str, roots: tuple[Path, ...]) -> Path:
    path = Path(name)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError('validated input must be an absolute regular file in the bound project')
    resolved = path.resolve()
    allowed = [root for root in roots if resolved.is_relative_to(root)]
    # Host validation may hash declared datasets and checkpoints; this is not
    # source-inspection permission to load their contents into an Agent context.
    def forbidden(relative: Path) -> bool:
        parts = [part.casefold() for part in relative.parts]
        return (any(part in {'.git', '.hg', '.svn', '.mars', '.ssh'} or part.startswith('.env') for part in parts)
                or relative.suffix.casefold() in {'.key', '.pem'})
    if not allowed or any(forbidden(resolved.relative_to(root)) for root in allowed):
        raise ValueError('validated input escapes the host-bound project or references control/credential files')
    return resolved


def _checked_path(job: LocalCommandJob, name: str) -> Path:
    """Standalone callers must still validate the current host binding."""
    return _path_in_roots(name, _checked_roots(job))


def known_input_changes(job: LocalCommandJob, source_token: str) -> list[dict[str, Any]]:
    """Learn paths only from a verified checker, then poll stat signatures cheaply."""
    path = job.run_root / 'execution/preflight' / (_identity(job, source_token) + '.inputs.json')
    if not path.is_file():
        return []
    if path.is_symlink():
        raise ValueError('validated input snapshot cannot be symbolic')
    raw = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(raw, dict) or raw.get('schema') != 'local_command_input_snapshot.v1'
            or raw.get('run_id') != job.run_id or raw.get('project') != job.project):
        raise ValueError('validated input snapshot identity mismatch')
    records = raw.get('input_files')
    if not isinstance(records, list) or len(records) > 256:
        raise ValueError('validated input snapshot is invalid')
    roots = _checked_roots(job) if records else ()
    changed: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get('path'), str):
            raise ValueError('validated input snapshot is invalid')
        target = _path_in_roots(record['path'], roots)
        if not target.is_file():
            changed.append({'path': str(target), 'state': 'missing'})
        else:
            signature = _input_signature(target)
            if signature != {key: record.get(key) for key in signature}:
                changed.append(signature)
    return changed


def _verified_inputs(job: LocalCommandJob, result: dict[str, Any], *, deadline: float, max_bytes: int) -> list[dict[str, Any]]:
    rows = [row for check in result['checks'] for row in check.get('details', {}).get('input_files', [])]
    if len(rows) > 256:
        raise ValueError('too many validated input files')
    roots = _checked_roots(job) if rows else ()
    records: dict[str, dict[str, Any]] = {}
    consumed = 0
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('path'), str) or not isinstance(row.get('sha256'), str):
            raise ValueError('validated input_files must include absolute paths and SHA-256')
        target = _path_in_roots(row['path'], roots)
        if not target.is_file():
            raise ValueError('validated input file is missing')
        before = _input_signature(target)
        if str(target) in records:
            if records[str(target)]['sha256'] != row['sha256'].removeprefix('sha256:'):
                raise ValueError('checker declared inconsistent input hashes')
            continue
        consumed += before['bytes']
        if consumed > max_bytes:
            raise ValueError('validation input hashing exceeded its host byte budget')
        checksum = hashlib.sha256()
        with target.open('rb') as stream:
            for block in iter(lambda: stream.read(1048576), b''):
                if time.monotonic() > deadline:
                    raise TimeoutError('validation input hashing exceeded its response time budget')
                checksum.update(block)
        if _input_signature(target) != before or checksum.hexdigest() != row['sha256'].removeprefix('sha256:'):
            raise ValueError('validated input changed or its hash does not match the checker')
        records[str(target)] = {**before, 'sha256': checksum.hexdigest()}
    return [records[name] for name in sorted(records)]


def preflight_state(job: LocalCommandJob, inputs_token: str) -> dict[str, Any]:
    from app.harness.tools.execution.local_command import _registered_command, _file_hash
    if validation_configuration(_registered_command(job)) is None:
        return {'status': 'unverified', 'configured': False}
    path = job.run_root / 'execution/preflight' / (_identity(job, inputs_token) + '.json')
    if not path.is_file() or path.is_symlink():
        return {'status': 'required', 'configured': True}
    raw = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(raw, dict) or raw.get('schema') != 'local_command_preflight.v1'
            or raw.get('run_id') != job.run_id or raw.get('project') != job.project
            or raw.get('experiment_id') != job.experiment_id or raw.get('inputs_token') != inputs_token):
        raise ValueError('command validation receipt identity mismatch')
    receipt_path = Path(str(raw.get('receipt_path', '')))
    base = (job.run_root / 'execution/preflight').resolve()
    if not receipt_path.resolve().is_relative_to(base) or receipt_path.is_symlink() or not receipt_path.is_file():
        raise ValueError('command validation receipt is unavailable')
    if _file_hash(receipt_path) != raw.get('receipt_sha256'):
        raise ValueError('command validation receipt changed')
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    if (not isinstance(receipt, dict) or receipt.get('status') != raw.get('status')
            or receipt.get('run_id') != job.run_id or receipt.get('project') != job.project
            or receipt.get('experiment_id') != job.experiment_id or receipt.get('inputs_token') != inputs_token):
        raise ValueError('command validation receipt binding mismatch')
    if receipt.get('status') == 'passed':
        for name, field in (('job.json', 'request_sha256'), ('result.json', 'result_sha256')):
            source = receipt_path.with_name(name)
            if source.is_symlink() or not source.is_file() or _file_hash(source) != receipt.get(field):
                raise ValueError('command validation request or result changed')
        records = receipt.get('input_files', [])
        roots = _checked_roots(job) if records else ()
        for record in records:
            target = _path_in_roots(record['path'], roots)
            signature = _input_signature(target) if target.is_file() else None
            if signature is None or signature != {key: record.get(key) for key in signature}:
                return {'status': 'stale', 'configured': True, 'error': 'validated input files changed',
                        'receipt_path': str(receipt_path), 'receipt_sha256': raw['receipt_sha256']}
    return {'status': raw['status'], 'configured': True, 'error': raw.get('error', ''),
            'receipt_path': str(receipt_path), 'receipt_sha256': raw['receipt_sha256'],
            'checks': receipt.get('checks', []), 'input_files': receipt.get('input_files', [])}


def _stop(process: subprocess.Popen[bytes]) -> None:
    if sys.platform == 'win32':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=10)


def _sanitize_log(path: Path, max_bytes: int) -> None:
    with path.open('rb') as stream:
        output = stream.read(max_bytes)
    safe = bounded_error(output, max_chars=max_bytes)
    deadline = time.monotonic() + 0.5
    while True:
        try:
            atomic_write_text(path, safe)
            return
        except PermissionError:
            # Windows can retain inherited log handles briefly after child exit.
            # Keep redaction mandatory; a persistent lock must still fail closed.
            if os.name != 'nt' or time.monotonic() >= deadline:
                raise
            time.sleep(0.025)


def run_command_preflight(job: LocalCommandJob, inputs_token: str, *, timeout_budget: float | None = None,
                          source_token: str | None = None) -> dict[str, Any]:
    """Run only an explicitly registered checker; legacy commands stay unverified."""
    from app.harness.tools.execution.local_command import (
        _command, _registered_command, _file_hash, command_request, command_environment,
    )
    from app.harness.tools.config import tool_config
    selected = _registered_command(job)
    policy = validation_configuration(selected)
    if policy is None:
        return {'status': 'unverified', 'configured': False}
    if timeout_budget is not None:
        if not math.isfinite(timeout_budget) or timeout_budget <= 0:
            raise ValueError('command validation response budget exhausted; training was not started')
        policy['timeout_seconds'] = min(policy['timeout_seconds'], timeout_budget)
    tool = tool_config('execution.simulation_runner')
    require_process_backend(tool.process_backend, require_isolation=tool.require_isolation)
    # Alternate validation argv still undergoes the same allowlist and checkout routing.
    argv, _, _ = _command(job, 'execution.simulation_runner', validation=True)
    invocation = uuid.uuid4().hex
    root = job.run_root.resolve() / 'execution/preflight'
    directory = root / invocation
    directory.mkdir(parents=True, exist_ok=False)
    request_path, result_path = directory / 'job.json', directory / 'result.json'
    request = command_request(job, invocation, directory)
    atomic_write_json(request_path, request)
    request_hash = _file_hash(request_path)
    environment = sanitized_subprocess_environment(overrides={
        **command_environment(job, request_path, result_path, directory), **policy['environment']})
    stdout_path, stderr_path = directory / 'stdout.log', directory / 'stderr.log'
    started = time.monotonic()
    process: subprocess.Popen[bytes] | None = None
    status, error = 'failed', ''
    result: dict[str, Any] = {}
    checked_inputs: list[dict[str, Any]] = []
    try:
        options: dict[str, Any] = ({'creationflags': getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)}
                                  if os.name == 'nt' else {'start_new_session': True})
        with stdout_path.open('wb') as stdout, stderr_path.open('wb') as stderr:
            process = subprocess.Popen(argv, cwd=directory, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=stdout, stderr=stderr, **options)
            while process.poll() is None:
                if time.monotonic() - started > policy['timeout_seconds']:
                    _stop(process)
                    raise TimeoutError('registered command validation timed out; training was not started')
                if stdout_path.stat().st_size + stderr_path.stat().st_size > policy['max_output_bytes']:
                    _stop(process)
                    raise ValueError('registered command validation exceeded its output budget')
                time.sleep(0.02)
        if stdout_path.stat().st_size + stderr_path.stat().st_size > policy['max_output_bytes']:
            raise ValueError('registered command validation exceeded its output budget')
        if process.returncode != 0:
            raise ValueError(f'validation exited with status {process.returncode}: '
                             + bounded_error(stderr_path.read_bytes()))
        if _file_hash(request_path) != request_hash:
            raise ValueError('validation modified its host-owned job request')
        if not result_path.is_file() or result_path.is_symlink() or result_path.stat().st_size > policy['max_output_bytes']:
            raise ValueError('validation did not write its bounded result JSON')
        result = json.loads(result_path.read_text(encoding='utf-8'))
        validate(instance=result, schema=_VALIDATION_SCHEMA)
        if any(result[key] != request[key] for key in ('invocation_id', 'run_id', 'experiment_id')):
            raise ValueError('validation result belongs to a different request')
        checked_inputs = _verified_inputs(job, result, deadline=started + policy['timeout_seconds'],
                                          max_bytes=policy['max_input_bytes'])
        status = 'passed'
    except Exception as exc:
        error = bounded_error(f'{type(exc).__name__}: {exc}')
    finally:
        if process is not None and process.poll() is None:
            _stop(process)
    # Public diagnostics never contain unrestricted child output.
    for path in (stdout_path, stderr_path):
        if path.is_file():
            _sanitize_log(path, policy['max_output_bytes'])
    receipt_path = directory / 'validation_receipt.json'
    atomic_write_json(receipt_path, {'schema': 'local_command_validation_receipt.v1',
        'run_id': job.run_id, 'project': job.project, 'experiment_id': job.experiment_id,
        'invocation_id': invocation, 'inputs_token': inputs_token, 'status': status, 'error': error,
        'argv': list(argv), 'pid': process.pid if process is not None else None,
        'returncode': process.returncode if process is not None else None,
        'duration_seconds': time.monotonic() - started, 'request_sha256': request_hash,
        'result_sha256': _file_hash(result_path) if result_path.is_file() and not result_path.is_symlink() else None,
        'stdout_path': str(stdout_path), 'stderr_path': str(stderr_path), 'checks': result.get('checks', []),
        'input_files': checked_inputs})
    record = {'schema': 'local_command_preflight.v1', 'run_id': job.run_id, 'project': job.project,
              'experiment_id': job.experiment_id, 'inputs_token': inputs_token, 'status': status,
              'error': error, 'receipt_path': str(receipt_path), 'receipt_sha256': _file_hash(receipt_path)}
    atomic_write_json(root / (_identity(job, inputs_token) + '.json'), record)
    if status == 'passed' and checked_inputs:
        snapshot = root / (_identity(job, source_token or inputs_token) + '.inputs.json')
        with path_lock(snapshot.with_suffix('.lock')):
            if not snapshot.exists():
                atomic_write_json(snapshot, {'schema': 'local_command_input_snapshot.v1',
                    'run_id': job.run_id, 'project': job.project, 'input_files': checked_inputs})
    return {**record, 'configured': True, 'input_files': checked_inputs}
