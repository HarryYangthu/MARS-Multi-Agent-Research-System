"""Executable handoff semantics shared by generated and human-approved artifacts.

Schema parsing deliberately remains compatible with historical documents. Admission
of new work is stricter: a runnable matrix has concrete seeds and budget units.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from app.harness.schema.frontmatter_parser import parse


def document_metadata(text: str) -> dict[str, Any]:
    if text.startswith('[upstream artifact: '):
        text = text.split('\n', 1)[1]
    return parse(text).metadata


def document_hash(text: str) -> str:
    if text.startswith('[upstream artifact: '):
        text = text.split('\n', 1)[1]
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def budget(config: dict[str, Any]) -> tuple[str, int]:
    if 'budget_steps' in config:
        if 'max_iters' in config or config.get('budget_unit', 'steps') != 'steps':
            raise ValueError('steps 与 epochs 预算混用；不得把训练步数换成训练轮数')
        value = config['budget_steps']
        unit = 'steps'
    elif config.get('budget_unit') == 'epochs' and 'max_iters' in config:
        value, unit = config['max_iters'], 'epochs'
    else:
        raise ValueError('缺少明确预算：steps 使用 budget_steps，epochs 使用 budget_unit 与 max_iters')
    if type(value) is not int or value < 1:
        raise ValueError('预算必须是正整数')
    return unit, value


def experiment_errors(metadata: dict[str, Any]) -> list[str]:
    rows = metadata.get('ablations')
    if not isinstance(rows, list) or not rows:
        return ['/ablations: 缺少可执行实验矩阵']
    errors: list[str] = []
    names: set[str] = set()
    paths: set[str] = set()
    for index, row in enumerate(rows):
        prefix = f'/ablations/{index}'
        if not isinstance(row, dict) or not isinstance(row.get('config'), dict):
            errors.append(prefix + ': 缺少配置对象')
            continue
        name = row.get('name')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', name):
            errors.append(prefix + '/name: 名称必须是安全且唯一的文件标识')
        else:
            path = re.sub(r'[^A-Za-z0-9_-]', '_', name)
            if name in names or path in paths:
                errors.append(prefix + '/name: 实验名称或输出路径重复')
            names.add(name)
            paths.add(path)
        config = row['config']
        if type(config.get('seed')) is not int or config['seed'] < 0:
            errors.append(prefix + '/config/seed: 请从真实基线证据解析为非负整数，不能写“同基线种子”')
        try:
            budget(config)
        except ValueError as exc:
            errors.append(prefix + '/config: ' + str(exc))
    if metadata.get('estimated_runs') != len(rows):
        errors.append('/estimated_runs: 必须与批准矩阵数量一致')
    return errors


def execution_handoff_errors(plan_text: str, coding: dict[str, Any], execution: dict[str, Any]) -> list[str]:
    """An execution document may not introduce a second experiment protocol."""
    errors = handoff_errors(plan_text, coding)
    if errors:
        return errors
    rows = document_metadata(plan_text)['ablations']
    bindings = {job['name']: job['config'] for job in coding['execution_jobs']}
    expected = {row['name']: {**row['config'], **bindings[row['name']]} for row in rows}
    jobs = execution.get('planned_experiments')
    if not isinstance(jobs, list) or len(jobs) != len(expected):
        return ['/planned_experiments: 执行清单数量与批准实验不一致']
    seen: set[str] = set()
    for index, job in enumerate(jobs):
        if not isinstance(job, dict) or not isinstance(job.get('name'), str):
            errors.append(f'/planned_experiments/{index}: 缺少实验身份')
            continue
        name = job['name']
        if name not in expected or name in seen:
            errors.append(f'/planned_experiments/{index}: 执行清单名称与批准实验不一致')
        elif job.get('config') != expected[name]:
            errors.append(f'/planned_experiments/{index}/config: 执行清单改写了批准参数或编码入口；请回到对应阶段修正')
        seen.add(name)
    return errors


def handoff_errors(plan_text: str, coding: dict[str, Any]) -> list[str]:
    plan = document_metadata(plan_text)
    errors = experiment_errors(plan)
    if coding.get('project') != plan.get('project'):
        errors.append('/project: 编码交付与实验方案不属于同一项目')
    if coding.get('experiment_plan_sha256') != document_hash(plan_text):
        errors.append('/experiment_plan_sha256: 编码交付必须绑定当前批准方案的完整 SHA-256，期望 ' + document_hash(plan_text) + '；方案更新后重新校验')
    rows = plan.get('ablations', [])
    jobs = coding.get('execution_jobs')
    if not isinstance(jobs, list) or not jobs:
        return errors + ['/execution_jobs: 每组实验必须交付明确的实际入口和配置绑定']
    names = [row.get('name') for row in rows if isinstance(row, dict)]
    job_names = [job.get('name') for job in jobs if isinstance(job, dict)]
    if errors or not all(isinstance(name, str) for name in job_names):
        return errors + ['/execution_jobs: 请先完成有效实验矩阵与逐项配置绑定']
    if len(jobs) != len(names) or len(job_names) != len(jobs) or set(job_names) != set(names):
        return errors + ['/execution_jobs: 实验名称和数量必须逐项对应批准矩阵']
    by_name = {row['name']: row['config'] for row in rows if isinstance(row, dict) and isinstance(row.get('config'), dict)}
    for index, job in enumerate(jobs):
        config = job.get('config')
        if not isinstance(config, dict):
            errors.append(f'/execution_jobs/{index}/config: 缺少配置对象')
            continue
        if not (config.get('command_id') or config.get('entrypoint') and config.get('config_path')):
            errors.append(f'/execution_jobs/{index}/config: 缺少登记命令或实际入口与配置文件')
        approved = by_name.get(job['name'], {})
        for key, value in config.items():
            if key in approved and value != approved[key]:
                errors.append(f'/execution_jobs/{index}/config/{key}: 编码交付改写了批准参数')
        merged = {**approved, **config}
        if type(merged.get('seed')) is not int or merged['seed'] < 0:
            errors.append(f'/execution_jobs/{index}/config/seed: 实际种子必须是非负整数')
        try:
            if budget(merged) != budget(approved):
                errors.append(f'/execution_jobs/{index}/config: 实际预算与批准方案不一致')
        except ValueError as exc:
            errors.append(f'/execution_jobs/{index}/config: ' + str(exc))
    return errors
