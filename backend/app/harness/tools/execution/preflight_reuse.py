"""Same-process, same-task reuse of genuine no-training validation receipts.

An input fingerprint is not approval. New confirmation tokens get their own
host derivation record, while the actual subprocess receipt stays immutable.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, TYPE_CHECKING
import uuid

from app.harness.persistence import atomic_write_json
from app.harness.tools.process_runtime import sanitized_subprocess_environment

if TYPE_CHECKING:
    from app.harness.tools.execution.local_command import LocalCommandJob

_HOST_SESSION = uuid.uuid4().hex


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _task_path(path: Path, root: Path) -> Path:
    base = root.resolve()
    if (not path.is_absolute() or not path.resolve().is_relative_to(base)
            or any(part.is_symlink() for part in (path, *path.parents) if part.is_relative_to(base))):
        raise ValueError('preflight reuse evidence escapes its task or references a symlink')
    return path


def _record(path: Path, root: Path) -> dict[str, Any]:
    _task_path(path, root)
    if not path.is_file() or path.stat().st_size > 2097152:
        raise ValueError('preflight reuse evidence is not a bounded task-owned regular file')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('preflight reuse evidence must be an object')
    return value


def _request_identity(request: dict[str, Any]) -> dict[str, Any]:
    result = {key: value for key, value in request.items() if key not in {'invocation_id', 'output_dir'}}
    config = request['config']
    result['config'] = {key: value for key, value in config.items()
                        if key not in {'confirmation_token', 'confirmation_inputs_token'}}
    return result


def preflight_fingerprint(job: LocalCommandJob) -> str | None:
    """Rebuild the current host identity, without reading a repository tree."""
    from app.harness.tools.config import tool_config
    from app.harness.tools.execution.local_command import _command, _command_files, _registered_command, command_request
    attempt = job.config.get('attempt')
    if type(attempt) is not int or attempt < 1:
        return None  # Legacy callers/receipts never become reusable implicitly.
    argv, _, _ = _command(job, 'execution.simulation_runner')
    validation_argv, _, _ = _command(job, 'execution.simulation_runner', validation=True)
    request = command_request(job, 'host-fingerprint', job.run_root / 'execution/preflight/.identity')
    if request.get('code_workspace') and (Path(request['code_workspace']) / '.git').exists():
        from app.harness.tools.git_branch import git
        if git(Path(request['code_workspace']), 'status', '--porcelain'):
            return None  # Includes unlisted/new source files without hashing a tree.
    environment = sanitized_subprocess_environment()
    # These variables are always replaced by command_environment for this job;
    # their actual bindings are in request or deterministic output locations.
    for key in ('MARS_RUN_ID', 'MARS_EXPERIMENT_ID', 'MARS_PROJECT', 'MARS_RUN_ROOT', 'MARS_JOB_REQUEST',
                'MARS_RESULT_PATH', 'MARS_TENSORBOARD_ENABLED', 'MARS_VISUALIZATIONS_DIR'):
        environment.pop(key, None)
    for field, key in (('code_workspace', 'MARS_CODE_ROOT'), ('project_workspace', 'MARS_PROJECT_ROOT'),
                       ('data_binding', 'MARS_DATA_PATH')):
        if request.get(field):
            environment.pop(key, None)
    return _digest({'schema': 'local_command_preflight_identity.v1',
        'run_root': str(job.run_root.resolve()), 'request': _request_identity(request),
        'command_id': job.command_id, 'registration': _registered_command(job),
        'argv': argv, 'validation_argv': validation_argv,
        'command_files': _command_files(tuple(dict.fromkeys((*argv, *validation_argv)))),
        'process_policy': asdict(tool_config('execution.simulation_runner')),
        'environment_sha256': _digest(environment)})


def input_records_unchanged(records: Any) -> bool:
    """Check new POSIX signatures cheaply; Windows and old records rehash bytes."""
    from app.harness.tools.execution.command_preflight import _input_signature
    if not isinstance(records, list) or not records or len(records) > 256:
        return False
    try:
        for row in records:
            if not isinstance(row, dict) or not isinstance(row.get('path'), str):
                return False
            path = Path(row['path'])
            if path.is_symlink() or not path.is_file():
                return False
            before = _input_signature(path)
            if any(row.get(key) != value for key, value in before.items() if key != 'ctime_ns'):
                return False
            if 'ctime_ns' in row and row['ctime_ns'] != before['ctime_ns']:
                return False
            if os.name == 'nt' or 'ctime_ns' not in row:
                expected = str(row.get('sha256', '')).removeprefix('sha256:')
                if re.fullmatch(r'[0-9a-f]{64}', expected) is None:
                    return False
                with path.open('rb') as stream:
                    actual = hashlib.file_digest(stream, 'sha256').hexdigest()
                if actual != expected or _input_signature(path) != before:
                    return False
    except (OSError, ValueError, TypeError, KeyError):
        return False
    return True


def _index(job: LocalCommandJob, fingerprint: str) -> Path:
    return _task_path(job.run_root.resolve() / 'execution/preflight/reuse_index' / (fingerprint + '.json'), job.run_root)


def remember_preflight(job: LocalCommandJob, fingerprint: str | None, record: dict[str, Any]) -> None:
    if fingerprint is not None and record.get('status') == 'passed':
        atomic_write_json(_index(job, fingerprint), {'schema': 'local_command_preflight_reuse.v1',
            'run_id': job.run_id, 'project': job.project, 'experiment_id': job.experiment_id,
            'fingerprint': fingerprint, 'host_pid': os.getpid(), 'host_session': _HOST_SESSION,
            'inputs_token': record['inputs_token'],
            'receipt_path': record['receipt_path'], 'receipt_sha256': record['receipt_sha256']})


def derived_source_token(job: LocalCommandJob, raw: dict[str, Any], receipt: dict[str, Any],
                         fingerprint: str | None) -> str:
    """Check a token derivation separately from the original process identity."""
    from app.harness.tools.execution.local_command import _file_hash
    if 'derivation_path' not in raw:
        return str(raw['inputs_token'])
    path = Path(str(raw['derivation_path']))
    binding = _record(path, job.run_root)
    if (_file_hash(path) != raw.get('derivation_sha256')
            or binding.get('schema') != 'local_command_preflight_derivation.v1'
            or binding.get('checker_started') is not False
            or fingerprint is None or binding.get('fingerprint') != fingerprint
            or any(binding.get(key) != value for key, value in {
                'run_id': job.run_id, 'project': job.project, 'experiment_id': job.experiment_id,
                'inputs_token': raw['inputs_token'], 'source_receipt_path': raw['receipt_path'],
                'source_receipt_sha256': raw['receipt_sha256'],
                'source_inputs_token': receipt.get('inputs_token'), 'attempt': job.config['attempt']}.items())):
        raise ValueError('preflight token derivation identity or evidence changed')
    return str(binding['source_inputs_token'])


def reuse_preflight(job: LocalCommandJob, inputs_token: str, fingerprint: str | None) -> dict[str, Any] | None:
    """Locate one exact current-run receipt; never scan history or run a checker."""
    from app.harness.tools.execution.command_preflight import _identity, preflight_state
    from app.harness.tools.execution.local_command import _file_hash
    if fingerprint is None:
        return None
    index = _index(job, fingerprint)
    if not index.exists():
        return None
    raw = _record(index, job.run_root)
    if raw.get('host_pid') != os.getpid() or raw.get('host_session') != _HOST_SESSION:
        return None  # A cold host keeps old confirmations readable but really revalidates.
    if (raw.get('schema') != 'local_command_preflight_reuse.v1'
            or any(raw.get(key) != value for key, value in {
                'run_id': job.run_id, 'project': job.project, 'experiment_id': job.experiment_id,
                'fingerprint': fingerprint}.items())
            or not isinstance(raw.get('inputs_token'), str)):
        raise ValueError('preflight reuse index identity mismatch')
    checked = preflight_state(job, raw['inputs_token'])
    if checked['status'] != 'passed':
        return None
    if (checked.get('receipt_path') != raw.get('receipt_path')
            or checked.get('receipt_sha256') != raw.get('receipt_sha256')):
        raise ValueError('preflight reuse index source changed')
    source = _record(Path(checked['receipt_path']), job.run_root)
    if (source.get('reuse_fingerprint') != fingerprint or type(source.get('returncode')) is not int
            or source['returncode'] != 0 or type(source.get('pid')) is not int or source['pid'] <= 0
            or not input_records_unchanged(checked.get('input_files'))):
        return None
    record = {'schema': 'local_command_preflight.v1', 'run_id': job.run_id, 'project': job.project,
        'experiment_id': job.experiment_id, 'inputs_token': inputs_token, 'status': 'passed', 'error': '',
        'receipt_path': checked['receipt_path'], 'receipt_sha256': checked['receipt_sha256']}
    if inputs_token != raw['inputs_token']:
        path = _task_path(job.run_root.resolve() / 'execution/preflight/derivations' / (uuid.uuid4().hex + '.json'), job.run_root)
        atomic_write_json(path, {'schema': 'local_command_preflight_derivation.v1',
            'run_id': job.run_id, 'project': job.project, 'experiment_id': job.experiment_id,
            'attempt': job.config['attempt'], 'inputs_token': inputs_token, 'fingerprint': fingerprint,
            'source_inputs_token': source['inputs_token'], 'source_receipt_path': checked['receipt_path'],
            'source_receipt_sha256': checked['receipt_sha256'], 'checker_started': False})
        record.update(derivation_path=str(path), derivation_sha256=_file_hash(path))
        atomic_write_json(_task_path(job.run_root / 'execution/preflight' / (_identity(job, inputs_token) + '.json'), job.run_root), record)
    return {**checked, **record, 'configured': True, 'reused': True}
