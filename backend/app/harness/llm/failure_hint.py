"""Content-free provider failure hints from terminal native receipts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def provider_failure_hint(events: list[dict[str, Any]], *, status: str) -> str:
    if status != 'model_error':
        return ''
    requests = [i for i, event in enumerate(events) if event.get('kind') == 'model_request']
    if not requests:
        return ''
    index = requests[-1]
    provider = events[index].get('provider')
    attempts = [event for event in events[index + 1:]
                if event.get('kind') in {'sdk_attempt_failed', 'sdk_attempt_succeeded'}]
    if not attempts or attempts[-1].get('kind') != 'sdk_attempt_failed':
        return ''
    details = attempts[-1].get('details', {})
    if (provider == 'zhipu' and isinstance(details, dict)
            and details.get('http_status') == 429 and str(details.get('api_error_code')) == '1113'):
        return ('GLM 账户余额不足或无可用资源包（1113）。请补充额度或在设置中更换可用 API 后继续恢复；'
                '进度和累计调用预算已保留，系统不会自动重复调用。')
    return ''


def checkpoint_failure_hint(root: Path, *, status: str) -> str:
    if status != 'model_error':
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
