"""Prepare a zero-code handoff from current registered execution capability."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import time
from typing import Any, TypeVar

from app.agents.base import Artifact, RunRequest
from app.bridge.coding_approval import coding_candidate_errors
from app.harness.agent_loop.trace import digest
from app.harness.persistence import atomic_write_json
from app.harness.schema.frontmatter_parser import dumps
from app.harness.tools.execution.command_preflight import run_command_preflight
from app.harness.tools.execution.local_command import LocalCommandJob
from app.storage.run_store import RunHandle

_T = TypeVar('_T')


async def _bounded_check(fn: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
    """Retain the checkout until the real bounded worker exits on cancellation."""
    worker = asyncio.create_task(asyncio.to_thread(fn, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if worker.done() and not worker.cancelled():
            worker.exception()  # Consume failure while preserving the stop request.
        raise


async def prepare_reused_artifact(run: RunHandle, request: RunRequest) -> Artifact | None:
    """A declined reuse is ordinary Coding, never an invented successful check."""
    requested = request.extra.get('existing_execution_job')
    if not isinstance(requested, dict):
        return None
    from app.bridge.coding_reuse_evidence import inspect_reusable_execution
    from app.bridge.registered_execution_preparation import (
        current_preflight_inputs_unchanged, inspect_registered_execution, verified_current_preflight,
    )
    # Public A1 requests depend on today's registered capability, not on an old
    # completed simulation. Explicit internal history audits retain their path.
    historical = 'source_run_id' in requested
    inspect = inspect_reusable_execution if historical else inspect_registered_execution
    started = time.monotonic()
    evidence = await _bounded_check(inspect, run, requested)
    if evidence is None:
        return None
    invocation = str(request.extra['invocation_id'])
    sink = request.progress_sink
    if sink is not None:
        await sink({'kind': 'action', 'phase': 'reuse',
            'message': '已有模型的登记入口已确认，正在真实核对本次环境、数据、训练单位和种子。'})
    from app.bridge.agent_runner import _load_selected_data_source
    selected = _load_selected_data_source(run)
    jobs = evidence['execution_jobs']
    if not isinstance(jobs, list) or len(jobs) != 1:
        return None
    row = jobs[0]
    config = dict(row['config'])
    for source_key, target_key in (('stored_path', 'data_path'), ('id', 'data_source_id'),
                                   ('fs_mhz', 'fs_mhz'), ('channel_count', 'channel_count')):
        if selected.get(source_key) not in (None, ''):
            config[target_key] = selected[source_key]
    job = LocalCommandJob(run_id=run.run_id, experiment_id=str(row['name']), project=run.project,
        run_root=run.root, config=config, seed=int(config['seed']), steps=int(config['max_iters']),
        command_id=str(config['command_id']))
    token = digest({'evidence': evidence, 'requested': requested, 'current_job': config})
    try:
        checked = await _bounded_check(run_command_preflight, job, token, timeout_budget=45)
    except (OSError, ValueError, RuntimeError):
        run.write_event('agent_events', {'event': 'coding.execution_reuse_declined', 'agent': 'coding',
            'reason': '当前执行预检不可用，继续完整编码准备。', 'invocation_id': invocation})
        return None
    if checked.get('status') != 'passed' or not checked.get('input_files'):
        run.write_event('agent_events', {'event': 'coding.execution_reuse_declined', 'agent': 'coding',
            'reason': '本次环境、数据或预算未通过真实预检，继续完整编码准备。',
            'invocation_id': invocation, 'preflight_status': checked.get('status')})
        return None
    if not historical and not await _bounded_check(verified_current_preflight, run, evidence, checked, config):
        run.write_event('agent_events', {'event': 'coding.execution_reuse_declined', 'agent': 'coding',
            'reason': '真实预检未明确验证本次预算单位、种子及代码输入，继续完整编码准备。',
            'invocation_id': invocation})
        return None
    # A preflight may run concurrently with edits outside the isolated task.
    # Revalidate immutable source and current bytes before accepting the recipe.
    after = await _bounded_check(inspect, run, requested)
    if (after is None or digest(after) != digest(evidence)
            or digest(_load_selected_data_source(run)) != digest(selected)
            or not await _bounded_check(current_preflight_inputs_unchanged, checked)):
        run.write_event('agent_events', {'event': 'coding.execution_reuse_declined', 'agent': 'coding',
            'reason': '核对期间复用证据或代码发生变化，继续完整编码准备。', 'invocation_id': invocation})
        return None
    validation_path = Path(str(checked['receipt_path']))
    if not validation_path.resolve().is_relative_to(run.root.resolve()):
        raise ValueError('Current reuse validation receipt escapes its task')
    receipt_ref = f'coding/reuse/{invocation}.json'
    receipt: dict[str, Any] = {'schema': 'coding.execution_reuse.v1', 'run_id': run.run_id, 'project': run.project,
        'invocation_id': invocation, 'mode': ('host_verified_existing_execution' if historical
            else 'host_validated_registered_execution'), 'model_calls': 0,
        'files_changed': [], 'source': evidence, 'requested': requested,
        'current_preflight': {'ref': validation_path.relative_to(run.root).as_posix(),
            'sha256': hashlib.sha256(validation_path.read_bytes()).hexdigest(),
            'status': checked['status'], 'input_files': checked['input_files']},
        'prepared_seconds': time.monotonic() - started,
        'created_at': datetime.now(timezone.utc).isoformat(), 'approved': False,
        'training_started': False, 'full_test_suite_rerun': False}
    metadata = {'schema': 'code_spec.v1', 'project': run.project, 'agent': 'coding',
        'target_lang': evidence['target_lang'], 'files_changed': [], 'new_dependencies': [],
        'baseline_compat': {'preserved': True,
            'rationale': '使用当前独立代码目录中已经存在的模型和配置；本次未修改代码或科学配置。'},
        'test_coverage': {'unit_tests_added': 0, 'baseline_smoke_test': 'skipped'},
        'execution_jobs': [{'name': row['name'], 'config': config}],
        'execution_reuse': {'mode': receipt['mode'], 'receipt_ref': receipt_ref,
            'source_run_id': evidence.get('source_run_id'), 'model_calls': 0,
            'current_preflight_ref': receipt['current_preflight']['ref'], 'full_tests': 'not_rerun'}}
    body = ('# 复用已有模型的执行交付\n\n'
        f'本次使用已存在的入口 `{config["entrypoint"]}` 和配置 `{config["config_path"]}`，'
        f'预算为 {config["max_iters"]} {config["budget_unit"]}，种子 {config["seed"]}。'
        '本次代码改动为零，执行清单由宿主按当前登记能力组装，未调用编码模型。\n\n'
        + (f'历史复用来源：`{evidence["source_run_id"]}`。' if historical else '无需已有仿真结果作为启动前置。')
        + '当前独立代码目录、登记命令和本次输入绑定已核验。'
        '数据使用当前项目已选择并冻结的文件，不沿用历史输出目录。\n\n'
        f'本次环境、数据、配置和预算已通过真实无训练预检：`{receipt["current_preflight"]["ref"]}`。'
        '本次没有重跑完整测试套件，也没有复用历史训练成功标记或性能指标。\n\n'
        '本交付仍按当前审核模式审核，之后核对仿真配置；训练尚未开始。'
        '仿真将生成当前任务的新收据、日志、指标、权重及原生可视化，不复用旧测量结果。\n\n'
        f'完整复用核验记录：`{receipt_ref}`。\n')
    artifact = Artifact(text=dumps(metadata, body), schema_id='code_spec.v1', metadata=metadata, body=body)
    errors = coding_candidate_errors(run, artifact.text)
    if errors:
        run.write_event('agent_events', {'event': 'coding.execution_reuse_declined', 'agent': 'coding',
            'reason': '当前交付一致性校验未通过，继续完整编码准备。', 'invocation_id': invocation})
        return None
    atomic_write_json(run.root / receipt_ref, receipt)
    run.write_event('agent_events', {'event': 'coding.execution_delivery_reused', 'agent': 'coding',
        'invocation_id': invocation, 'source_run_id': evidence.get('source_run_id'), 'receipt_ref': receipt_ref,
        'model_calls': 0, 'prepared_seconds': receipt['prepared_seconds'],
        'message': '已有模型通过本次真实预检并生成执行清单，零代码改动，无编码模型调用。'})
    if sink is not None:
        await sink({'kind': 'action', 'phase': 'reuse',
            'message': '本次执行清单已准备，代码未改动；真实预检通过，等待按当前设置审核。'})
    return artifact
