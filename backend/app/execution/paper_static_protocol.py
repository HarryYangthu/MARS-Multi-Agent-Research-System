"""Inspect the real static trainer's epoch/step contract without executing it."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _positive_integer(value: Any, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} 必须是正整数")
    return value


def static_training_protocol(config_path: Path, overrides: list[str], *,
                             unit: str, count: int) -> dict[str, Any]:
    """Mirror base/scenario precedence for the legacy trainer's loop fields."""
    raw: dict[str, Any] = {}
    base = config_path.with_name('base.yaml')
    for path in ([base, config_path] if base.is_file() and base != config_path else [config_path]):
        value = yaml.safe_load(path.read_text())
        if not isinstance(value, dict):
            raise ValueError('训练配置文件必须是对象')
        raw.update(value)
    for item in overrides:
        if item != '--set':
            key, value = item.split('=', 1)
            if '.' not in key:
                raw[key] = yaml.safe_load(value)
    total = _positive_integer(raw.get('Etotal'), 'Etotal') * _positive_integer(raw.get('Epoch'), 'Epoch')
    _positive_integer(count, '训练预算')
    if unit not in ('steps', 'epochs'):
        raise ValueError('训练预算单位必须是 steps 或 epochs')
    if unit == 'epochs' and count > total:
        raise ValueError(f'批准预算为 {count} 个完整训练轮次，但配置 Etotal × Epoch 仅允许 {total} 轮；请修正配置，不能提前结束后记为完成')
    enabled = bool(raw.get('if_scheduler', False))
    warning = ('本组按参数更新次数训练，学习率调度也按更新次数步进；'
               f'{count} 次更新不是 {count} 个完整训练轮次，不能当作完整基线复现结果。') if unit == 'steps' else ''
    return {'budget_unit': unit, 'requested_budget': count, 'configured_total_epochs': total,
            'training_epochs': count if unit == 'epochs' else None,
            'scheduler_unit': unit if enabled else 'disabled', 'budget_warning': warning}


def static_summary_errors(summary: dict[str, Any], *, unit: str, count: int,
                          seed: int | None) -> list[str]:
    """Completion is bound to observed training, for both supported axes."""
    errors: list[str] = []
    field = 'optimizer_steps' if unit == 'steps' else 'epochs'
    if type(summary.get(field)) is not int or summary[field] != count:
        errors.append(f'实际 {field} 与批准的 {count} {unit} 不一致，不能记为训练完成')
    if seed is not None and (type(summary.get('seed')) is not int or summary['seed'] != seed):
        errors.append('实际训练随机种子与批准方案不一致')
    return errors
