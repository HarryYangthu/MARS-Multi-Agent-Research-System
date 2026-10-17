"""Import explicit trainer CSV schemas without inferring metrics or missing points."""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

from app.execution.tensorboard_writer import ScalarWriter

_EPOCH_TAGS = {'epoch': 'runtime/epoch', 'loss_mse': 'train/loss', 'lr': 'train/learning_rate',
         'train_PIM': 'train/PIM_db', 'train_RES': 'train/RES_db',
         'validation_PIM': 'validation/PIM_db', 'validation_RES': 'validation/RES_db'}
_CIRCLE_TAGS = {'circle': 'runtime/circle', 'mean_loss_sum': 'train/mean_loss_sum',
                'lr': 'train/learning_rate', 'train_PIM_native': 'train/native_PIM_db',
                'train_RES_native': 'train/native_RES_db'}
_TAGS = {**_EPOCH_TAGS, **_CIRCLE_TAGS}


def training_csv_rows(path: Path) -> list[dict[str, float]]:
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('training.csv exceeds the visualization import size limit')
    text = path.read_text(encoding='utf-8-sig')
    # The trainer can still be flushing a row. Import only complete lines.
    reader = csv.DictReader(text[:text.rfind('\n') + 1].splitlines())
    columns = set(reader.fieldnames or [])
    if {'updates', *_EPOCH_TAGS}.issubset(columns):
        tags = _EPOCH_TAGS
    elif {'updates', *_CIRCLE_TAGS, 'rx_centers_mhz', 'err_centers_mhz'}.issubset(columns):
        # These measurements use the trainer's native frequency windows. They
        # must never be presented as fixed-window validation/test results.
        tags = _CIRCLE_TAGS
    else:
        raise ValueError('unsupported training.csv columns; no curve was inferred')
    rows = []
    for raw in reader:
        if None in raw or any(value is None for value in raw.values()):
            continue
        row = {key: float(raw[key]) for key in ('updates', *tags) if raw[key].strip()}
        step = row.get('updates', float('nan'))
        if not math.isfinite(step) or step < 0 or not step.is_integer():
            raise ValueError('training.csv contains an invalid optimizer update index')
        rows.append(row)
    return rows


def import_training_csvs(root: Path, attempt: Path, group: Path, identity: str,
                         request: dict[str, Any], state: dict[str, Any], report: dict[str, Any]) -> None:
    for path in sorted(attempt.rglob('training.csv')):
        if not path.resolve().is_relative_to(attempt.resolve()):
            report['warnings'].append('跳过运行目录之外的训练曲线链接')
            continue
        key = path.parent.relative_to(attempt).as_posix()
        previous = state['models'].get(key, {'format': 'training_csv_v1', 'updates': -1})
        if previous.get('format') != 'training_csv_v1':
            continue  # A legacy history importer already owns this model.
        try:
            rows = training_csv_rows(path)
        except (OSError, ValueError) as exc:
            report['warnings'].append(f'训练曲线未接入：{key}：{exc}')
            continue
        if not rows:
            continue
        destination = group / key
        with ScalarWriter(destination) as writer:
            for row in rows:
                step = int(row['updates'])
                if step <= previous['updates']:
                    continue
                for column, tag in _TAGS.items():
                    writer.scalar(tag, row.get(column), step)
                previous['updates'] = step
            if not previous.get('provenance'):
                writer.text('run/provenance', json.dumps({'source': path.relative_to(root).as_posix(),
                    'run_id': request['run_id'], 'experiment_id': request.get('experiment_id'),
                    'mode': 'reconstructed_from_saved_csv',
                    'notice': '仅导入真实保存的CSV行；缺失轮次不补造，事件时间为导入时间。'}, ensure_ascii=False))
                previous['provenance'] = True
        state['models'][key] = previous
        report['models'].append({'id': identity + '/' + key, 'name': key,
            'label': str(request.get('experiment_id', key)), 'is_baseline': False, 'native': False,
            'scalar_run': destination.relative_to(root).as_posix(),
            'histogram_run': destination.relative_to(root).as_posix(),
            'image_run': (group / 'figures').relative_to(root).as_posix(),
            'image_tags': [], 'comparison_tags': [], 'attempt': identity,
            'training_steps': previous['updates'],
            'validation_points': sum(math.isfinite(row.get('validation_RES', float('nan'))) for row in rows)})
