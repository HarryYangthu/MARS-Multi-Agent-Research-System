"""Read-only failure and explicit-retry evidence for execution review."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.harness.runtime.project_scope import safe_scope_path
from app.harness.tools.execution.command_preflight import bounded_error
from app.storage.run_store import RunHandle


def _read(run: RunHandle, path: Path) -> tuple[bytes, dict[str, Any]]:
    name = path.relative_to(run.root).as_posix()
    raw = safe_scope_path(run.root, name, must_exist=True).read_bytes()
    return raw, {'ref': name, 'sha256': hashlib.sha256(raw).hexdigest()}


def execution_recovery_evidence(run: RunHandle, node: str) -> dict[str, Any]:
    """A retry request permits reconsideration, never substitutes for approval.

    Keep raw failures and cleanup acknowledgements. Do not infer completion
    from process absence or interpret the caller's explanation as a test.
    """
    lifecycle = run.root / 'events/run_lifecycle.jsonl'
    retry: dict[str, Any] | None = None
    stop: dict[str, Any] | None = None
    retry_after_stop = False
    if lifecycle.is_file():
        raw, source = _read(run, lifecycle)
        for sequence, line in enumerate(raw.splitlines(), 1):
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get('event') not in {'run.cancelled', 'run.explicit_execution_retry'}:
                continue
            if event.get('run_id') != run.run_id:
                raise ValueError('执行恢复事件与当前任务不匹配')
            # Hash only the relevant event, not an append-only log's changing tail.
            item = {'ref': source['ref'], 'line': sequence,
                    'sha256': hashlib.sha256(line).hexdigest(), 'event': event}
            if event.get('event') == 'run.cancelled':
                stop, retry_after_stop = item, False
            elif event.get('event') == 'run.explicit_execution_retry' and event.get('node') == node:
                retry, retry_after_stop = item, True
    jobs: list[dict[str, Any]] = []
    for path in sorted((run.root / 'execution/jobs').glob('*.json')):
        raw, item = _read(run, path)
        job = json.loads(raw)
        if (job.get('run_id'), job.get('project')) != (run.run_id, run.project):
            raise ValueError('执行作业记录与当前任务或项目不匹配')
        item['record'] = {key: job.get(key) for key in (
            'experiment_id', 'attempt', 'status', 'updated_at', 'cleanup_complete', 'error')}
        result = job.get('result', {})
        item['result_error'] = bounded_error(str(result.get('error') or ''), max_chars=len(raw) + 1)
        item['receipts'] = []
        # Completed results have their own measurement verification at dispatch.
        # Recovery review needs the failed invocation's exact host receipt.
        if job.get('status') in {'failed', 'cancelled'} and result.get('fingerprint_hash'):
            for ref in result.get('evidence_refs', []):
                target = Path(ref)
                if not target.is_absolute():
                    target = run.root / target
                if target.name != 'execution_receipt.json':
                    continue
                receipt_raw, receipt_item = _read(run, target)
                if 'sha256:' + receipt_item['sha256'] != result['fingerprint_hash']:
                    raise ValueError('失败作业的原始收据已改变')
                receipt = json.loads(receipt_raw)
                if (receipt.get('run_id') != run.run_id
                        or receipt.get('experiment_id') != job.get('experiment_id')
                        or receipt.get('status') != job.get('status')):
                    raise ValueError('失败收据与作业身份或状态不匹配')
                receipt_item['text'] = bounded_error(receipt_raw.decode('utf-8'), max_chars=len(receipt_raw) + 1)
                receipt_item['logs'] = []
                for stream in ('stdout', 'stderr'):
                    capture = receipt.get('log_capture', {}).get(stream)
                    if not capture:
                        continue
                    log = Path(capture['path'])
                    if not log.is_absolute():
                        log = run.root / log
                    log_raw, log_item = _read(run, log)
                    if log.resolve().parent != target.resolve().parent:
                        raise ValueError('失败日志不属于对应执行调用')
                    log_item.update(text=bounded_error(log_raw.decode('utf-8'), max_chars=len(log_raw) + 1),
                                    capture=capture, integrity='observed_current_bytes')
                    receipt_item['logs'].append(log_item)
                item['receipts'].append(receipt_item)
            if not item['receipts']:
                raise ValueError('失败作业缺少与指纹匹配的原始收据')
        jobs.append(item)
    return {'latest_explicit_retry': retry, 'latest_stop': stop,
            'retry_after_latest_stop': retry_after_stop, 'jobs': jobs,
            'scope': 'Historical failures remain failures. An explicit retry requests a fresh review; it is not '
                     'execution approval. Caller explanations are not verified tests. Cleanup acknowledgements '
                     'must be checked; a stop after the retry takes precedence. Fresh configuration checks remain required.'}
