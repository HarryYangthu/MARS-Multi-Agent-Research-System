"""Explicit recovery of an existing run; never creates a replacement run."""
from __future__ import annotations

import json
import time
from typing import Any

from app.bridge.node_key import parse_node_key
from app.bridge.orchestrator import Orchestrator
from app.bridge.task_runtime import resumable_task, task_contract_path
from app.bridge.research_run_service import research_execution_admission, load_run_research_contract
from app.harness.agent_loop.trace import digest
from app.harness.llm.accounting import (
    ResourceBudgetError, RunModelBudget, charged_model_attempts, charged_token_component,
)
from app.harness.runtime.readiness import ProductionReadinessError, assert_ready_for_run
from app.harness.runtime.task_contract import TaskEnvelope


def recovery_status(orch: Orchestrator, run_id: str, *, project: str) -> dict[str, Any]:
    session = orch.session(run_id)
    if session.run.project != project:
        raise ValueError('任务所属项目不匹配')
    states = {key: state.value for key, state in session.graph.all_states().items()}
    result: dict[str, Any] = {'run_id': run_id, 'project': project, 'actions': [],
                             'status': 'idle', 'message': '', 'token': ''}
    evidence: list[Any] = [states, session.read_only, session.termination]

    def finish(status: str, message: str) -> dict[str, Any]:
        result.update(status=status, message=message, token=digest(evidence))
        return result

    if orch.owned_tasks.active(run_id) is not None:
        return finish('running', '任务正在运行，无需重复恢复。')
    if session.read_only or session.termination or orch.owned_tasks.closing:
        return finish('blocked', '此任务需要先核对停止或历史执行状态，请打开任务详情处理。')
    if research_execution_admission(session.run, session.request.extra) is not None:
        return finish('blocked', '研究执行约束尚未就绪，请打开任务详情处理。')
    latest: dict[str, str] = {}
    for key in states:
        identity = parse_node_key(key)
        previous = latest.get(identity.stage)
        if previous is None or identity.attempt > parse_node_key(previous).attempt:
            latest[identity.stage] = key
    candidates = [key for key in latest.values() if states[key] in {'failed', 'running'}]
    if not candidates:
        return finish('idle', '')
    agents = [orch.registry.get(parse_node_key(node).stage) for node in candidates]
    legacy = load_run_research_contract(session.run, session.request.extra) is None
    completion_driven = legacy and all(getattr(getattr(agent, 'loop_policy', None), 'completion_driven', False)
                                       for agent in agents)
    evidence.append({'completion_driven': completion_driven})
    # Unknown remote calls cannot be converted into a new attempt by clicking retry.
    ledger = session.run.root / 'resources/model_budget.v1.json'
    elapsed_exhausted = False
    if ledger.exists():
        try:
            raw = RunModelBudget(session.run.root).recovery_snapshot()
        except (ResourceBudgetError, OSError, ValueError):
            return finish('blocked', '资源记录或预算配置无法校验，请先核对记录和配置。')
        evidence.append(raw)
        if any(row.get('status') in {'in_flight', 'reconciliation_required'}
               for row in raw.get('requests', {}).values()):
            return finish('blocked', '存在结果未确认的模型请求，需先核对记录，避免重复调用。')
        rows = list(raw['requests'].values())
        limits = raw['configuration']['limits']
        exhausted = (limits['max_model_requests'] is not None
                     and sum(charged_model_attempts(row) for row in rows) >= limits['max_model_requests'])
        exhausted = exhausted or sum(row['charged_tokens'] for row in rows) >= limits['max_total_tokens']
        for component, limit in (('input', 'max_input_tokens'), ('output', 'max_billed_output_tokens')):
            exhausted = exhausted or (limit in limits and sum(charged_token_component(row, component)
                                      for row in rows) >= limits[limit])
        if limits.get('max_cost') is not None:
            exhausted = exhausted or any(row['charged_cost'] is None for row in rows)
            exhausted = exhausted or sum(row['charged_cost'] or 0 for row in rows) >= limits['max_cost']
        if exhausted and not completion_driven:
            return finish('blocked', '累计调用预算已耗尽，请先调整并核对预算；恢复不会清空已有用量。')
        elapsed_exhausted = (not completion_driven and
            time.time() - raw.get('revision_started_at', raw['started_at']) >= limits['max_elapsed_seconds'])
    if legacy:
        for agent in agents:
            check = getattr(agent, 'execution_blocker', None)
            if callable(check):
                try:
                    blocker = check(project)
                except (OSError, ValueError) as exc:
                    blocker = str(exc)
                if blocker:
                    return finish('blocked', str(blocker))
    resumable = not elapsed_exhausted
    retryable: list[str] = []
    for node in candidates:
        path = task_contract_path(session.run, node)
        if path.exists():
            contract = TaskEnvelope.model_validate_json(path.read_text())
            if contract.run_id != run_id or contract.node_id != node or contract.project != project:
                return finish('blocked', '任务检查点所属信息不匹配，请核对任务记录。')
            evidence.append(contract.model_dump())
            checkpoint = session.run.root / 'agent_traces' / contract.agent / contract.invocation_id / 'checkpoint.json'
            if not checkpoint.resolve().is_relative_to(session.run.root.resolve()):
                return finish('blocked', '检查点路径异常，请核对任务记录。')
            if checkpoint.exists():
                state = json.loads(checkpoint.read_text())
                if not isinstance(state, dict):
                    return finish('blocked', '检查点格式异常，请核对任务记录。')
                evidence.append(state)
                if state.get('pending') == 'tool' or state.get('pending_batch'):
                    return finish('blocked', '存在结果未确认的工具操作，需先核对执行回执，避免重复修改或运行。')
        try:
            resumable_task(session.run, node)
        except (OSError, ValueError, KeyError):
            resumable = False
        if states[node] == 'failed':
            retryable.append(node)
    if resumable:
        result['actions'].append({'action': 'resume', 'node': candidates[0], 'label': '继续恢复'})
    # An unowned running node must be reconciled, never silently restarted.
    if 'running' not in states.values():
        for node in retryable:
            result['actions'].append({'action': 'retry', 'node': node, 'label': '重试当前阶段'})
    if result['actions']:
        return finish('recoverable', '从检查点继续；重试会重新执行当前阶段并保留原任务记录。'
                      if resumable else '检查点不能直接续跑，可在原任务中重试失败阶段。')
    return finish('blocked', '当前检查点无法安全恢复，请打开任务详情核对执行记录。')


async def recover_run(orch: Orchestrator, run_id: str, *, project: str,
                      action: str, node: str, token: str) -> dict[str, Any]:
    view = recovery_status(orch, run_id, project=project)
    if view['status'] == 'running':
        return {'ok': True, 'status': 'already_running', 'run_id': run_id,
                'message': '任务已在运行，没有重复启动。'}
    if view['token'] != token:
        return {'ok': False, 'status': 'stale_recovery', 'error': '任务状态已变化，请刷新恢复选项后重试。'}
    if not any(item['action'] == action and item['node'] == node for item in view['actions']):
        return {'ok': False, 'status': 'recovery_blocked', 'error': view['message'] or '当前没有可用恢复操作。'}
    try:
        assert_ready_for_run(project=project)
    except ProductionReadinessError:
        return {'ok': False, 'status': 'not_ready', 'error': '执行配置未就绪，请先检查项目与 API 配置。'}
    if action == 'resume':
        result = orch.resume_owned_run(run_id)
    else:
        result = await orch.request_artifact_revision(run_id=run_id, agent=parse_node_key(node).stage,
            reason='用户请求在原任务中重试失败阶段；保留上游产物、既有记录及累计资源用量。')
    result['run_id'] = run_id
    if result.get('ok'):
        result['message'] = '已提交检查点恢复。' if action == 'resume' else '已在原任务中启动阶段重试。'
    else:
        result.setdefault('error', '恢复暂未启动，请刷新状态并查看任务记录。')
    return result
