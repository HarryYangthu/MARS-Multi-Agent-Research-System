"""Explicit coding repair before any experiment has been confirmed or launched.

Completed coding nodes and approved artifact versions remain historical facts.
A new coding attempt must pass the ordinary review, followed by a fresh execution
intake and configuration review. This is not permission to restart a running job.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any
from uuid import uuid4

from app.bridge.node_key import attempt_key, parse_node_key
from app.harness.persistence import atomic_write_json, atomic_write_text, path_lock
from app.harness.runtime.run_graph import RunGraph
from app.harness.runtime.state_machine import NodeState
from app.storage.run_store import RunHandle


def pending_revision(run: RunHandle) -> dict[str, Any] | None:
    rows = [json.loads(path.read_text(encoding='utf-8'))
            for path in (run.root / 'hitl/preexecution_revisions').glob('*.json')]
    active = [row for row in rows if row['status'] != 'completed']
    if len(active) > 1:
        raise ValueError('存在多个未完成的执行前返工请求，请核对记录')
    return active[0] if active else None


def assert_no_execution(run: RunHandle) -> None:
    for directory, pattern in (('confirmations', '*.json'), ('jobs', '*.json'),
                               ('local_commands', '**/job.json'), ('paper_static', '**/execution_receipt.json'),
                               ('pim_cpu', '**/execution_receipt.json')):
        if any((run.root / 'execution' / directory).glob(pattern)):
            raise ValueError('实验已经确认或启动，不能用执行前返工替换；请使用执行诊断流程')
    if (run.root / 'execution/batch_summary.json').exists():
        raise ValueError('已有实验批次记录，不能用执行前返工替换')


def enqueue_revision(run: RunHandle, graph: RunGraph, coding: str, execution: str, reason: str,
                     *, data_source: dict[str, Any] | None = None) -> dict[str, Any]:
    with path_lock(run.root / 'execution/.confirmation.lock'):
        return _enqueue_revision(run, graph, coding, execution, reason, data_source=data_source)


def _enqueue_revision(run: RunHandle, graph: RunGraph, coding: str, execution: str, reason: str,
                      *, data_source: dict[str, Any] | None = None) -> dict[str, Any]:
    if not reason.strip():
        raise ValueError('执行前返工必须说明具体原因')
    assert_no_execution(run)
    if pending := pending_revision(run):
        if data_source is not None and pending.get('data_source') != data_source:
            raise ValueError('已有未完成的修订，不能再次切换数据；请先处理当前修订')
        attach_revision(graph, pending)
        return pending
    if graph.state(coding) != NodeState.DONE or graph.state(execution) != NodeState.APPROVED:
        raise ValueError('仅支持编码已完成、执行清单待确认的返工')
    if any(state in {NodeState.RUNNING, NodeState.WAITING_REVIEW, NodeState.FAILED}
           for state in graph.all_states().values()):
        raise ValueError('存在其他活动或失败阶段，请先处理该阶段')
    if any(graph.state(key) != NodeState.PENDING for key in graph.successors(execution)):
        raise ValueError('执行下游已经开始，不能插入执行前返工')
    attempt = max(parse_node_key(key).attempt for key in graph.nodes
                  if parse_node_key(key).stage == 'coding') + 1
    node = attempt_key('coding', attempt)
    row: dict[str, Any] = {'schema': 'preexecution_revision.v1', 'id': uuid4().hex, 'run_id': run.run_id,
           'node': node, 'execution_node': execution, 'previous_coding_node': coding,
           'reason': reason.strip(), 'status': 'pending', 'created_at': datetime.now(timezone.utc).isoformat(),
           'previous_graph': graph.to_dict()}
    if data_source is not None:
        from pathlib import Path
        from app.storage.data_source_store import sha256_file
        source = Path(str(data_source.get('stored_path') or ''))
        if data_source.get('project') != run.project or not source.is_file():
            raise ValueError('所选数据不属于本项目或文件不可读取')
        if sha256_file(source) != data_source.get('checksum'):
            raise ValueError('数据文件已变化，请重新上传或登记后再修订')
        experiments = [key for key in graph.nodes if parse_node_key(key).stage == 'experiment']
        previous = max(experiments, key=lambda key: parse_node_key(key).attempt) if experiments else None
        if previous and graph.state(previous) not in {NodeState.DONE, NodeState.SKIPPED}:
            raise ValueError('实验设计尚未完成，不能插入数据修订')
        experiment = attempt_key('experiment', parse_node_key(previous).attempt + 1 if previous else 1)
        old_data = run.root / 'input/selected_data_source.json'
        row.update(nodes=[experiment, node], previous_experiment_node=previous, data_source=data_source,
                   previous_data_source=json.loads(old_data.read_text(encoding='utf-8')) if old_data.is_file() else None)
    # The durable request is written first. Admission can reconcile its node after
    # a crash; no approved document or original node state is overwritten.
    atomic_write_json(run.root / 'hitl/preexecution_revisions' / (row['id'] + '.json'), row)
    attach_revision(graph, row)
    return row


def attach_revision(graph: RunGraph, row: dict[str, Any]) -> None:
    node, execution = row['node'], row['execution_node']
    if node not in graph.nodes:
        graph.add_node(node, metadata={'stage': 'coding', 'attempt': parse_node_key(node).attempt,
                                      'preexecution_revision': row['id']})
    graph.add_edge(row['previous_coding_node'], node)
    if row.get('data_source') is not None:
        experiment = row['nodes'][0]
        if experiment not in graph.nodes:
            graph.add_node(experiment, metadata={'stage': 'experiment', 'attempt': parse_node_key(experiment).attempt,
                                                'preexecution_revision': row['id']})
        if row.get('previous_experiment_node'):
            graph.add_edge(row['previous_experiment_node'], experiment)
        graph.add_edge(row['previous_coding_node'], experiment)
        graph.add_edge(experiment, node)
    graph.add_edge(node, execution)
    # The generic scheduler treats APPROVED as a satisfied predecessor. Keep
    # downstream work blocked even if the new coding attempt fails or is stopped.
    for downstream in graph.successors(execution):
        graph.add_edge(node, downstream)


def apply_revision_data(run: RunHandle, row: dict[str, Any]) -> dict[str, Any] | None:
    """Replay the durable, user-selected input before either revised stage runs."""
    source = row.get('data_source')
    if not isinstance(source, dict):
        return None
    from app.storage.data_source_store import selection_summary
    atomic_write_json(run.root / 'input/selected_data_source.json', source)
    atomic_write_json(run.root / 'context/selected_data_source.json', source)
    atomic_write_text(run.root / 'context/selected_data_source.md', selection_summary(source) + '\n')
    return source


def finish_revision(run: RunHandle, row: dict[str, Any]) -> None:
    atomic_write_json(run.root / 'hitl/preexecution_revisions' / (row['id'] + '.json'),
                      {**row, 'status': 'completed', 'completed_at': datetime.now(timezone.utc).isoformat()})
