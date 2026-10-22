"""Durable ZCode coding/execution feedback, without granting execution approval.

The ordinary graph remains the lifecycle authority. Failed attempts stay failed;
explicit repair edges connect them to a new reviewed coding/execution attempt.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from app.bridge.node_key import attempt_key, parse_node_key
from app.harness.agent_loop.trace import atomic_json, digest
from app.harness.agent_loop.zcode.continuation import safe_trace
from app.harness.runtime.run_graph import RunGraph
from app.harness.runtime.state_machine import NodeState
from app.settings import repo_root
from app.storage.run_store import RunHandle


def policy() -> dict[str, Any]:
    raw = yaml.safe_load((repo_root() / 'configs/coding_execution.yaml').read_text(encoding='utf-8'))
    if (not isinstance(raw, dict) or set(raw) != {'enabled', 'max_repairs'}
            or type(raw['enabled']) is not bool or type(raw['max_repairs']) is not int
            or not 0 <= raw['max_repairs'] <= 20):
        raise ValueError('Invalid coding_execution policy')
    return raw


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Expected a host-owned object: ' + path.name)
    return value


def scientific_inputs(run: RunHandle) -> dict[str, str | None]:
    return {name: _hash(run.root / name) if (run.root / name).is_file() else None for name in (
        'experiment/experiment_plan.approved.md', 'input/selected_data_source.json',
        'input/user_request.md', 'input/evolution_contract.v1.json')}


def record_candidate(run: RunHandle, node: str, invocation: str, artifact: Path) -> None:
    """Bind a real completed ZCode turn to its exact candidate and scientific scope."""
    trace_ref = f'agent_traces/coding/{invocation}'
    trace = safe_trace(run.root, trace_ref)
    checkpoint = trace / 'checkpoint.json'
    if not checkpoint.is_file():
        return
    state = _json(checkpoint)
    if (state.get('backend') != 'zcode' or state.get('status') != 'passed'
            or not state.get('continuation_compatibility')):
        return
    atomic_json(run.root / 'coding_execution/candidates' / (node + '.json'), {
        'run_id': run.run_id, 'project': run.project, 'node': node,
        'trace_ref': trace_ref, 'checkpoint_sha256': _hash(checkpoint),
        'artifact_ref': artifact.relative_to(run.root).as_posix(), 'artifact_sha256': _hash(artifact),
        'scientific_inputs': scientific_inputs(run)})


def _source(run: RunHandle, node: str) -> dict[str, Any] | None:
    coding = attempt_key('coding', parse_node_key(node).attempt)
    path = run.root / 'coding_execution/candidates' / (coding + '.json')
    if not path.is_file():
        return None
    source = _json(path)
    if (source.get('run_id') != run.run_id or source.get('project') != run.project
            or source.get('node') != coding or source.get('scientific_inputs') != scientific_inputs(run)):
        raise ValueError('编码与执行的研究输入已变化，需要重新核对，不能接续旧会话。')
    approved = run.root / 'coding/code_spec.approved.md'
    if not approved.is_file() or _hash(approved) != source['artifact_sha256']:
        raise ValueError('已批准编码交付与 ZCode 原会话不一致，需要明确修订。')
    checkpoint = safe_trace(run.root, source['trace_ref']) / 'checkpoint.json'
    if _hash(checkpoint) != source['checkpoint_sha256']:
        raise ValueError('ZCode 原会话检查点发生变化，不能接续。')
    return source


def verified_failures(run: RunHandle, attempt: int) -> list[dict[str, Any]]:
    """Only terminal failed processes with matching real receipts are repairable."""
    failures: list[dict[str, Any]] = []
    for path in sorted((run.root / 'execution/jobs').glob('*.json')):
        job = _json(path)
        if (job.get('project'), job.get('run_id')) != (run.project, run.run_id):
            raise ValueError('执行记录项目或任务身份不匹配。')
        if job.get('status') not in {'failed', 'completed'}:
            raise ValueError('仍有运行中或结果不明的作业；核对前不能自动修改代码或重启。')
        if job.get('attempt') != attempt or job['status'] != 'failed':
            continue
        result = job.get('result') or {}
        fingerprint = str(result.get('fingerprint_hash', '')).removeprefix('sha256:')
        matches = [p for p in (run.root / 'execution/local_commands').glob('*/*/execution_receipt.json')
                   if not p.is_symlink() and _hash(p) == fingerprint]
        if len(matches) != 1:
            raise ValueError('失败作业缺少唯一真实退出回执，不能自动重试。')
        receipt = _json(matches[0])
        if (receipt.get('run_id') != run.run_id or receipt.get('experiment_id') != job.get('experiment_id')
                or receipt.get('status') != 'failed' or type(receipt.get('returncode')) is not int):
            raise ValueError('失败作业尚不能确认进程已退出。')
        failures.append({'experiment_id': job['experiment_id'], 'error': receipt.get('error', ''),
            'returncode': receipt['returncode'], 'receipt_ref': matches[0].relative_to(run.root).as_posix(),
            'receipt_sha256': fingerprint})
    if not failures:
        raise ValueError('没有可核验的失败训练进程，不自动触发代码修复。')
    return failures


def repair_record(run: RunHandle, graph: RunGraph, node: str, error: str, *,
                  preparation: dict[str, Any] | None = None) -> dict[str, Any] | None:
    settings = policy()
    if not settings['enabled'] or not settings['max_repairs']:
        return None
    source = _source(run, node)
    if source is None:
        return None  # Historical/native/standalone execution follows its original workflow.
    if graph.metadata(node).get('coding_execution_repair_target'):
        return None
    repairs = [item for item in graph.nodes.values() if item.metadata.get('coding_execution_repair_target')]
    if len(repairs) >= settings['max_repairs']:
        raise ValueError('编码执行闭环已达到修复次数，保留真实失败，请人工核对。')
    if preparation is not None:
        if (preparation.get('status') != 'failed' or preparation.get('node') != node
                or preparation.get('run_id') != run.run_id or preparation.get('project') != run.project):
            raise ValueError('运行准备失败身份不能核验。')
        from app.execution.job_journal import job_states
        if any(row['status'] not in {'failed', 'completed'} for row in job_states(run.root)):
            raise ValueError('仍有结果未确认的作业，不自动修复运行准备。')
        evidence: list[dict[str, Any]] = [{'preparation': preparation}]
    else:
        evidence = verified_failures(run, parse_node_key(node).attempt)
    changes = run.root / 'input/research_changes.json'
    signature = digest({'error': error, 'changes': _hash(changes) if changes.is_file() else source['artifact_sha256']})
    if any(item.metadata.get('coding_execution_failure_signature') == signature for item in repairs):
        raise ValueError('相同输入再次出现相同失败，已停止自动重试。')
    attempt = max(parse_node_key(key).attempt for key in graph.nodes) + 1
    target = attempt_key('coding', attempt)
    value = {'schema': 'coding_execution.feedback.v1', 'run_id': run.run_id, 'project': run.project,
        'source_node': node, 'target_node': target, 'next_attempt': attempt, 'error': error,
        'phase': 'preflight' if preparation is not None else 'execution', 'evidence': evidence,
        'scientific_inputs': source['scientific_inputs'], 'failure_signature': signature,
        'continuation': {key: source[key] for key in ('trace_ref', 'checkpoint_sha256')},
        'instruction': '在同一个 ZCode 会话修复实现问题。保留批准的数据、种子、预算、模型集合和指标口径；'
                       '需要改变研究条件时说明阻碍。只交付代码修订，由 MARS 重新核对并沿用既有审批。'}
    atomic_json(run.root / 'coding_execution/repairs' / (target + '.json'), value)
    return value


def append_repair(graph: RunGraph, record: dict[str, Any]) -> None:
    source, coding = record['source_node'], record['target_node']
    execution = attempt_key('execution', record['next_attempt'])
    if graph.state(source) != NodeState.FAILED:
        raise ValueError('Only an explicitly failed stage can enter the repair loop')
    if coding in graph.nodes or execution in graph.nodes:
        raise ValueError('Repair already scheduled')
    for key in (coding, execution):
        identity = parse_node_key(key)
        graph.add_node(key, kind='agent', metadata={'stage': identity.stage, 'attempt': identity.attempt,
            'coding_execution': True, 'repair_source': source})
    graph.add_edge(source, coding, allow_failure=True)
    graph.add_edge(coding, execution)
    if 'writing' in graph.nodes:
        graph.add_edge(source, 'writing', allow_failure=True)
        graph.add_edge(execution, 'writing')
    graph.nodes[source].metadata.update(coding_execution_repair_target=execution,
        coding_execution_failure_signature=record['failure_signature'])


def repaired_failure(graph: RunGraph, node: str) -> bool:
    seen: set[str] = set()
    while node not in seen:
        seen.add(node)
        target = graph.metadata(node).get('coding_execution_repair_target')
        if target not in graph.nodes:
            return False
        if graph.state(target) == NodeState.DONE:
            return True
        if graph.state(target) != NodeState.FAILED:
            return False
        node = target
    return False


def restore_pending_repairs(run: RunHandle, graph: RunGraph) -> bool:
    """Finish only an already-recorded repair intent after an explicit resume.

    A crash between the feedback write and graph journal commit must not lose
    the repair or replay a job. This function never launches or approves work.
    """
    if not policy()['enabled']:
        return False
    changed = False
    for path in sorted((run.root / 'coding_execution/repairs').glob('*.json')):
        record = feedback_for_coding(run, path.stem)
        if record is None or record['target_node'] in graph.nodes:
            continue
        source_node = record['source_node']
        if source_node not in graph.nodes or graph.state(source_node) != NodeState.FAILED:
            continue
        source = _source(run, source_node)
        if source is None or record['continuation'] != {
                key: source[key] for key in ('trace_ref', 'checkpoint_sha256')}:
            raise ValueError('待恢复修复与原编码会话不匹配。')
        from app.execution.job_journal import job_states
        if any(row['status'] not in {'failed', 'completed'} for row in job_states(run.root)):
            raise ValueError('待恢复修复仍有结果不明的作业，请先核对。')
        if record['phase'] == 'execution' and record['evidence'] != verified_failures(
                run, parse_node_key(source_node).attempt):
            raise ValueError('原失败作业证据已变化，不恢复自动修复。')
        if record['next_attempt'] != max(parse_node_key(key).attempt for key in graph.nodes) + 1:
            raise ValueError('待恢复修复的任务顺序已变化，请核对。')
        append_repair(graph, record)
        changed = True
    return changed


def feedback_for_coding(run: RunHandle, node: str) -> dict[str, Any] | None:
    path = run.root / 'coding_execution/repairs' / (node + '.json')
    if not path.is_file():
        return None
    record = _json(path)
    if ((record.get('run_id'), record.get('project'), record.get('target_node')) != (run.run_id, run.project, node)
            or record.get('scientific_inputs') != scientific_inputs(run)):
        raise ValueError('修复反馈不属于当前研究输入，需要重新核对。')
    return record


def record_completed(run: RunHandle, node: str) -> None:
    if _source(run, node) is None:
        return
    paths = ('execution/metrics.json', 'execution/batch_summary.json')
    atomic_json(run.root / 'coding_execution/results' / (node + '.json'), {
        'schema': 'coding_execution.result.v1', 'run_id': run.run_id, 'project': run.project,
        'node': node, 'status': 'completed', 'evidence': {name: _hash(run.root / name) for name in paths},
        'scientific_inputs': scientific_inputs(run)})
