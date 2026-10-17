"""Read-only, content-bound recipes for preparing an existing-model execution.

An old completed run establishes a runnable recipe, never current performance,
current test success, or approval. The caller must perform a fresh no-training
preflight before publishing a new delivery through the ordinary review path.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
from typing import Any

from jsonschema.exceptions import ValidationError as JSONValidationError

from app.harness.schema.experiment_contract import budget, coding_job_errors
from app.harness.schema.validator import validate_document
from app.harness.tools.git_branch import GitBranch, current_git_branch, git, saved_branch
from app.storage.run_store import RunHandle, RunStore


class ReuseUnavailable(ValueError):
    """A bounded reason suitable for a nonblocking fallback event."""


def _regular(path: Path, root: Path) -> Path:
    absolute = path.absolute()
    base = root.resolve()
    if not absolute.is_relative_to(base):
        raise ReuseUnavailable('evidence_outside_bound_root')
    if any(part.is_symlink() for part in (absolute, *absolute.parents) if part.is_relative_to(base)):
        raise ReuseUnavailable('symbolic_evidence')
    resolved = absolute.resolve(strict=True)
    if not resolved.is_relative_to(base) or not resolved.is_file():
        raise ReuseUnavailable('missing_regular_evidence')
    return resolved


def _sha(path: Path) -> str:
    before = path.stat()
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino, before.st_dev) != (
            after.st_size, after.st_mtime_ns, after.st_ino, after.st_dev):
        raise ReuseUnavailable('input_changed_while_hashing')
    return digest.hexdigest()


def _matches(path: Path, expected: object) -> bool:
    return isinstance(expected, str) and _sha(path) == expected.removeprefix('sha256:')


def _record(path: Path, root: Path) -> dict[str, Any]:
    checked = _regular(path, root)
    if checked.stat().st_size > 2 * 1024 * 1024:
        raise ReuseUnavailable('oversized_receipt')
    raw = json.loads(checked.read_text(encoding='utf-8'))
    if not isinstance(raw, dict):
        raise ReuseUnavailable('invalid_receipt')
    return raw


def _relative(value: object) -> str:
    if (not isinstance(value, str) or not value or value != value.strip()
            or any(c in value for c in '\r\n\0') or PureWindowsPath(value).drive):
        raise ReuseUnavailable('explicit_repository_paths_required')
    path = Path(value.replace('\\', '/'))
    if path.is_absolute() or '..' in path.parts or any(part.casefold() == '.git' for part in path.parts):
        raise ReuseUnavailable('explicit_repository_paths_required')
    return path.as_posix()


def _requested(raw: dict[str, Any]) -> dict[str, Any]:
    required = {'entrypoint', 'config_path', 'seed', 'budget_unit', 'max_iters'}
    if not required.issubset(raw) or set(raw) - required - {'source_run_id'}:
        raise ReuseUnavailable('explicit_execution_request_required')
    result = dict(raw)
    result['entrypoint'] = _relative(raw['entrypoint'])
    result['config_path'] = _relative(raw['config_path'])
    if type(raw['seed']) is not int or raw['seed'] < 0:
        raise ReuseUnavailable('explicit_seed_required')
    budget(result)
    if 'source_run_id' in result and (not isinstance(result['source_run_id'], str)
            or not re.fullmatch(r'[A-Za-z0-9_.-]+', result['source_run_id'])):
        raise ReuseUnavailable('invalid_source_identity')
    return result


def _approved_recipe(source: RunHandle, requested: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], Path]:
    approved = _regular(source.root / 'coding/code_spec.approved.md', source.root)
    if approved.stat().st_size > 2 * 1024 * 1024:
        raise ReuseUnavailable('oversized_code_delivery')
    validation = validate_document(approved.read_text(encoding='utf-8'), expected_schema='code_spec.v1')
    metadata = validation.metadata
    if (not validation.valid or metadata.get('project') != source.project
            or metadata.get('files_changed') != [] or metadata.get('new_dependencies', []) != []
            or metadata.get('baseline_compat', {}).get('preserved') is not True
            or metadata.get('coding_backend') or coding_job_errors(metadata, project=source.project)):
        raise ReuseUnavailable('source_is_not_zero_change_delivery')
    jobs = metadata['execution_jobs']
    if len(jobs) != 1:
        raise ReuseUnavailable('single_existing_model_recipe_required')
    recipe: dict[str, Any] = jobs[0]
    config = recipe['config']
    if set(config) - {'command_id', 'entrypoint', 'config_path', 'seed', 'budget_unit', 'max_iters', 'budget_steps',
                      'data_path', 'data_source_id', 'fs_mhz', 'channel_count'}:
        raise ReuseUnavailable('source_recipe_contains_unrequested_overrides')
    if set(config).intersection({'confirmation_token', 'confirmation_inputs_token', 'output_dir', 'run_id',
            'invocation_id', 'experiment_id', 'code_workspace', 'project_workspace', 'code_commit', 'run_root'}):
        raise ReuseUnavailable('source_recipe_contains_attempt_bindings')
    if (config.get('entrypoint') != requested['entrypoint'] or config.get('config_path') != requested['config_path']
            or not isinstance(config.get('command_id'), str) or not config['command_id']):
        raise ReuseUnavailable('recipe_config_does_not_match')
    paths = sorted((source.root / 'coding/.approvals/code_spec').glob('*.json'))
    if not paths:
        raise ReuseUnavailable('source_approval_missing')
    approval = _record(paths[-1], source.root)
    version = approval.get('source_version')
    if (approval.get('schema') != 'artifact_approval.v1' or approval.get('run_id') != source.run_id
            or approval.get('agent_dir') != 'coding' or approval.get('stem') != 'code_spec'
            or not isinstance(version, str) or not re.fullmatch(r'v[1-9]\d*', version)):
        raise ReuseUnavailable('source_approval_identity_changed')
    versioned = _regular(source.root / 'coding' / ('code_spec.' + version + '.md'), source.root)
    if not _matches(approved, approval.get('source_sha256')) or not _matches(versioned, approval.get('source_sha256')):
        raise ReuseUnavailable('source_approval_content_changed')
    return metadata, recipe, approved


def _completed_receipt(source: RunHandle, recipe: dict[str, Any]) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    paths = sorted((source.root / 'execution/local_commands').glob('*/*/execution_receipt.json'), reverse=True)
    if len(paths) > 128:
        raise ReuseUnavailable('source_receipt_scan_limit')
    matches: list[tuple[Path, dict[str, Any], dict[str, Any]]] = []
    for path in paths:
        receipt = _record(path, source.root)
        if (receipt.get('schema') != 'local_command_receipt.v1' or receipt.get('status') != 'completed'
                or type(receipt.get('returncode')) is not int or receipt['returncode'] != 0
                or receipt.get('run_id') != source.run_id or receipt.get('experiment_id') != recipe['name']):
            continue
        request_path = _regular(path.with_name('job.json'), source.root)
        result_path = _regular(path.with_name('result.json'), source.root)
        if not _matches(request_path, receipt.get('request_sha256')) or not _matches(result_path, receipt.get('result_sha256')):
            raise ReuseUnavailable('source_execution_content_changed')
        request = _record(request_path, source.root)
        if (request.get('schema') != 'local_command_request.v1' or request.get('project') != source.project
                or request.get('output_dir') != str(path.parent.resolve())
                or any(request.get(key) != receipt.get(key) for key in ('invocation_id', 'run_id', 'experiment_id'))
                or not isinstance(request.get('config'), dict)
                or any(request['config'].get(key) != value for key, value in recipe['config'].items())):
            raise ReuseUnavailable('source_execution_identity_changed')
        from app.harness.tools.execution.local_command import _read_result
        _, _, artifacts = _read_result(result_path, request, ())
        if artifacts != receipt.get('evidence'):
            raise ReuseUnavailable('source_execution_evidence_changed')
        for artifact in artifacts:
            _regular(Path(artifact['path']), path.parent)
        matches.append((path, receipt, request))
    if len(matches) != 1:
        raise ReuseUnavailable('source_completed_execution_missing_or_ambiguous')
    return matches[0]


def _verified_preflight(source: RunHandle, recipe: dict[str, Any], request: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    matches: list[tuple[Path, dict[str, Any]]] = []
    paths = sorted((source.root / 'execution/preflight').glob('*/validation_receipt.json'))
    if len(paths) > 128:
        raise ReuseUnavailable('source_preflight_scan_limit')
    for path in paths:
        record = _record(path, source.root)
        if (record.get('schema') != 'local_command_validation_receipt.v1' or record.get('status') != 'passed'
                or record.get('returncode') != 0 or record.get('run_id') != source.run_id
                or record.get('project') != source.project or record.get('experiment_id') != recipe['name']):
            continue
        job_path = _regular(path.with_name('job.json'), source.root)
        result_path = _regular(path.with_name('result.json'), source.root)
        if not _matches(job_path, record.get('request_sha256')) or not _matches(result_path, record.get('result_sha256')):
            raise ReuseUnavailable('source_preflight_content_changed')
        job = _record(job_path, source.root)
        if (job.get('schema') != 'local_command_request.v1'
                or any(job.get(key) != record.get(key) for key in ('invocation_id', 'run_id', 'experiment_id'))
                or job.get('code_workspace') != request.get('code_workspace') or job.get('code_commit') != request.get('code_commit')
                or any(job.get(key) != request.get(key) for key in ('project', 'seed', 'steps'))
                or not isinstance(job.get('config'), dict)
                or any(job['config'].get(key) != value for key, value in recipe['config'].items())):
            continue
        result = _record(result_path, source.root)
        if (result.get('schema') != 'local_command_validation.v1' or result.get('status') != 'ready'
                or any(result.get(key) != record.get(key) for key in ('invocation_id', 'run_id', 'experiment_id'))
                or not isinstance(result.get('checks'), list) or not result['checks']
                or result['checks'] != record.get('checks')
                or any(not isinstance(row, dict) or row.get('ready') is not True for row in result['checks'])):
            raise ReuseUnavailable('source_preflight_identity_changed')
        if not isinstance(record.get('input_files'), list) or not record['input_files']:
            continue
        declared = [row for check in result['checks'] for row in check.get('details', {}).get('input_files', [])]
        if (any(not isinstance(row, dict) or not isinstance(row.get('path'), str)
                or not isinstance(row.get('sha256'), str) for row in declared)
                or {row['path']: row['sha256'].removeprefix('sha256:') for row in declared}
                != {row.get('path'): str(row.get('sha256', '')).removeprefix('sha256:')
                    for row in record['input_files'] if isinstance(row, dict)}):
            raise ReuseUnavailable('source_preflight_inputs_changed')
        matches.append((path, record))
    if not matches:
        raise ReuseUnavailable('source_verified_inputs_missing')
    # Preview and admission validation may legitimately have identical inputs.
    fingerprints = {json.dumps([{key: row.get(key) for key in ('path', 'sha256')}
                               for row in record['input_files']], sort_keys=True) for _, record in matches}
    if len(fingerprints) != 1:
        raise ReuseUnavailable('source_validated_inputs_ambiguous')
    return matches[-1]


def _match_inputs(source_root: Path, current_root: Path, source_project_root: Path,
                  current_project_root: Path, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(records) > 256:
        raise ReuseUnavailable('source_input_limit')
    matched: list[dict[str, Any]] = []
    names: set[str] = set()
    for row in records:
        if not isinstance(row, dict) or not isinstance(row.get('path'), str):
            raise ReuseUnavailable('source_input_record_invalid')
        old = Path(row['path'])
        if old.is_relative_to(source_root):
            current = current_root / old.relative_to(source_root)
            old = _regular(old, source_root)
            current = _regular(current, current_root)
        elif old.is_relative_to(source_project_root):
            current = current_project_root / old.relative_to(source_project_root)
            old = _regular(old, source_project_root)
            current = _regular(current, current_project_root)
        else:
            raise ReuseUnavailable('source_input_outside_project')
        expected = row.get('sha256')
        old_hash = _sha(old)
        current_hash = old_hash if current == old else _sha(current)
        if (str(current) in names or not isinstance(expected, str)
                or old_hash != expected.removeprefix('sha256:') or current_hash != expected.removeprefix('sha256:')):
            raise ReuseUnavailable('source_or_current_input_changed')
        names.add(str(current))
        matched.append({'path': str(current), 'source_path': str(old), 'sha256': current_hash})
    return matched


def _source_evidence(run: RunHandle, source: RunHandle, requested: dict[str, Any], branch: GitBranch) -> dict[str, Any]:
    if source.project != run.project or source.run_id == run.run_id or source.root.resolve() == run.root.resolve():
        raise ReuseUnavailable('source_project_or_identity_mismatch')
    metadata, recipe, approved = _approved_recipe(source, requested)
    path, receipt, request = _completed_receipt(source, recipe)
    old_branch = saved_branch(branch.source_root, source.root, source.project, source.run_id)
    if old_branch is None or not old_branch.source_repo_path or old_branch.source_root != branch.source_root:
        raise ReuseUnavailable('source_checkout_binding_missing')
    old_root = old_branch.root.resolve()
    if (request.get('code_workspace') != str(old_root) or git(old_root, 'status', '--porcelain')
            or not isinstance(request.get('code_commit'), str)
            or not re.fullmatch(r'[0-9a-f]{40,64}', request['code_commit'])):
        raise ReuseUnavailable('source_checkout_changed')
    old_commit = request['code_commit']
    source_tree = git(old_root, 'rev-parse', old_commit + '^{tree}')
    current_tree = git(branch.root, 'rev-parse', 'HEAD^{tree}')
    if source_tree != current_tree or source_tree != git(old_root, 'rev-parse', 'HEAD^{tree}'):
        raise ReuseUnavailable('code_content_changed')
    preflight_path, preflight = _verified_preflight(source, recipe, request)
    config = deepcopy(recipe['config'])
    data_fields = {'data_path': 'stored_path', 'data_source_id': 'id',
                   'fs_mhz': 'fs_mhz', 'channel_count': 'channel_count'}
    if set(config).intersection(data_fields):
        selection = _record(run.root / 'input/selected_data_source.json', run.root)
        for name, selection_name in data_fields.items():
            if name not in config:
                continue
            chosen = selection.get(selection_name)
            if name == 'data_path':
                if not isinstance(config[name], str) or not isinstance(chosen, str):
                    raise ReuseUnavailable('current_data_override_not_explicit')
                original_path, current_path = Path(config[name]), Path(chosen)
                if not original_path.is_absolute():
                    original_path = old_root / original_path
                if not current_path.is_absolute():
                    current_path = branch.root / current_path
                if original_path.resolve() != current_path.resolve():
                    raise ReuseUnavailable('current_data_override_does_not_match')
                config[name] = str(current_path.resolve())
            elif chosen is None or chosen != config[name]:
                raise ReuseUnavailable('current_data_override_does_not_match')
    config.update({key: requested[key] for key in ('seed', 'max_iters', 'budget_unit')})
    if 'budget_steps' in config:
        if requested['budget_unit'] != 'steps':
            raise ReuseUnavailable('budget_unit_changed')
        config['budget_steps'] = requested['max_iters']
    from app.harness.tools.execution.local_command import LocalCommandJob, _bound_workspace, _command, _registered_command
    job = LocalCommandJob(run.run_id, 'existing_model_reuse', run.project, run.root, config=config,
                          seed=requested['seed'], steps=requested['max_iters'], command_id=config['command_id'])
    registered = _registered_command(job)
    from app.harness.tools.execution.command_preflight import validation_configuration
    if validation_configuration(registered) is None:
        raise ReuseUnavailable('fresh_no_training_validation_unavailable')
    argv, _, _ = _command(job, 'execution.simulation_runner')
    historical_argv = receipt.get('argv')
    if not isinstance(historical_argv, list) or any(not isinstance(part, str) for part in historical_argv):
        raise ReuseUnavailable('source_command_evidence_invalid')
    rebound_argv = [part.replace(str(old_root), str(branch.root.resolve())) for part in historical_argv]
    if list(argv) != rebound_argv or preflight.get('argv') != historical_argv:
        raise ReuseUnavailable('registered_command_changed')
    command_files = receipt.get('command_files')
    if not isinstance(command_files, list) or not command_files:
        raise ReuseUnavailable('source_interpreter_evidence_missing')
    for row in command_files:
        if not isinstance(row, dict) or not isinstance(row.get('path'), str):
            raise ReuseUnavailable('source_interpreter_evidence_invalid')
        interpreter = Path(row['path'])
        if not interpreter.is_file() or not _matches(interpreter, row.get('sha256')):
            raise ReuseUnavailable('execution_interpreter_changed')
    executable = Path(argv[0]).resolve(strict=True)
    if not any(Path(row['path']).resolve() == executable and _matches(executable, row.get('sha256')) for row in command_files):
        raise ReuseUnavailable('execution_interpreter_not_bound')
    _, project_path = _bound_workspace(job)
    if project_path is None or not isinstance(request.get('project_workspace'), str):
        raise ReuseUnavailable('project_workspace_missing')
    if Path(request['project_workspace']).resolve() != project_path.resolve():
        raise ReuseUnavailable('source_project_workspace_changed')
    inputs = _match_inputs(old_root, branch.root.resolve(), Path(request['project_workspace']).resolve(), project_path,
                           preflight['input_files'])
    for relative in (requested['entrypoint'], requested['config_path']):
        if str((branch.root / relative).resolve()) not in {row['path'] for row in inputs}:
            raise ReuseUnavailable('recipe_inputs_not_verified')
    # A current selected dataset is explicit input. Never silently reuse the old
    # dataset when the user has selected another one with different content.
    selected = run.root / 'input/selected_data_source.json'
    if selected.exists() or selected.is_symlink():
        raw = _record(selected, run.root)
        if not isinstance(raw.get('stored_path'), str):
            raise ReuseUnavailable('current_data_selection_invalid')
        data = Path(raw['stored_path'])
        if not data.is_absolute():
            data = branch.root / data
        from app.harness.tools.execution.command_preflight import _checked_path
        data = _checked_path(job, str(data))
        if not any(row['path'] == str(data) and _matches(data, row['sha256']) for row in inputs):
            raise ReuseUnavailable('selected_dataset_changed')
    name = re.sub(r'[^A-Za-z0-9_-]', '_', Path(requested['config_path']).stem)[:60]
    command_hash = hashlib.sha256(json.dumps(registered, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'schema': 'coding.execution_reuse_evidence.v1', 'source_run_id': source.run_id,
        'target_lang': metadata['target_lang'],
        'source_code_spec_ref': approved.relative_to(source.root).as_posix(), 'source_code_spec_path': str(approved),
        'source_code_spec_sha256': _sha(approved), 'source_receipt_ref': path.relative_to(source.root).as_posix(),
        'source_receipt_path': str(path), 'source_receipt_sha256': _sha(path),
        'source_preflight_ref': preflight_path.relative_to(source.root).as_posix(),
        'source_preflight_path': str(preflight_path), 'source_preflight_sha256': _sha(preflight_path),
        'source_code_commit': old_commit, 'current_code_commit': git(branch.root, 'rev-parse', 'HEAD'),
        'code_tree_sha': current_tree, 'registered_command_sha256': command_hash,
        'execution_jobs': [{'name': f'reuse_{name}_{requested["max_iters"]}{requested["budget_unit"]}', 'config': config}],
        'input_files': inputs,
        'prior_check_provenance': {'source_code_spec_ref': approved.relative_to(source.root).as_posix(),
            'declared_test_coverage': metadata.get('test_coverage', {}), 'current_test_status': 'not_rerun'},
        'environment': {'interpreter_path': str(executable), 'interpreter_sha256': _sha(executable),
                        'packages_verified': False, 'fresh_preflight_required': True}}


def inspect_reusable_execution(run: RunHandle, requested: dict[str, Any]) -> dict[str, Any] | None:
    """Return verified recipe evidence or quietly fall back to ordinary Coding."""
    reason = 'no_matching_verified_recipe'
    try:
        request = _requested(requested)
        branch = current_git_branch(run.project, run.run_id)
        if branch is None or not branch.source_repo_path or git(branch.root, 'status', '--porcelain'):
            raise ReuseUnavailable('current_clean_isolated_checkout_required')
        store = RunStore()
        explicit = request.get('source_run_id')
        source = store.get(explicit) if isinstance(explicit, str) else None
        candidates = ([source] if source is not None else [] if explicit else
                      sorted((item for item in store.list() if item.project == run.project and item.run_id != run.run_id),
                             key=lambda item: (item.created_at, item.run_id), reverse=True)[:128])
        found: list[dict[str, Any]] = []
        for candidate in candidates:
            try:
                found.append(_source_evidence(run, candidate, request, branch))
            except ReuseUnavailable as exc:
                reason = str(exc)
            except (OSError, ValueError, KeyError, TypeError, JSONValidationError):
                reason = 'source_evidence_unavailable'
        if found:
            identities = {json.dumps(item['execution_jobs'][0]['config'], sort_keys=True) for item in found}
            if len(identities) == 1:
                return found[0]
            reason = 'verified_recipes_ambiguous'
    except ReuseUnavailable as exc:
        reason = str(exc)
    except (OSError, ValueError, KeyError, TypeError, JSONValidationError):
        reason = 'current_reuse_evidence_unavailable'
    try:
        run.write_event('agent_events', {'event': 'coding.execution_reuse_unavailable', 'agent': 'coding',
                        'reason': reason, 'message': '现有运行交付暂不可直接复用，继续常规编码准备。'})
    except OSError:
        pass  # A best-effort optimization diagnostic cannot block normal Coding.
    return None
