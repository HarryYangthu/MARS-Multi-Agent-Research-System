"""Validate execution delivery semantics against actual bound host inputs."""
from __future__ import annotations
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4
import yaml
from app.harness.schema.experiment_contract import budget, delivery_experiments
from app.harness.tools.config import load_execution_config
from app.harness.tools.project_repo import load_project_repo
from app.settings import get_settings


def execution_delivery_context(*, project: str | None = None) -> dict[str, Any]:
    """Expose the same host bindings the coding handoff validator consumes."""
    backend = get_settings().mars_execution_backend
    policy = load_execution_config()['execution']
    rows = policy.get('local_commands', [])
    if project is not None:
        from app.harness.tools.execution.local_command import command_supports_project
        rows = [row for row in rows if isinstance(row, dict) and command_supports_project(row, project)] if isinstance(rows, list) else []
    commands = [{key: row[key] for key in ('id', 'label', 'argv', 'required_metrics', 'project_ids',
                'entrypoints', 'config_paths', 'consumes_data_path', 'validation') if key in row}
                for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    from app.harness.tools.execution.local_command import local_command_protocol
    from app.harness.tools.execution.command_preflight import validation_protocol
    return {
        'backend': backend,
        'local_commands': commands if backend == 'local_command' else [],
        'paper_static': policy.get('paper_static', {}) if backend == 'paper_static' else {},
        'protocol': local_command_protocol() if backend == 'local_command' else {},
        'validation_protocol': validation_protocol() if backend == 'local_command' else {},
        'instruction': (
            '当前执行方式为 local_command：每项 execution_jobs.config 必须填写上述登记命令的 id '
            '作为 command_id；只有 entrypoint/config_path 不足以启动。argv 由 MARS 执行，不得自行替换。'
            '命令从 MARS_JOB_REQUEST 读取本项配置和预算，产出 MARS_RESULT_PATH 与真实证据。'
            '登记命令的项目、入口和配置能力必须与本项匹配；旧命令不能作为其他模型的通用入口。'
            '声明 consumes_data_path 时必须消费 MARS 交付的数据；验证模式只检查，不训练。'
            '若没有登记命令，明确说明执行配置未就绪，不得捏造 command_id 或声称可执行。'
            '登记入口尚不存在时，应在允许修改范围内创建该交付文件，不将其误报为用户缺失的输入。'
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
                from app.harness.tools.git_branch import current_git_branch
                from app.harness.runtime.project_scope import current_project_scope
                branch = current_git_branch(project)
                scope = current_project_scope(project)
                run_id = branch.run_id if branch is not None else scope.run_id if scope is not None else 'handoff-check'
                _command(LocalCommandJob(run_id=run_id, experiment_id=job['name'],
                    project=project, run_root=scope.run_root if scope is not None else Path('.'),
                    command_id=command_id, config=config), 'execution.simulation_runner')
        except (OSError, ValueError, RuntimeError) as exc:
            errors.append(prefix + ': ' + str(exc))
    return errors


async def coding_handoff_preflight_errors(plan_text: str, coding: dict[str, Any], *, project: str,
                                         run_id: str, run_root: Path, attempt: int = 1,
                                         plan_required: bool = False) -> list[str]:
    """Feed real registered no-training checks back before Coding is delivered.

    This is not artifact approval, execution confirmation or a training
    receipt. No source configuration or scientific default is rewritten.
    """
    errors = coding_handoff_errors(plan_text, coding, project=project, plan_required=plan_required)
    if errors or get_settings().mars_execution_backend != 'local_command':
        return errors
    from app.harness.persistence import atomic_write_json
    from app.harness.runtime.project_scope import current_project_scope
    from app.harness.tools.execution.command_preflight import bounded_error, run_command_preflight, validation_configuration
    from app.harness.tools.execution.local_command import LocalCommandJob, _registered_command
    from app.harness.tools.git_branch import current_git_branch
    try:
        jobs = delivery_experiments(plan_text, coding, project=project, plan_required=plan_required)
        configured = [validation_configuration(_registered_command(LocalCommandJob(
            run_id=run_id or 'handoff-check', experiment_id=row['name'], project=project,
            run_root=run_root, config=row['config'], command_id=str(row['config']['command_id'])))) is not None
            for row in jobs]
        if (not run_id or not run_root.is_absolute() or run_root.is_symlink()
                or not run_root.is_dir() or type(attempt) is not int or attempt < 1):
            if not any(configured):
                # Legacy commands without a checker retain static admission;
                # lack of task context must not manufacture a passed check.
                return []
            raise ValueError('编码交付预检需要当前任务的真实 run_id、run_root 和 attempt')
        root = run_root.resolve(strict=True)
        current_git_branch(project, run_id)  # Existing capability validates the bound checkout identity.
        scope = current_project_scope(project, run_id)
        if scope is not None and scope.run_root != root:
            raise ValueError('编码交付预检目录与当前任务能力不一致')
        selected_path = root / 'input/selected_data_source.json'
        selected = json.loads(selected_path.read_text()) if any(configured) and selected_path.is_file() else {}
        if not isinstance(selected, dict) or selected.get('project', project) != project:
            raise ValueError('本任务冻结的数据选择不属于当前项目')
    except (OSError, ValueError, RuntimeError) as exc:
        return ['/execution_jobs: ' + bounded_error(str(exc))]
    identity = {'project': project, 'run_id': run_id, 'attempt': attempt, 'coding': coding}
    token = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    records: list[dict[str, Any]] = []
    for index, row in enumerate(jobs):
        prefix = f'/execution_jobs/{index}/config'
        record: dict[str, Any] = {'name': row['name'], 'status': 'unverified', 'configured': False}
        records.append(record)
        try:
            if not configured[index]:
                record['reason'] = '未登记无训练 checker；仅完成静态交接核对，不能称为运行预检通过。'
                continue
            config = {**row['config'], 'label': row['name'], 'attempt': attempt}
            # Same frozen host bindings as execution preparation; these never
            # replace model, optimizer, training-unit or seed declarations.
            for source, target in (('stored_path', 'data_path'), ('id', 'data_source_id'),
                                   ('fs_mhz', 'fs_mhz'), ('channel_count', 'channel_count')):
                if selected.get(source) not in (None, ''):
                    config[target] = selected[source]
            _unit, count = budget(config)
            seed = config.get('seed')
            if type(seed) is not int:
                raise ValueError('交付预检需要已声明的整数 seed；不能补写默认种子')
            job = LocalCommandJob(run_id=run_id, experiment_id=row['name'], project=project,
                run_root=root, config=config, seed=seed, steps=count, command_id=str(config['command_id']))
            record['configured'] = True
            worker = asyncio.create_task(asyncio.to_thread(run_command_preflight, job, token))
            try:
                checked = await asyncio.shield(worker)
            except asyncio.CancelledError:
                # The checker owns a bounded process. Keep its checkout grant
                # until it exits, including on a repeated stop request.
                while not worker.done():
                    try:
                        await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if worker.done() and not worker.cancelled():
                    worker.exception()
                raise
            record.update(status=checked.get('status', 'unverified'), reused=bool(checked.get('reused')))
            receipt = Path(str(checked.get('receipt_path', '')))
            if not receipt.is_absolute() or not receipt.is_file() or not receipt.resolve().is_relative_to(root):
                raise ValueError('登记 checker 未返回当前任务内的真实验证收据')
            ref = receipt.relative_to(root).as_posix()
            record.update(receipt_ref=ref, receipt_sha256=checked.get('receipt_sha256'))
            if checked.get('status') != 'passed':
                detail = bounded_error(str(checked.get('error') or '登记的无训练预检未通过'))
                record['error'] = detail
                errors.append(prefix + ': 真实无训练交付预检失败：' + detail + f'；证据：{ref}')
        except (OSError, ValueError, RuntimeError) as exc:
            detail = bounded_error(str(exc))
            record.update(status='failed', error=detail)
            errors.append(prefix + ': ' + detail)
    status = 'failed' if errors else 'passed' if all(r['status'] == 'passed' for r in records) else 'unverified'
    atomic_write_json(root / 'coding/delivery_preflight' / (uuid4().hex + '.json'), {
        'schema': 'coding.delivery_preflight.v1', 'project': project, 'run_id': run_id, 'attempt': attempt,
        'candidate_sha256': token, 'status': status, 'training_started': False, 'approved': False,
        'jobs': records, 'errors': errors})
    return errors
