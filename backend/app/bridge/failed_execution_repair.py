"""Admission evidence for explicit code repair after a terminated failed batch."""
from __future__ import annotations

import hashlib
import json

from app.bridge.execution_recovery_evidence import execution_recovery_evidence
from app.storage.run_store import RunHandle


def failed_execution_snapshot(run: RunHandle, node: str) -> dict[str, str]:
    evidence = execution_recovery_evidence(run, node)
    jobs = evidence['jobs']
    if not jobs or not any(job['record']['status'] == 'failed' for job in jobs):
        raise ValueError('仅允许有真实失败作业的编码修复；不能替代执行审批')
    snapshot: dict[str, str] = {}
    for job in jobs:
        if job['record']['status'] not in {'failed', 'completed'}:
            raise ValueError('存在运行中、中断或状态不明的作业，不能修改执行代码')
        if job['record']['status'] == 'failed' and (
            not job['receipts'] or not all(receipt['process_exit_recorded'] for receipt in job['receipts'])
        ):
            raise ValueError('失败作业缺少可核验的进程退出收据')
        snapshot[job['ref']] = job['sha256']
    # Also reject unjournalled/in-flight local invocations. Old invocations stay
    # untouched, including those preceding an explicit retry of the same job.
    invocations = list((run.root / 'execution/local_commands').glob('*/*/job.json'))
    if not invocations:
        raise ValueError('当前执行后修复仅支持有完整本地进程收据的任务')
    for path in invocations:
        receipt_path = path.parent / 'execution_receipt.json'
        if not path.resolve().is_relative_to(run.root.resolve()) or not receipt_path.resolve().is_relative_to(run.root.resolve()):
            raise ValueError('执行记录越出任务目录')
        request = json.loads(path.read_text(encoding='utf-8'))
        receipt_raw = receipt_path.read_bytes()
        receipt = json.loads(receipt_raw)
        if (request.get('run_id') != run.run_id or receipt.get('run_id') != run.run_id
                or receipt.get('experiment_id') != request.get('experiment_id')
                or receipt.get('status') not in {'completed', 'failed'}
                or type(receipt.get('returncode')) is not int):
            raise ValueError('存在未退出或身份不匹配的执行调用，不能返工')
        snapshot[path.relative_to(run.root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        snapshot[receipt_path.relative_to(run.root).as_posix()] = hashlib.sha256(receipt_raw).hexdigest()
    return snapshot
