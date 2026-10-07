"""Check coding bindings against approved semantics and actual host inputs."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.harness.schema.experiment_contract import budget, document_metadata, handoff_errors
from app.harness.tools.config import load_execution_config
from app.harness.tools.project_repo import load_project_repo
from app.settings import get_settings


def coding_handoff_errors(plan_text: str, coding: dict[str, Any], *, project: str) -> list[str]:
    errors = handoff_errors(plan_text, coding)
    if errors:
        return errors
    plan = document_metadata(plan_text)
    rows = {row['name']: row['config'] for row in plan['ablations']}
    backend = get_settings().mars_execution_backend
    policy = load_execution_config()['execution']
    for index, job in enumerate(coding['execution_jobs']):
        config = {**rows[job['name']], **job['config']}
        prefix = f'/execution_jobs/{index}/config'
        if config.get('backend', backend) != backend:
            errors.append(prefix + ': 编码入口与当前执行方式不同，请在编码交付前核对')
            continue
        try:
            if backend == 'pim_cpu' and budget(config)[0] != 'steps':
                raise ValueError('pim_cpu 只支持 steps 预算，不能把 epochs 当作训练步数')
            if backend == 'paper_static':
                from app.execution.paper_static_adapter import approved_config_path, _override_args
                root = load_project_repo(project).root.resolve()
                path = approved_config_path(config, policy.get('paper_static', {}), root)
                if not (root / 'train_static.py').is_file():
                    raise ValueError('交付的训练入口不存在')
                raw = yaml.safe_load(path.read_text())
                if not isinstance(raw, dict):
                    raise ValueError('实验配置文件必须是对象')
                overrides = _override_args(config, policy.get('paper_static', {}))
                effective = {part.split('=', 1)[0]: part.split('=', 1)[1]
                             for part in overrides if part != '--set'}
                seed: Any = effective.get('seed', raw.get('seed'))
                if type(seed) is not int or seed != config['seed']:
                    # A --set value is encoded as text; validate its exact numeric meaning.
                    if not isinstance(seed, str) or seed != str(config['seed']):
                        raise ValueError('实际配置文件的随机种子与批准方案不一致')
                unit, count = budget(config)
                if unit == 'epochs':
                    epochs: Any = effective.get('Epoch', raw.get('Epoch', raw.get('epochs')))
                    total: Any = effective.get('Etotal', raw.get('Etotal', 1))
                    if isinstance(epochs, str) and epochs.isdigit():
                        epochs = int(epochs)
                    if isinstance(total, str) and total.isdigit():
                        total = int(total)
                    if type(epochs) is not int or type(total) is not int or epochs < 1 or total < 1:
                        raise ValueError('实际训练轮数无法核验')
                    if epochs * total < count:
                        raise ValueError('配置文件在批准轮数之前结束，实际预算无法落实')
            elif backend == 'local_command':
                from app.harness.tools.execution.local_command import LocalCommandJob, _command
                command_id = config.get('command_id')
                if not isinstance(command_id, str) or not command_id:
                    raise ValueError('当前执行方式要求交付实际 command_id')
                _command(LocalCommandJob(run_id='handoff-check', experiment_id=job['name'],
                    project=project, run_root=Path('.'), command_id=command_id), 'execution.simulation_runner')
        except (OSError, ValueError, RuntimeError) as exc:
            errors.append(prefix + ': ' + str(exc))
    return errors
