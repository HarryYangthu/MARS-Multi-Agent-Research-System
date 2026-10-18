"""Prepare current host-registered execution capabilities without mining history.

This is only an execution handoff candidate. A fresh real no-training checker
must verify the requested budget, seed and inputs before it becomes code_spec.
"""
from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path
import re
from typing import Any, cast

from app.bridge.coding_reuse_evidence import ReuseUnavailable, _record, _regular, _relative, _requested, _sha
from app.harness.agent_loop.trace import digest
from app.harness.tools.config import load_execution_config
from app.harness.tools.execution.command_preflight import validation_configuration
from app.harness.tools.execution.local_command import LocalCommandJob, _command, _command_files, _registered_command
from app.harness.tools.git_branch import current_git_branch, git
from app.storage.run_store import RunHandle


def registered_execution_config(project: str, requested: dict[str, Any]) -> dict[str, Any]:
    """Resolve a unique explicitly scoped host capability, never a default id."""
    request = _requested(requested)
    if set(request) != {'entrypoint', 'config_path', 'seed', 'budget_unit', 'max_iters'}:
        raise ReuseUnavailable('explicit_current_execution_request_required')
    execution = load_execution_config()['execution']
    if execution.get('backend') != 'local_command':
        raise ReuseUnavailable('registered_local_execution_required')
    rows = execution.get('local_commands', [])
    matches: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id']:
            continue
        scopes = {key: row.get(key) for key in ('project_ids', 'entrypoints', 'config_paths')}
        if any(not isinstance(value, list) or not value
               or any(not isinstance(part, str) or not part for part in value) for value in scopes.values()):
            continue  # Old unrestricted commands are not model-selection capabilities.
        declared = cast(dict[str, list[str]], scopes)
        if project not in declared['project_ids']:
            continue
        if any(not any(fnmatchcase(request[name], _relative(pattern)) for pattern in declared[field])
               for field, name in (('entrypoints', 'entrypoint'), ('config_paths', 'config_path'))):
            continue
        units = row.get('budget_units')
        if units is not None and (not isinstance(units, list) or request['budget_unit'] not in units):
            continue
        if validation_configuration(row) is None:
            continue
        matches.append(row)
    if len(matches) != 1:
        raise ReuseUnavailable('registered_execution_missing_or_ambiguous')
    return {**request, 'command_id': matches[0]['id']}


def inspect_registered_execution(run: RunHandle, requested: dict[str, Any]) -> dict[str, Any] | None:
    """Read a bounded current capability snapshot; never scan previous runs."""
    try:
        config = registered_execution_config(run.project, requested)
        branch = current_git_branch(run.project, run.run_id)
        if branch is None or not branch.source_repo_path or git(branch.root, 'status', '--porcelain'):
            raise ReuseUnavailable('current_clean_isolated_checkout_required')
        branch.validate(run.project, run.run_id)
        paths = [_regular(branch.root / config[key], branch.root) for key in ('entrypoint', 'config_path')]
        language = {'.py': 'python', '.c': 'c', '.cc': 'cpp', '.cpp': 'cpp', '.cxx': 'cpp', '.rs': 'rust'}.get(paths[0].suffix)
        if language is None:
            raise ReuseUnavailable('registered_entrypoint_language_unavailable')
        stem = re.sub(r'[^A-Za-z0-9_-]', '_', Path(config['config_path']).stem)[:50]
        name = f'existing_{stem}_{config["max_iters"]}{config["budget_unit"]}'
        job = LocalCommandJob(run.run_id, name, run.project, run.root, config=config,
                              seed=config['seed'], steps=config['max_iters'], command_id=config['command_id'])
        registration = _registered_command(job)
        argv, _, _ = _command(job, 'execution.simulation_runner')
        validation_argv, _, _ = _command(job, 'execution.simulation_runner', validation=True)
        return {'schema': 'coding.registered_execution_evidence.v1', 'target_lang': language,
            'project': run.project, 'run_id': run.run_id, 'code_workspace': str(branch.root.resolve()),
            'current_code_commit': git(branch.root, 'rev-parse', 'HEAD'),
            'code_tree_sha': git(branch.root, 'rev-parse', 'HEAD^{tree}'),
            'registered_command_sha256': digest(registration), 'argv': list(argv),
            'validation_argv': list(validation_argv),
            'command_files': _command_files(tuple(dict.fromkeys((*argv, *validation_argv)))),
            'input_files': [{'path': str(path), 'sha256': _sha(path)} for path in paths],
            'execution_jobs': [{'name': name, 'config': config}],
            'prior_check_provenance': {'source_run_id': None, 'current_test_status': 'not_rerun'},
            'fresh_preflight_required': True}
    except (OSError, ValueError, KeyError, TypeError):
        run.write_event('agent_events', {'event': 'coding.registered_execution_unavailable', 'agent': 'coding',
            'reason': 'current_registered_capability_unavailable',
            'message': '当前登记能力不能直接生成执行清单，继续常规编码准备。'})
        return None


def verified_current_preflight(run: RunHandle, evidence: dict[str, Any], checked: dict[str, Any], config: dict[str, Any]) -> bool:
    """Require actual input coverage and explicit seed/unit consumption evidence."""
    if checked.get('status') != 'passed' or not isinstance(checked.get('input_files'), list):
        return False
    inputs = checked['input_files']
    verified = {row.get('path'): row.get('sha256') for row in inputs if isinstance(row, dict)}
    for row in evidence['input_files']:
        if verified.get(row['path']) != row['sha256']:
            return False
    data_path = config.get('data_path')
    if data_path and str(Path(data_path).resolve()) not in verified:
        return False
    try:
        receipt_path = _regular(Path(checked['receipt_path']), run.root)
        if _sha(receipt_path) != str(checked['receipt_sha256']).removeprefix('sha256:'):
            return False
        receipt = _record(receipt_path, run.root)
        if (receipt.get('run_id') != run.run_id or receipt.get('project') != run.project
                or receipt.get('status') != 'passed' or receipt.get('input_files') != inputs):
            return False
    except (OSError, ValueError, KeyError, TypeError):
        return False
    checks = receipt.get('checks')
    if not isinstance(checks, list):
        return False
    for check in checks:
        details = check.get('details', {}) if isinstance(check, dict) else {}
        if (isinstance(details, dict) and details.get('training_started') is False
                and details.get('budget_unit') == config['budget_unit']
                and type(details.get('budget_value')) is int and details['budget_value'] == config['max_iters']
                and type(details.get('seed')) is int and details['seed'] == config['seed']):
            return True
    return False


def current_preflight_inputs_unchanged(checked: dict[str, Any]) -> bool:
    """Check verified input signatures after preparation without hashing data twice."""
    rows = checked.get('input_files')
    if not isinstance(rows, list) or not rows:
        return False
    try:
        for row in rows:
            path = Path(row['path'])
            stat = path.stat()
            if path.is_symlink() or not path.is_file() or any(row.get(key) != value for key, value in (
                    ('bytes', stat.st_size), ('mtime_ns', stat.st_mtime_ns),
                    ('inode', stat.st_ino), ('device', stat.st_dev))):
                return False
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return True
