"""Expose only identity- and content-bound host preparation evidence to review."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from app.bridge.coding_reuse_evidence import _record, _regular, _requested, _sha
from app.bridge.registered_execution_preparation import current_preflight_inputs_unchanged
from app.harness.agent_loop.trace import digest
from app.harness.tools.execution.local_command import LocalCommandJob, _bound_workspace, _command, _command_files, _registered_command
from app.harness.tools.git_branch import git
from app.storage.run_store import RunHandle


def _receipt_ref(value: Any, pattern: str) -> str:
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise ValueError('执行准备证据引用不是本次任务的受支持收据。')
    return value


def _matches_file(path: Path, expected: Any, run: RunHandle) -> dict[str, Any]:
    checked = _regular(path, run.root)
    record = _record(checked, run.root)
    if (not isinstance(expected, str)
            or _sha(checked) != expected.removeprefix('sha256:')):
        raise ValueError('执行准备证据内容校验未通过。')
    return record


def coding_preparation_review_evidence(run: RunHandle, metadata: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Validate actual host receipts before adding their exact refs to review.

    No metadata path becomes readable solely because the model mentioned it.
    Missing/changed claimed evidence blocks review; ordinary code delivery with
    no host preparation metadata keeps its existing review behavior.
    """
    if 'execution_reuse' not in metadata:
        return {}
    declared = metadata['execution_reuse']
    if (not isinstance(declared, dict) or metadata.get('project') != run.project
            or metadata.get('files_changed') != [] or declared.get('model_calls') != 0
            or type(declared.get('model_calls')) is not int):
        raise ValueError('执行准备声明与当前代码产物不一致。')
    ref = _receipt_ref(declared.get('receipt_ref'), r'coding/reuse/[0-9a-f]{32}\.json')
    receipt = _record(run.root / ref, run.root)
    if (receipt.get('schema') != 'coding.execution_reuse.v1' or receipt.get('run_id') != run.run_id
            or receipt.get('project') != run.project or receipt.get('invocation_id') != Path(ref).stem
            or receipt.get('mode') != declared.get('mode')
            or receipt.get('mode') not in {'host_validated_registered_execution', 'host_verified_existing_execution'}
            or type(receipt.get('model_calls')) is not int or receipt['model_calls'] != 0
            or receipt.get('training_started') is not False or receipt.get('approved') is not False
            or receipt.get('files_changed') != []):
        raise ValueError('执行准备收据不属于当前系统交付。')
    binding = receipt.get('current_preflight')
    if not isinstance(binding, dict) or binding.get('status') != 'passed':
        raise ValueError('执行准备缺少本次真实预检绑定。')
    validation_ref = _receipt_ref(binding.get('ref'), r'execution/preflight/[0-9a-f]{32}/validation_receipt\.json')
    if declared.get('current_preflight_ref') != validation_ref:
        raise ValueError('执行准备产物引用了不同的预检。')
    validation_path = run.root / validation_ref
    validation = _matches_file(validation_path, binding.get('sha256'), run)
    invocation = Path(validation_ref).parent.name
    if (validation.get('schema') != 'local_command_validation_receipt.v1'
            or validation.get('run_id') != run.run_id or validation.get('project') != run.project
            or validation.get('invocation_id') != invocation or validation.get('status') != 'passed'
            or type(validation.get('returncode')) is not int or validation['returncode'] != 0
            or type(validation.get('pid')) is not int or validation['pid'] <= 0
            or not isinstance(validation.get('input_files'), list) or not 1 <= len(validation['input_files']) <= 256
            or validation.get('input_files') != binding.get('input_files')
            or not current_preflight_inputs_unchanged(validation)):
        raise ValueError('本次预检身份、进程或实际输入证据已变化。')
    request = _matches_file(validation_path.with_name('job.json'), validation.get('request_sha256'), run)
    result = _matches_file(validation_path.with_name('result.json'), validation.get('result_sha256'), run)
    jobs = metadata.get('execution_jobs')
    if not isinstance(jobs, list) or len(jobs) != 1 or not isinstance(jobs[0], dict):
        raise ValueError('执行准备必须对应当前唯一作业。')
    job = jobs[0]
    config = job.get('config')
    requested = receipt.get('requested')
    source = receipt.get('source')
    if (not isinstance(config, dict) or not isinstance(requested, dict) or not isinstance(source, dict)
            or source.get('target_lang') != metadata.get('target_lang')
            or any(config.get(key) != requested.get(key)
                   for key in ('entrypoint', 'config_path', 'max_iters', 'budget_unit', 'seed'))):
        raise ValueError('本次执行请求与代码产物不一致。')
    _requested(requested)
    identities = {'invocation_id': invocation, 'run_id': run.run_id, 'experiment_id': job.get('name')}
    if (request.get('schema') != 'local_command_request.v1'
            or request.get('project') != run.project or request.get('config') != config
            or request.get('seed') != config.get('seed') or request.get('steps') != config.get('max_iters')
            or request.get('output_dir') != str(validation_path.parent.resolve())
            or result.get('schema') != 'local_command_validation.v1' or result.get('status') != 'ready'
            or result.get('checks') != validation.get('checks')
            or any(record.get(key) != value for record in (request, result, validation)
                   for key, value in identities.items())):
        raise ValueError('真实预检请求、结果与当前作业不一致。')
    current = LocalCommandJob(run.run_id, str(job['name']), run.project, run.root, config=config,
        seed=config['seed'], steps=config['max_iters'], command_id=config['command_id'])
    code_root, project_root = _bound_workspace(current)
    registration = _registered_command(current)
    registration_hash = (hashlib.sha256(json.dumps(registration, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if receipt['mode'] == 'host_verified_existing_execution' else digest(registration))
    if (code_root is None or project_root is None or request.get('code_workspace') != str(code_root.resolve())
            or request.get('project_workspace') != str(project_root.resolve())
            or source.get('current_code_commit') != request.get('code_commit')
            or source.get('registered_command_sha256') != registration_hash):
        raise ValueError('系统登记能力或当前独立代码绑定已变化。')
    if receipt['mode'] == 'host_validated_registered_execution':
        argv, _, _ = _command(current, 'execution.simulation_runner')
        validation_argv, _, _ = _command(current, 'execution.simulation_runner', validation=True)
        if (source.get('schema') != 'coding.registered_execution_evidence.v1'
                or source.get('run_id') != run.run_id or source.get('project') != run.project
                or source.get('code_workspace') != str(code_root.resolve())
                or source.get('current_code_commit') != git(code_root, 'rev-parse', 'HEAD')
                or source.get('code_tree_sha') != git(code_root, 'rev-parse', 'HEAD^{tree}')
                or source.get('argv') != list(argv) or source.get('validation_argv') != list(validation_argv)
                or validation.get('argv') != list(validation_argv)
                or source.get('command_files') != _command_files(tuple(dict.fromkeys((*argv, *validation_argv))))):
            raise ValueError('系统入口、解释器或当前代码快照已变化。')
    details = [row.get('details', {}) for row in validation.get('checks', []) if isinstance(row, dict)]
    if (receipt['mode'] == 'host_validated_registered_execution'
            and not any(isinstance(item, dict) and item.get('training_started') is False
                and item.get('budget_unit') == config['budget_unit']
                and type(item.get('budget_value')) is int and item['budget_value'] == config['max_iters']
                and type(item.get('seed')) is int and item['seed'] == config['seed'] for item in details)):
        raise ValueError('真实预检未核验本次预算、种子及无训练状态。')
    if (not isinstance(validation.get('checks'), list) or not 1 <= len(validation['checks']) <= 100
            or any(not isinstance(row, dict) or row.get('ready') is not True
                or not isinstance(row.get('details', {}), dict) for row in validation['checks'])):
        raise ValueError('真实预检检查项不完整。')
    # Only bounded semantic summaries enter the model input, never unrestricted
    # child output or arbitrary files mentioned by a candidate.
    checks = [{'name': str(row.get('name', ''))[:128], 'ready': row.get('ready'),
        'verified_request': {key: row.get('details', {}).get(key)
            for key in ('budget_unit', 'budget_value', 'seed', 'training_started')}}
        for row in validation.get('checks', []) if isinstance(row, dict)][:16]
    inputs = validation['input_files']
    return {
        ref: {'schema': receipt['schema'], 'sha256': _sha(_regular(run.root / ref, run.root)),
            'run_id': run.run_id, 'project': run.project, 'mode': receipt['mode'],
            'model_calls': 0, 'training_started': False, 'approved': False,
            'requested': requested, 'execution_job': job,
            'current_preflight': {'ref': validation_ref, 'sha256': binding['sha256']}},
        validation_ref: {'schema': validation['schema'], 'sha256': _sha(validation_path),
            'run_id': run.run_id, 'project': run.project, 'status': 'passed',
            'pid': validation['pid'], 'returncode': 0, 'checks': checks,
            'verified_input_count': len(inputs), 'training_started': False,
            'verified_input_files': [{'path': row['path'], 'sha256': row['sha256']} for row in inputs[:16]],
            'input_summary_truncated': len(inputs) > 16}}
