"""Validate execution delivery semantics against actual bound host inputs."""
from __future__ import annotations
from pathlib import Path
from typing import Any
import yaml
from app.harness.schema.experiment_contract import budget, delivery_experiments
from app.harness.tools.config import load_execution_config
from app.harness.tools.project_repo import load_project_repo
from app.settings import get_settings


def execution_delivery_context() -> dict[str, Any]:
    """Expose the same host bindings the coding handoff validator consumes."""
    backend = get_settings().mars_execution_backend
    policy = load_execution_config()['execution']
    rows = policy.get('local_commands', [])
    commands = [{key: row[key] for key in ('id', 'argv', 'required_metrics') if key in row}
                for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    return {
        'backend': backend,
        'local_commands': commands if backend == 'local_command' else [],
        'paper_static': policy.get('paper_static', {}) if backend == 'paper_static' else {},
        'instruction': (
            '当前执行方式为 local_command：每项 execution_jobs.config 必须填写上述登记命令的 id '
            '作为 command_id；只有 entrypoint/config_path 不足以启动。argv 由宿主执行，不得自行替换。'
            '命令从 MARS_JOB_REQUEST 读取本项配置和预算，产出 MARS_RESULT_PATH 与真实证据。'
            '若没有登记命令，明确说明执行配置未就绪，不得捏造 command_id 或声称可执行。'
            if backend == 'local_command' else
            '按当前执行适配器交付实际入口、配置、种子和预算；不能替换批准的训练条件。'
        ),
    }

def coding_handoff_errors(plan_text: str, coding: dict[str, Any], *, project: str,
                          plan_required: bool = False) -> list[str]:
    try:
        jobs = delivery_experiments(plan_text, coding, project=project, plan_required=plan_required)
    except ValueError as exc:
        return [str(exc)]
    errors: list[str] = []
    backend = get_settings().mars_execution_backend
    policy = load_execution_config()['execution']
    for index, job in enumerate(jobs):
        config = job['config']
        prefix = f'/execution_jobs/{index}/config'
        if config.get('backend', backend) != backend:
            errors.append(prefix + ': 编码入口与当前执行方式不同，请在编码交付前核对')
            continue
        try:
            if backend == 'pim_cpu' and budget(config)[0] != 'steps':
                raise ValueError('pim_cpu 只支持 steps 预算，不能把 epochs 当作训练步数')
            if backend == 'paper_static':
                from app.harness.tools.execution.paper_config import approved_config_path, override_args
                root = load_project_repo(project).root.resolve()
                path = approved_config_path(config, policy.get('paper_static', {}), root)
                if config.get('entrypoint') != 'train_static.py':
                    raise ValueError('当前训练适配器的入口是 train_static.py，交付入口不一致')
                if not (root / 'train_static.py').is_file():
                    raise ValueError('交付的训练入口不存在')
                raw = yaml.safe_load(path.read_text())
                if not isinstance(raw, dict):
                    raise ValueError('实验配置文件必须是对象')
                overrides = override_args(config, policy.get('paper_static', {}))
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
                    rows = policy.get('local_commands', [])
                    ids = [str(row['id']) for row in rows if isinstance(row, dict) and row.get('id')] if isinstance(rows, list) else []
                    raise ValueError('当前执行方式 local_command 要求交付实际 command_id；可用登记 ID：'
                                     + (', '.join(ids) if ids else '无，请先配置真实执行命令'))
                _command(LocalCommandJob(run_id='handoff-check', experiment_id=job['name'],
                    project=project, run_root=Path('.'), command_id=command_id), 'execution.simulation_runner')
        except (OSError, ValueError, RuntimeError) as exc:
            errors.append(prefix + ': ' + str(exc))
    return errors
