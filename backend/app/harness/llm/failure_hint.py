"""Content-free provider failure hints from terminal native receipts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _terminal_provider(events: list[dict[str, Any]], request_index: int,
                       terminal_index: int) -> str | None:
    request = events[request_index]
    declared = request.get('provider')
    provider = declared if isinstance(declared, str) and declared else None
    receipts = [event for event in events[request_index + 1:terminal_index]
                if event.get('kind') == 'provider_request']
    if not receipts:
        return provider
    terminal = events[terminal_index]
    identity = terminal.get('request')
    if type(identity) is not int or identity < 1:
        return None
    if 'request' in request and (type(request['request']) is not int
                                 or request['request'] != identity):
        return None
    matched = [event for event in receipts
               if type(event.get('request')) is int and event['request'] == identity]
    if not matched:
        return None
    providers: set[str] = set()
    for receipt in matched:
        value = receipt.get('provider')
        if not isinstance(value, str) or not value:
            return None
        providers.add(value)
        correlation = receipt.get('correlation')
        if isinstance(correlation, dict):
            for related in (request, terminal):
                other = related.get('correlation')
                if isinstance(other, dict) and other != correlation:
                    return None
    if len(providers) != 1 or (provider is not None and provider not in providers):
        return None
    return next(iter(providers))


def provider_failure_hint(events: list[dict[str, Any]], *, status: str) -> str:
    if status == 'validation_failed':
        return _validation_failure_hint(events)
    if status == 'blocked':
        return _tool_approval_hint(events)
    if status == 'protocol_exhausted':
        errors = [event for event in events if event.get('kind') == 'protocol_error']
        if not errors:
            return ''
        error = str(errors[-1].get('error', ''))
        problem = ('修订操作不符合工具约定' if 'revision operation' in error else
                   '修订字段路径不符合约定' if 'revision path' in error else
                   '结构化指令含重复字段' if 'duplicate JSON key' in error else
                   '结构化指令未通过格式校验')
        return (f'{problem}，有限修复后仍未通过。未通过的草稿和已读材料已保留；'
                '重试将复用可核验的已有工作，方案仍需通过内容校验与独立评审。')
    if status != 'model_error':
        return ''
    requests = [i for i, event in enumerate(events) if event.get('kind') == 'model_request']
    if not requests:
        return ''
    index = requests[-1]
    tail = events[index + 1:]
    hint = _truncation_hint(events[index], tail)
    if hint:
        return hint
    attempt_indices = [i for i in range(index + 1, len(events))
                       if events[i].get('kind') in {'sdk_attempt_failed', 'sdk_attempt_succeeded'}]
    attempts = [events[i] for i in attempt_indices]
    if not attempts or attempts[-1].get('kind') != 'sdk_attempt_failed':
        return ''
    provider = _terminal_provider(events, index, attempt_indices[-1])
    details = attempts[-1].get('details', {})
    http_status = details.get('http_status') if isinstance(details, dict) else None
    if provider and type(http_status) is int and 500 <= http_status <= 599:
        label = {'deepseek': 'DeepSeek', 'zhipu': 'GLM'}.get(provider, '模型服务')
        return (f'{label} 服务返回 HTTP {http_status}（已尝试 {len(attempts)} 次），本次未取得模型回复。'
                '已有代码、草稿与调用记录已保留；请核对服务可用性后在原任务恢复或重试。')
    if (provider == 'deepseek' and isinstance(details, dict)
            and details.get('http_status') == 402):
        return ('DeepSeek 账户余额不足（HTTP 402）。请充值或切换有余额的 API 密钥后重试当前阶段；'
                '已有方案与调用记录保留。')
    if isinstance(details, dict) and details.get('exception_type') in {
            'APIConnectionError', 'ConnectError', 'NetworkError'}:
        label = 'GLM' if provider == 'zhipu' else '模型服务'
        timed_out = any(isinstance(event.get('details'), dict)
                        and event['details'].get('exception_type') in {
                            'APITimeoutError', 'ReadTimeout', 'ConnectTimeout', 'TimeoutError'}
                        for event in attempts[:-1])
        sequence = '请求先超时，重试后连接失败' if timed_out else '请求连接失败'
        return (f'{label} {sequence}（已尝试 {len(attempts)} 次），本次未取得模型回复。'
                '已有工作与调用记录已保留；请检查网络、代理或模型服务后重试。'
                '恢复时仍会核对原请求和工具状态；该错误不表示仿真代码失败。')
    if isinstance(details, dict) and details.get('exception_type') in {
            'APITimeoutError', 'ReadTimeout', 'ConnectTimeout', 'TimeoutError'}:
        label = 'GLM' if provider == 'zhipu' else '模型服务'
        return (f'{label} 请求超时（已尝试 {len(attempts)} 次），本次未取得模型回复。'
                '进度和累计调用记录已保留；请检查网络或模型服务后重试。'
                '恢复时仍会核对原请求和工具状态。')
    if (provider == 'zhipu' and isinstance(details, dict)
            and details.get('http_status') == 429 and str(details.get('api_error_code')) == '1113'):
        return ('GLM 账户余额不足或无可用资源包（1113）。请核对额度或在设置中更换可用 API 后，在原任务重试当前阶段；'
                '进度和累计调用预算已保留，系统不会自动重复调用。')
    return ''


def _validation_failure_hint(events: list[dict[str, Any]]) -> str:
    # Attribute a terminal rejection only to its own invocation, never to a
    # previous draft or to provider text that merely quotes an error.
    terminal = events[-1] if events else {}
    if terminal.get('kind') != 'finished' or terminal.get('status') != 'validation_failed':
        return ''
    validations = [event for event in events[:-1] if event.get('kind') == 'validation'
                   and event.get('correlation') == terminal.get('correlation')]
    if not validations:
        return ''
    errors = validations[-1].get('errors')
    if not isinstance(errors, list) or not errors or any(not isinstance(error, str) for error in errors):
        return ''
    if any(error.endswith(': registered command does not support this project') for error in errors):
        return ('上次编码交付被拒绝：登记的训练命令不支持当前项目。'
                '请核对宿主命令的项目绑定后重试编码；已有代码和失败记录保留，此次交付未进入后续执行。')
    return ('本阶段交付未通过校验。请打开任务详情中的“产物校验”记录核对未通过项，'
            '修正后重试当前阶段；已有代码和原始记录保留。')


def _tool_approval_hint(events: list[dict[str, Any]]) -> str:
    relevant = [event for event in events if event.get('kind') in {
        'model_request', 'tool_dispatch', 'observation'}]
    if len(relevant) < 2:
        return ''
    dispatch, observation = relevant[-2:]
    visible = observation.get('visible')
    if (dispatch.get('kind') != 'tool_dispatch' or observation.get('kind') != 'observation'
            or observation.get('tool') != dispatch.get('tool')
            or observation.get('correlation') != dispatch.get('correlation')
            or not isinstance(visible, dict) or visible.get('status') != 'requires_approval'
            or visible.get('ok') is not False):
        return ''
    if observation.get('tool') == 'code.delete_file':
        return ('编码在删除文件时遇到人工审批要求，系统未自动批准。'
                '若删除只是可选清理，可保留文件并修正交付后重试；'
                '若确实必须删除，请先核对具体文件及审批记录。')
    return '工具操作需要人工审批，任务已暂停；请先核对操作及审批记录，系统未自动批准。'


def _truncation_hint(request: dict[str, Any], tail: list[dict[str, Any]]) -> str:
    terminal = [event for event in tail if event.get('kind') in {
        'model_error', 'model_response', 'completion_recovery', 'sdk_attempt_started'}]
    if not terminal or terminal[-1].get('kind') != 'model_error':
        return ''
    reason = terminal[-1].get('reason')
    if (not isinstance(reason, dict) or reason.get('code') != 'output_truncated'
            or reason.get('finish_reason') != 'length'):
        return ''
    responses = [event for event in tail if event.get('kind') == 'model_response']
    if (not responses or responses[-1].get('rejected') is not True
            or responses[-1].get('reason') != reason):
        return ''
    review = request.get('phase') == 'reflect'
    phase = '独立评审' if review else '方案生成'
    provider = {'deepseek': 'DeepSeek', 'zhipu': 'GLM'}.get(str(request.get('provider')), '模型')
    limit = request.get('max_tokens')
    budget = f'（本次配置为 {limit} token）' if type(limit) is int and limit > 0 else ''
    output = '未取得完整评审结论' if review else '未取得完整输出'
    return (f'{phase}阶段的 {provider} 回复达到长度上限后被截断{budget}，{output}。'
            '推理内容也占用输出额度；请提高对应 Agent 的输出上限并重启服务，再重试当前阶段。'
            '已有进度和草稿保留，仍需完成校验与独立评审。')


def _model_call_limit_hint(root: Path) -> str:
    path = root / 'checkpoint.json'
    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            return ''
        state = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return ''
    if (not isinstance(state, dict) or state.get('status') != 'budget_exhausted'
            or state.get('error') != 'ZCode model-call limit reached'):
        return ''
    counts = state.get('counts')
    used = counts.get('model_requests') if isinstance(counts, dict) else None
    if type(used) is not int or used <= 0:
        return ''
    return (f'本轮已到模型调用次数上限（{used} 次），交付内容尚未通过验收。'
            '已有工作、检查结果和累计调用记录已保留；核对未通过项后可在原任务重试。'
            '此状态不代表仿真已运行或仿真代码运行失败。')


def checkpoint_failure_hint(root: Path, *, status: str) -> str:
    if status == 'budget_exhausted':
        return _model_call_limit_hint(root) or _zcode_budget_hint(root)
    if status not in {'model_error', 'protocol_exhausted', 'blocked', 'validation_failed'}:
        return ''
    path = root / 'events.jsonl'
    try:
        # model_request records can contain a large protected prompt. Grow the
        # bounded tail until its provider identity is present; never infer it.
        limit = 128_000
        while limit <= 4 * 1024 * 1024:
            with path.open('rb') as stream:
                stream.seek(0, 2)
                offset = max(0, stream.tell() - limit)
                stream.seek(offset)
                lines = stream.read().decode('utf-8', errors='replace').splitlines()
            if offset:
                lines = lines[1:]
            events = []
            for line in lines:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    events.append(event)
            required_kind = 'validation' if status == 'validation_failed' else 'model_request'
            if any(event.get('kind') == required_kind for event in events) or not offset:
                return provider_failure_hint(events, status=status)
            limit *= 2
        return ''
    except OSError:
        return ''


def _zcode_budget_hint(root: Path) -> str:
    path = root / 'checkpoint.json'
    try:
        if path.stat().st_size > 4 * 1024 * 1024:
            return ''
        state = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return ''
    if (not isinstance(state, dict) or state.get('backend') != 'zcode'
            or state.get('status') != 'budget_exhausted' or not isinstance(state.get('counts'), dict)):
        return ''
    labels = {'ZCode tool-call limit reached': ('工具调用', 'tool_dispatches'),
              'ZCode model-call limit reached': ('模型调用', 'model_requests')}
    item = labels.get(str(state.get('error')))
    if item is None:
        return ''
    label, key = item
    used = state['counts'].get(key)
    if type(used) is not int or used < 1:
        return ''
    return (f'ZCode 已达到本轮{label}次数上限（已调用 {used} 次），交付尚未完成验收。'
            '已有代码、检查结果和累计用量已保留；请核对剩余工作后重试当前阶段，'
            '仍需完成真实测试与人工审核。')
