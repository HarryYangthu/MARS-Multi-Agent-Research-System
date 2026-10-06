"""Durable job receipts; completed work is reused only with intact real evidence."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from app.execution.results import SimulationResult
from app.execution.simulation_runner import JobSpec, run_one
from app.harness.agent_loop.trace import digest
from app.harness.persistence import atomic_write_json
from app.settings import get_settings


def _hash(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
    return 'sha256:' + checksum.hexdigest()


def _path(spec: JobSpec) -> Path:
    if spec.run_root is None:
        raise ValueError('受管理作业必须绑定研究目录')
    key = digest([spec.experiment_id, spec.config.get('attempt', 1), spec.config.get('confirmation_token', '')])
    return spec.run_root / 'execution/jobs' / (key + '.json')


def _evidence(spec: JobSpec, result: SimulationResult) -> list[dict[str, str]]:
    assert spec.run_root is not None
    safe = ''.join(ch if ch.isalnum() or ch in '-_' else '_' for ch in spec.experiment_id)
    paths = list((spec.run_root / 'execution/local_commands' / safe).glob('*/execution_receipt.json'))
    paths += list((spec.run_root / 'execution/pim_cpu' / safe).glob('*/execution_receipt.json'))
    paths += list((spec.run_root / 'execution/paper_static' / safe).glob('*/execution_receipt.json'))
    paths = [path for path in paths if _hash(path) == result.fingerprint_hash]
    evidence: list[dict[str, str]] = []
    for path in paths:
        evidence.append({'path': str(path.resolve()), 'sha256': _hash(path)})
        receipt = json.loads(path.read_text())
        for row in receipt.get('evidence', []):
            target = Path(row['path'])
            if _hash(target) != row['sha256']:
                raise ValueError('作业测量证据与收据不一致')
            evidence.append({'path': str(target.resolve()), 'sha256': row['sha256']})
        if receipt.get('schema') == 'paper_static_receipt.v1':
            for key in ('summary', 'log'):
                target = Path(receipt[key + '_path'])
                checksum = _hash(target)
                if checksum != 'sha256:' + receipt[key + '_sha256']:
                    raise ValueError('作业测量证据与收据不一致')
                evidence.append({'path': str(target.resolve()), 'sha256': checksum})
        if receipt.get('schema') == 'pim_cpu_receipt.v1':
            target = path.parent / 'result.json'
            if _hash(target) != receipt['result_sha256']:
                raise ValueError('作业测量证据与收据不一致')
            evidence.append({'path': str(target.resolve()), 'sha256': _hash(target)})
    if not evidence:
        raise ValueError('作业没有可核验的真实执行收据，不能标记完成')
    return evidence


def job_states(run_root: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted((run_root / 'execution/jobs').glob('*.json')):
        raw = json.loads(path.read_text())
        row = {key: raw.get(key) for key in ('experiment_id', 'attempt', 'status', 'updated_at', 'error')}
        result = raw.get('result', {})
        row.update(metrics=result.get('metrics', {}), duration_seconds=result.get('duration_seconds'),
                   fingerprint_hash=result.get('fingerprint_hash', ''))
        rows.append(row)
    return rows


async def run_managed_job(spec: JobSpec, *, steps: int, bus_publish: Any | None = None) -> SimulationResult:
    if spec.run_root is None:
        return await run_one(spec, steps=steps, bus_publish=bus_publish)
    path = _path(spec)
    path.parent.mkdir(parents=True, exist_ok=True)
    inputs = digest({'run_id': spec.run_id, 'project': spec.project, 'name': spec.experiment_id,
                     'config': spec.config, 'seed': spec.seed, 'steps': steps,
                     'backend': get_settings().mars_execution_backend})
    if path.exists():
        saved = json.loads(path.read_text())
        if saved.get('inputs') != inputs:
            raise ValueError('该作业的输入已变化；请核对后创建新的执行尝试')
        if saved.get('status') != 'completed':
            raise ValueError('已有作业未完成或状态不明；请检查真实收据后显式重试阶段，禁止重复启动')
        result = SimulationResult(**saved['result'])
        if (result.status != 'completed' or result.is_mock or result.run_id != spec.run_id
                or result.experiment_id != spec.experiment_id or not result.metrics
                or not all(math.isfinite(value) for value in result.metrics.values())):
            raise ValueError('已完成作业记录无法校验')
        if not saved.get('evidence'):
            raise ValueError('已完成作业缺少证据')
        for evidence in saved['evidence']:
            target = Path(evidence['path'])
            if not target.resolve().is_relative_to(spec.run_root.resolve()) or _hash(target) != evidence['sha256']:
                raise ValueError('已完成作业证据缺失或被修改，不自动重跑')
        if bus_publish is not None:
            await bus_publish(f'run.{spec.run_id}.experiment.{spec.experiment_id}', {
                'event': 'execution.job_reused', 'experiment_id': spec.experiment_id,
                'message': '已核验真实收据，沿用已完成作业。'})
        return result
    for row in job_states(spec.run_root):
        if row['experiment_id'] == spec.experiment_id and row['status'] == 'running':
            raise ValueError('原作业仍运行或状态不明；核对原收据前禁止开启新尝试')
    claim = path.with_suffix('.claim')
    try:
        with claim.open('x'):
            pass
    except FileExistsError as exc:
        raise ValueError('此作业已被领取；状态不明时禁止重复启动') from exc
    state: dict[str, Any] = {'schema': 'execution.job', 'run_id': spec.run_id, 'project': spec.project,
        'experiment_id': spec.experiment_id, 'attempt': spec.config.get('attempt', 1), 'inputs': inputs,
        'status': 'running', 'updated_at': datetime.now(timezone.utc).isoformat()}
    atomic_write_json(path, state)
    try:
        result = await run_one(spec, steps=steps, bus_publish=bus_publish)
        state['status'] = result.status
        state['result'] = asdict(result)
        if result.status == 'completed':
            state['evidence'] = _evidence(spec, result)
        else:
            state['error'] = '作业失败，请检查原始日志与收据；不会自动修改实验参数。'
        return result
    except asyncio.CancelledError:
        state.update(status='interrupted', error='作业已中断；检查清理结果后显式恢复。')
        raise
    except Exception as exc:
        state.update(status='failed', error=str(exc))
        raise
    finally:
        state['updated_at'] = datetime.now(timezone.utc).isoformat()
        atomic_write_json(path, state)


def stopped_execution_retry_blocker(run_root: Path) -> str:
    """Called only after the owner acknowledged complete process cleanup."""
    try:
        rows = job_states(run_root)
    except (OSError, ValueError):
        return '作业记录无法校验；请先核对原始收据。'
    if any(row['status'] not in {'completed', 'failed', 'interrupted'} for row in rows):
        return '存在运行中或状态不明的作业；禁止自动重启。'
    return ''
