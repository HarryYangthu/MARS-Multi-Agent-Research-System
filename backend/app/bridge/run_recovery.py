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
    ResourceBudgetError, RunModelBudget, charged_token_component, model_request_capacity,
)
from app.harness.llm.provider_base import MAX_LLM_RETRIES
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
    stopped_draft = False

    def finish(status: str, message: str) -> dict[str, Any]:
        result.update(status=status, message=message, token=digest(evidence))
        return result

    if orch.owned_tasks.active(run_id) is not None:
        return finish('running', '任务正在运行，无需重复恢复。')
    if session.termination and not session.read_only and not orch.owned_tasks.closing:
        termination = session.termination
        nodes = termination.get('interrupted_nodes', [])
        if (termination.get('type') == 'cancelled' and termination.get('cleanup_complete') is True
                and len(nodes) == 1 and parse_node_key(nodes[0]).stage == 'execution'
                and states.get(nodes[0]) == 'failed'):
            from app.execution.job_journal import stopped_execution_retry_blocker, job_states
            evidence.append(job_states(session.run.root))
            blocker = stopped_execution_retry_blocker(session.run.root)
            if not blocker:
                result['actions'] = [{'action': 'retry', 'node': nodes[0], 'label': '重新核对并恢复仿真'}]
                return finish('recoverable', '作业中断清理已确认；重新核对配置后开启明确的新尝试，旧收据保留。')
        if len(nodes) == 1 and parse_node_key(nodes[0]).stage in {'idea', 'experiment', 'writing'}:
            from app.bridge.stopped_draft_retry import stopped_draft_retry_blocker
            try:
                blocker = stopped_draft_retry_blocker(session.run, nodes[0],
                    stage=parse_node_key(nodes[0]).stage, termination=termination, states=states)
            except (OSError, ValueError, KeyError) as exc:
                blocker = str(exc)
            if blocker:
                return finish('blocked', blocker)
            stopped_draft = True
        if (termination.get('type') == 'cancelled' and termination.get('cleanup_complete') is True
                and termination.get('scope') == 'owned_async_tasks' and len(nodes) == 1
                and parse_node_key(nodes[0]).stage == 'coding' and states.get(nodes[0]) == 'failed'):
            from app.bridge.research_branch import stopped_coding_retry_blocker
            try:
                blocker = stopped_coding_retry_blocker(session.run, nodes[0])
            except (OSError, ValueError, KeyError) as exc:
                blocker = str(exc)
            if blocker:
                return finish('blocked', blocker)
            stopped_draft = True
    if session.read_only or (session.termination and not stopped_draft) or orch.owned_tasks.closing:
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
    model_agents = [agent for agent in agents if getattr(agent, "requires_model", True)]
    legacy = load_run_research_contract(session.run, session.request.extra) is None
    # Unknown remote calls cannot be converted into a new attempt by clicking retry.
    ledger = session.run.root / 'resources/model_budget.v1.json'
    elapsed_exhausted = False
    if ledger.exists():
        try:
            model_budget = RunModelBudget(session.run.root)
            raw = model_budget.recovery_snapshot()
        except (ResourceBudgetError, OSError, ValueError):
            return finish('blocked', '资源记录或预算配置无法校验，请先核对记录和配置。')
        evidence.append(raw)
        evidence.append({'token_usage_mode': model_budget.token_mode})
        if any(row.get('status') in {'in_flight', 'reconciliation_required'}
               for row in raw.get('requests', {}).values()):
            return finish('blocked', '存在结果未确认的模型请求，需先核对记录，避免重复调用。')
        rows = list(raw['requests'].values())
        limits = raw['configuration']['limits']
        # Do not offer retry when even the next call's SDK retries cannot fit.
        # A completion-driven loop never exempts its Agent from the run budget.
        capacities = [model_request_capacity(raw, getattr(getattr(agent, 'config', None),
                       'max_retries', MAX_LLM_RETRIES)) for agent in model_agents]
        evidence.append([{'used': item.used, 'limit': item.limit, 'required': item.required}
                         for item in capacities])
        for capacity in capacities:
            if not capacity.available:
                return finish('blocked', f'累计调用预算不足：已计入 {capacity.used} 次，上限 {capacity.limit} 次，'
                    f'下一次调用需预留 {capacity.required} 次（含网络重试）。进度已保留，'
                    '请调整并核对预算后继续；刷新和重试不会清空已有用量。')
        exhausted = False
        if model_budget.token_mode == 'limited':
            exhausted = exhausted or sum(row['charged_tokens'] for row in rows) >= limits['max_total_tokens']
            for component, limit in (('input', 'max_input_tokens'), ('output', 'max_billed_output_tokens')):
                exhausted = exhausted or (limit in limits and sum(charged_token_component(row, component)
                                          for row in rows) >= limits[limit])
        if limits.get('max_cost') is not None:
            exhausted = exhausted or any(row['charged_cost'] is None for row in rows)
            exhausted = exhausted or sum(row['charged_cost'] or 0 for row in rows) >= limits['max_cost']
        if exhausted and model_agents:
            return finish('blocked', '累计调用预算已耗尽，请先调整并核对预算；恢复不会清空已有用量。')
        elapsed_exhausted = (
            time.time() - raw.get('revision_started_at', raw['started_at']) >= limits['max_elapsed_seconds'])
    if legacy:
        for agent in agents:
            if getattr(agent, 'name', '') == 'coding':
                from app.bridge.research_branch import coding_workspace_blocker
                try:
                    blocker = coding_workspace_blocker(session.run)
                except (OSError, ValueError) as exc:
                    blocker = str(exc)
                evidence.append({'coding_workspace_blocker': blocker})
                if blocker:
                    return finish('blocked', blocker)
                continue
            check = getattr(agent, 'execution_blocker', None)
            if callable(check):
                try:
                    blocker = check(project)
                except (OSError, ValueError) as exc:
                    blocker = str(exc)
                if blocker:
                    return finish('blocked', str(blocker))
    resumable = not elapsed_exhausted and not stopped_draft
    retryable: list[str] = []
    provider_hint = ''
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
                from app.harness.llm.failure_hint import checkpoint_failure_hint
                provider_hint = checkpoint_failure_hint(checkpoint.parent, status=str(state.get('status', ''))) or provider_hint
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
        if provider_hint:
            return finish('recoverable', provider_hint)
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
            reason='用户请求在原任务中重试失败阶段；保留上游产物、既有记录及累计资源用量。',
            restart_stopped=bool(orch.session(run_id).termination))
    result['run_id'] = run_id
    if result.get('ok'):
        result['message'] = '已提交检查点恢复。' if action == 'resume' else '已在原任务中启动阶段重试。'
    else:
        result.setdefault('error', '恢复暂未启动，请刷新状态并查看任务记录。')
    return result
