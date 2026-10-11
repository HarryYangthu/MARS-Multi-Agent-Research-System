"""Content-free provider failure hints from terminal native receipts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def provider_failure_hint(events: list[dict[str, Any]], *, status: str) -> str:
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
    provider = events[index].get('provider')
    tail = events[index + 1:]
    hint = _truncation_hint(events[index], tail)
    if hint:
        return hint
    attempts = [event for event in events[index + 1:]
                if event.get('kind') in {'sdk_attempt_failed', 'sdk_attempt_succeeded'}]
    if not attempts or attempts[-1].get('kind') != 'sdk_attempt_failed':
        return ''
    details = attempts[-1].get('details', {})
    if isinstance(details, dict) and details.get('exception_type') in {
            'APITimeoutError', 'ReadTimeout', 'ConnectTimeout', 'TimeoutError'}:
        label = 'GLM' if provider == 'zhipu' else '模型服务'
        return (f'{label} 请求超时（已尝试 {len(attempts)} 次），本次未取得模型回复。'
                '进度和累计调用记录已保留；请检查网络或模型服务后重试。'
                '恢复时仍会核对原请求和工具状态。')
    if (provider == 'zhipu' and isinstance(details, dict)
            and details.get('http_status') == 429 and str(details.get('api_error_code')) == '1113'):
        return ('GLM 账户余额不足或无可用资源包（1113）。请补充额度或在设置中更换可用 API 后继续恢复；'
                '进度和累计调用预算已保留，系统不会自动重复调用。')
    return ''


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


def checkpoint_failure_hint(root: Path, *, status: str) -> str:
    if status not in {'model_error', 'protocol_exhausted'}:
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
            if any(event.get('kind') == 'model_request' for event in events) or not offset:
                return provider_failure_hint(events, status=status)
            limit *= 2
        return ''
    except OSError:
        return ''
