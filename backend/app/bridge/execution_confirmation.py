"""Durable user confirmation of the real simulation inputs, before any job starts."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from app.bridge.execution_batch_plan import prepare_execution
from app.bridge.node_key import parse_node_key
from app.harness.agent_loop.trace import digest
from app.harness.llm.accounting import RunModelBudget, charged_model_attempts
from app.harness.persistence import atomic_write_json, path_lock
from app.harness.schema.frontmatter_parser import parse
from app.harness.tools.config import load_execution_config
from app.harness.tools.git_branch import _load as load_branch_receipt, current_git_branch, receipt_path
from app.harness.tools.project_repo import load_project_repo
from app.settings import get_settings, repo_root
from app.storage.run_store import RunHandle


def confirmation_policy() -> dict[str, Any]:
    import yaml
    raw: dict[str, Any] = yaml.safe_load((repo_root() / 'configs/execution_review.yaml').read_text())
    if raw.get('poll_interval_seconds', 0) <= 0 or raw.get('max_source_bytes', 0) < 1:
        raise ValueError('仿真配置核对策略无效')
    return raw


def _file_hash(path: Path) -> str:
    if not path.is_file():
        return ''
    if path.stat().st_size > confirmation_policy()['max_source_bytes']:
        raise ValueError('配置文件过大，无法完整核对')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _receipt_path(run: RunHandle, token: str) -> Path:
    if len(token) != 64 or any(char not in '0123456789abcdef' for char in token):
        raise ValueError('配置核对标识无效')
    return run.root / 'execution/confirmations' / (token + '.json')


def _public_config(value: Any) -> Any:
    """Connection credentials never enter the browser or confirmation receipt."""
    if isinstance(value, dict):
        return {str(key): ('[已隐藏]' if any(word in str(key).lower() for word in
            ('password', 'secret', 'token', 'credential', 'private_key', 'api_key', 'authorization', 'passphrase'))
            else _public_config(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [_public_config(item) for item in value]
    return value


def execution_preview(run: RunHandle, node_key: str) -> dict[str, Any]:
    """Read-only preview. The batch consumes the same preparation function."""
    from app.bridge.agent_runner import _load_selected_data_source
    settings = get_settings()
    config = load_execution_config()['execution']
    configured = str(config['backend'])
    blockers: list[str] = []
    warnings: list[str] = []
    defaults: dict[str, Any] = {
        'device': '远端 GPU' if settings.mars_execution_backend == 'remote_gpu' else '本地',
        'runtime_backend': settings.mars_execution_backend,
        'configured_backend': configured,
        'backend_source': config['backend_source'],
        'max_concurrency': config['max_concurrency'],
        'batch_steps': config['batch_steps'],
        'timeout_seconds': config['command_timeout_seconds'],
    }
    if configured == 'paper_static':
        paper_defaults = config.get('paper_static', {})
        defaults['max_concurrency'] = min(int(config['max_concurrency']), int(paper_defaults.get('max_concurrency', 1)))
        defaults['batch_steps'] = paper_defaults.get('default_max_iters', 1)
    options = run.root / 'input/run_request_options.v1.json'
    request_extra = json.loads(options.read_text()).get('extra', {}) if options.is_file() else {}
    defaults['stop_after_execution'] = request_extra.get('stop_after') == 'execution'
    files: dict[str, str] = {}
    for name in ('experiment/experiment_plan.approved.md', 'coding/code_spec.approved.md',
                 'execution/run_log.approved.md', 'input/selected_data_source.json', 'input/run_request_options.v1.json'):
        files[name] = _file_hash(run.root / name)
    if files['experiment/experiment_plan.approved.md']:
        if not files['coding/code_spec.approved.md']:
            blockers.append('缺少批准的编码交付，无法核验实验约定。')
        else:
            from app.execution.handoff_validation import coding_handoff_errors
            try:
                blockers.extend(coding_handoff_errors(
                    (run.root / 'experiment/experiment_plan.approved.md').read_text(),
                    parse((run.root / 'coding/code_spec.approved.md').read_text()).metadata,
                    project=run.project))
            except (OSError, ValueError, RuntimeError) as exc:
                blockers.append('批准交接无法核验：' + str(exc))
    experiments: list[dict[str, Any]] = []
    prepared = None
    try:
        prepared = prepare_execution(run, node_key)
        defaults.update(max_concurrency=prepared.max_concurrency, batch_steps=prepared.batch_steps)
        experiments = [{'name': spec.experiment_id, 'seed': spec.seed, 'config': spec.config}
                       for spec in prepared.specs]
        if not prepared.plan_source.startswith('execution_run_log'):
            blockers.append('执行产物中没有可用的批准实验清单，不能直接用上游方案替代执行计划。')
        if len(experiments) != prepared.planned_before_intent:
            blockers.append(f'批准方案有 {prepared.planned_before_intent} 组实验，当前默认配置只会执行 {len(experiments)} 组。请核对实验数量。')
    except (OSError, ValueError, RuntimeError) as exc:
        message = str(exc)
        if message == 'approved experiment seed must be a nonnegative integer':
            message = '批准方案缺少明确的随机种子，请填写非负整数，不能仅写“同基线种子”。'
        elif message == 'no valid approved experiment configurations; execution was not started':
            message = '没有可用的批准实验配置，尚未启动仿真。'
        elif '编码交付' in message:
            message = '编码交付与批准方案不一致，请修正配置绑定、随机种子和预算，再生成执行清单。'
        else:
            message = '实验清单或配置无法核验，请检查已批准的执行计划。'
        blockers.append('执行计划尚不可启动：' + message)
        plan = run.root / 'experiment/experiment_plan.approved.md'
        if plan.is_file():
            raw = parse(plan.read_text()).metadata.get('ablations', [])
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, dict) and isinstance(item.get('config', {}), dict):
                        cfg = item.get('config', {})
                        experiments.append({'name': str(item.get('name', '')), 'seed': cfg.get('seed'), 'config': cfg})
    if configured != settings.mars_execution_backend:
        blockers.append(f'实际运行环境为 {settings.mars_execution_backend}，全局配置为 {configured}，请先统一执行配置。')
    if config.get('declared_backend') not in (None, configured):
        warnings.append('当前执行方式由显式环境配置统一选择；YAML 中的默认执行方式未生效。')
    branch = current_git_branch(run.project, run.run_id)
    if branch is None and receipt_path(run.root).exists():
        repo = load_project_repo(run.project)
        branch = load_branch_receipt(repo.root, run.root, run.project, run.run_id)
        if branch is not None:
            branch.validate(run.project, run.run_id)
    if branch is not None:
        files['branch'] = digest({'branch': branch.branch, 'baseline_commit': branch.baseline_commit})
    repository = str(branch.root) if branch is not None else ''
    defaults.update(repository=repository, branch=branch.branch if branch is not None else '')
    source_configs: list[dict[str, Any]] = []
    code = run.root / 'coding/code_spec.approved.md'
    if code.is_file() and branch is not None:
        rows = parse(code.read_text()).metadata.get('files_changed', [])
        if not isinstance(rows, list):
            raise ValueError('代码产物的改动列表无法校验')
        for item in rows:
            if not isinstance(item, dict):
                raise ValueError('代码产物的改动列表无法校验')
            name = str(item.get('path', ''))
            path = (branch.root / name).resolve()
            if (not name or not path.is_relative_to(branch.root.resolve())
                    or any(part.startswith('.') for part in Path(name).parts)):
                raise ValueError('代码产物包含越界路径')
            files['code:' + name] = _file_hash(path)
            if path.suffix.lower() in {'.yaml', '.yml'} and path.is_file():
                import yaml
                raw = yaml.safe_load(path.read_text())
                if isinstance(raw, dict):
                    source_configs.append({'path': name, 'seed': raw.get('seed'), 'epochs': raw.get('Epoch', raw.get('epochs'))})
    data = _load_selected_data_source(run)
    defaults['data_path'] = str(data.get('stored_path') or '')
    if prepared is not None:
        paths = {str(spec.config.get('data_path') or '') for spec in prepared.specs}
        if len(paths) == 1:
            defaults['data_path'] = paths.pop()
        from app.harness.schema.experiment_contract import budget as approved_job_budget
        budgets = {approved_job_budget(spec.config) for spec in prepared.specs
                   if any(key in spec.config for key in ('budget_steps', 'budget_unit', 'max_iters'))}
        if len(budgets) == 1:
            defaults['budget_unit'], defaults['approved_budget'] = budgets.pop()
    if settings.mars_execution_backend == 'paper_static':
        from app.execution.paper_static_adapter import (
            _bool_value, _override_args, _paper_static_config, _python_from_config,
            _resolve_path, _validate_inputs, approved_config_path,
        )
        import yaml
        paper = _paper_static_config()
        files['adapter_policy'] = digest(paper)
        root = branch.root if branch is not None else _resolve_path(str(paper.get('repo_path', '')), repo_root())
        python = _python_from_config(paper)
        defaults.update(repository=str(root), python=python, timeout_seconds=paper.get('timeout_seconds'))
        if not bool(paper.get('enabled', True)):
            blockers.append('论文训练适配器未启用。')
        if prepared is None and experiments:
            if any(not item['config'].get('config_path') for item in experiments):
                blockers.append('编码交付未为每组实验绑定配置文件；请补齐 execution_jobs，执行管理器不会选用全局默认文件。')
        if prepared is not None:
            for item, spec in zip(experiments, prepared.specs, strict=True):
                try:
                    cfg_path = approved_config_path(spec.config, paper, root)
                    data_path = _resolve_path(str(spec.config.get('data_path') or paper.get('data_path', '')), root)
                    invalid = _validate_inputs(python=python, repo_path=root, config_path=cfg_path, data_path=data_path)
                    if invalid:
                        raise ValueError(invalid)
                    files['config:' + spec.experiment_id] = _file_hash(cfg_path)
                    files['entrypoint:' + spec.experiment_id] = _file_hash(root / 'train_static.py')
                    raw = yaml.safe_load(cfg_path.read_text())
                    if not isinstance(raw, dict):
                        raise ValueError('配置文件必须是对象')
                    overrides = _override_args(spec.config, paper)
                    effective = {part.split('=', 1)[0]: part.split('=', 1)[1] for part in overrides if part != '--set'}
                    actual_seed = effective.get('seed', raw.get('seed'))
                    from app.harness.schema.experiment_contract import budget as approved_budget
                    unit, count = approved_budget(spec.config)
                    item['effective'] = {'config_path': str(cfg_path), 'entrypoint': 'train_static.py',
                        'max_iters': count, 'budget_unit': unit,
                        'training_epochs': raw.get('Epoch', raw.get('epochs')),
                        'dry_run': _bool_value(spec.config.get('dry_run', paper.get('default_dry_run', False))),
                        'seed': actual_seed, 'data_path': str(data_path), 'overrides': effective}
                    if str(actual_seed) != str(spec.seed):
                        blockers.append(f'{spec.experiment_id} 的实际训练种子与批准方案不一致。')
                    if not any(row['path'] == str(cfg_path.relative_to(root)) for row in source_configs):
                        source_configs.append({'path': str(cfg_path.relative_to(root)), 'seed': raw.get('seed'),
                                               'epochs': raw.get('Epoch', raw.get('epochs'))})
                except (OSError, ValueError) as exc:
                    blockers.append(f'{spec.experiment_id}：{exc}')
    elif settings.mars_execution_backend == 'local_command':
        from app.harness.tools.execution.local_command import LocalCommandJob, _command
        import shutil
        if prepared is not None:
            for item, spec in zip(experiments, prepared.specs, strict=True):
                try:
                    argv, required, timeout = _command(LocalCommandJob(run_id=run.run_id,
                        experiment_id=spec.experiment_id, project=run.project, run_root=run.root,
                        command_id=str(spec.config.get('command_id') or '')), 'execution.simulation_runner')
                    if not shutil.which(argv[0]):
                        raise ValueError('启动命令的解释器不可执行')
                    item['effective'] = {'command_id': spec.config.get('command_id'),
                                         'required_metrics': list(required), 'timeout_seconds': timeout}
                except (OSError, ValueError, RuntimeError):
                    blockers.append(f'{spec.experiment_id} 的启动命令未登记、未授权或环境不可用。')
        elif not config.get('local_commands'):
            blockers.append('尚未配置实际启动命令。')
    elif settings.mars_execution_backend == 'remote_gpu':
        from app.execution.remote.executor import load_remote_executor_config
        try:
            remote = load_remote_executor_config()
            if not remote.enabled or remote.missing_fields():
                blockers.append('远端 GPU 连接配置未就绪，请核对 SSH、工作目录和 GPU 配置。')
            defaults.update(device='远端 GPU', host=remote.host, user=remote.user,
                remote_root=remote.remote_root, gpu_ids=list(remote.gpu_ids), python=remote.python,
                timeout_seconds=remote.command_timeout_seconds)
            files['remote_config'] = digest(str(remote))
        except (OSError, ValueError):
            blockers.append('远端 GPU 连接配置未就绪，请核对 SSH、工作目录和 GPU 配置。')
    elif settings.mars_execution_backend not in {'paper_static', 'local_command', 'pim_cpu', 'remote_gpu'}:
        blockers.append('当前执行后端无法核验真实仿真配置。')
    if settings.mars_execution_backend == 'local_command':
        import shutil
        for row in config.get('local_commands', []):
            if isinstance(row, dict):
                for index, arg in enumerate(row.get('argv', [])):
                    if not isinstance(arg, str):
                        continue
                    resolved = shutil.which(arg) if index == 0 else arg
                    if resolved and Path(resolved).is_file():
                        source = Path(resolved).resolve()
                        stat = source.stat()
                        files['command:' + resolved] = (_file_hash(source) if source.suffix in {'.py', '.sh', '.js'}
                            else digest([str(source), stat.st_size, stat.st_mtime_ns, stat.st_ino]))
    budget = RunModelBudget(run.root)
    ledger = budget.recovery_snapshot()
    budget_view = {'used': sum(charged_model_attempts(row) for row in ledger['requests'].values()),
                   'limit': ledger['configuration']['limits']['max_model_requests'], 'required': 0}
    if not files['execution/run_log.approved.md']:
        blockers.append('执行计划尚未生成并审核通过；此处先展示已有配置。')
    else:
        from app.harness.schema.validator import validate_document
        result = validate_document((run.root / 'execution/run_log.approved.md').read_text(), expected_schema='run_log.v1')
        if (not result.valid or result.metadata.get('run_id') != run.run_id
                or result.metadata.get('project') != run.project):
            blockers.append('批准的执行计划格式或任务身份无法校验。')
    # Credentials remain host-side; only their digest affects freshness.
    environment_hash = digest({key: value for key, value in os.environ.items()
                               if key.startswith(('MARS_REMOTE_', 'MARS_PAPER_'))})
    if prepared is not None:
        for spec in prepared.specs:
            raw_path = str(spec.config.get('data_path') or defaults.get('data_path') or '')
            if raw_path:
                path = Path(raw_path).expanduser()
                if not path.is_absolute() and defaults.get('repository'):
                    path = Path(defaults['repository']) / path
                if path.is_file():
                    stat = path.stat()
                    files['data:' + spec.experiment_id] = digest([str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ino])
                else:
                    blockers.append(f'{spec.experiment_id} 的数据路径不可访问。')
    token = digest({'run': run.run_id, 'project': run.project, 'node': node_key, 'files': files,
        'runtime': settings.mars_execution_backend, 'config': config, 'defaults': defaults,
        'experiments': experiments, 'policy': ledger['configuration_sha256'], 'environment': environment_hash})
    path = _receipt_path(run, token)
    confirmed = False
    if path.exists():
        receipt = json.loads(path.read_text())
        confirmed = (receipt.get('schema') == 'execution.confirmation' and receipt.get('run_id') == run.run_id
            and receipt.get('project') == run.project and receipt.get('node') == node_key
            and receipt.get('token') == token and receipt.get('actor') in {'user', 'auto_approve'})
        if not confirmed:
            raise ValueError('仿真配置确认记录无法校验')
    from app.execution.job_journal import job_states
    return {'jobs': job_states(run.root), 'runtime_mode': 'deterministic', 'run_id': run.run_id, 'project': run.project, 'node': node_key, 'token': token,
        'confirmed': confirmed, 'defaults': defaults, 'experiments': _public_config(experiments),
        'source_configs': source_configs, 'blockers': list(dict.fromkeys(blockers)),
        'warnings': warnings, 'budget': budget_view}


def save_confirmation(run: RunHandle, node_key: str, token: str, *, actor: str = 'user') -> dict[str, Any]:
    if actor not in {'user', 'auto_approve'}:
        raise ValueError('确认来源无效')
    with path_lock(run.root / 'execution/.confirmation.lock'):
        view = execution_preview(run, node_key)
        if view['token'] != token:
            raise ValueError('配置或代码已变化，请重新核对；尚未启动仿真。')
        if view['blockers']:
            raise ValueError('；'.join(view['blockers']))
        if not view['confirmed']:
            atomic_write_json(_receipt_path(run, token), {'schema': 'execution.confirmation',
                'run_id': run.run_id, 'project': run.project, 'node': node_key, 'token': token,
                'actor': actor, 'confirmed_at': datetime.now(timezone.utc).isoformat(),
                'defaults': view['defaults'], 'experiment_names': [item['name'] for item in view['experiments']]})
        return {**view, 'confirmed': True, 'created': not view['confirmed']}


def require_confirmation(run: RunHandle, node_key: str) -> dict[str, Any]:
    view = execution_preview(run, node_key)
    if view['blockers'] or not view['confirmed']:
        raise ValueError('仿真配置尚未确认或已发生变化；未启动作业。')
    return view
