"""Configure a folder project's Python adapter from the project setup screen."""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path, PureWindowsPath
from typing import Any

from app.harness.persistence import atomic_write_json, path_lock
from app.harness.project_workspace import folder_project
from app.harness.tools.config import load_execution_config
from app.harness.tools.execution.handoff import project_local_commands
from app.harness.tools.execution.registrations import registered_commands, registration_path
from app.harness.tools.project_repo import load_project_repo
from app.settings import get_settings


def require_training_for_run(project: str, *, entrypoint: str, standalone: bool) -> None:
    needs_execution = entrypoint == 'execution' or (
        not standalone and entrypoint in {'pipeline', 'idea', 'experiment', 'coding'})
    if needs_execution and folder_project(project) is not None:
        from app.harness.tools.execution.handoff import execution_delivery_blocker
        blocker = execution_delivery_blocker(project)
        if blocker:
            raise ValueError(blocker)


def _relative(value: str, *, pattern: bool = False) -> str:
    value = value.strip().replace('\\', '/')
    if (not value or Path(value).is_absolute() or PureWindowsPath(value).drive
            or '..' in value.split('/') or any(c in value for c in '{}\n\r\x00')
            or (not pattern and any(c in value for c in '*?['))):
        raise ValueError('训练脚本和参数文件必须使用代码目录内的相对路径')
    return value


def training_status(project: str) -> dict[str, Any]:
    folder = folder_project(project)
    if folder is None:
        raise ValueError('此配置仅用于文件夹项目')
    repo = load_project_repo(project).root
    policy = load_execution_config()['execution']
    commands = project_local_commands(policy, project)
    managed = registered_commands().get(project)
    current = managed or (commands[0] if commands else None)
    candidates = list(dict.fromkeys(str(row['argv'][0]) for row in policy.get('local_commands', [])
                                    if isinstance(row, dict) and isinstance(row.get('argv'), list) and row['argv']))
    candidates = [item for item in candidates if Path(item).is_file() or shutil.which(item)]
    if not candidates:
        candidates = [sys.executable]
    entrypoint = str((current or {}).get('entrypoints', ['mars_job.py'])[0])
    draft = {'python': str(current['argv'][0]) if current else candidates[0],
             'entrypoint': entrypoint,
             'config_pattern': str((current or {}).get('config_paths', ['configs/*.yaml'])[0]),
             'required_metrics': list((current or {}).get('required_metrics', [])),
             'consumes_data_path': bool((current or {}).get('consumes_data_path', True))}
    entry = repo / entrypoint
    exists = entry.is_file() and entry.resolve().is_relative_to(repo.resolve())
    return {'project': project, 'backend': get_settings().mars_execution_backend,
            'configured': bool(commands), 'command_id': str(current['id']) if current else '',
            'draft': draft, 'python_options': candidates, 'code_root': str(repo),
            'entrypoint_exists': exists, 'execution_verified': False,
            'message': ('训练启动方式已登记。' if commands else '尚未登记训练启动方式。') +
                       ('入口文件已找到，实际参数和数据会在仿真前核验。' if exists else
                        '入口文件尚未生成，编码 Agent 将按此约定创建并验证；当前未开始训练。')}


def save_training(project: str, *, python: str, entrypoint: str, config_pattern: str,
                  required_metrics: list[str], consumes_data_path: bool) -> dict[str, Any]:
    if get_settings().mars_execution_backend != 'local_command':
        raise ValueError('当前执行方式不是本机训练命令，请保留现有执行适配器配置')
    if folder_project(project) is None:
        raise ValueError('请先创建或打开项目')
    program = python.strip()
    resolved = shutil.which(program) if not Path(program).is_absolute() else program
    if (not resolved or not Path(resolved).is_file()
            or re.fullmatch(r'python(?:[0-9]+(?:\.[0-9]+)*)?(?:\.exe)?', Path(resolved).name, re.I) is None):
        raise ValueError('请选择本机已安装的 Python 可执行文件，不会重新安装依赖')
    script = _relative(entrypoint)
    if not script.endswith('.py'):
        raise ValueError('训练入口应为 Python 脚本（.py）')
    pattern = _relative(config_pattern, pattern=True)
    repo = load_project_repo(project).root.resolve()
    if not (repo / script).resolve().is_relative_to(repo):
        raise ValueError('训练入口不能指向项目代码之外')
    metrics = list(dict.fromkeys(value.strip() for value in required_metrics if value.strip()))
    if any(re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,63}', value) is None for value in metrics):
        raise ValueError('指标名仅支持英文字母、数字、下划线、点和连字符')
    row = {'id': 'training_' + project, 'label': '项目训练入口',
           'argv': [str(Path(resolved).resolve()), '-X', 'utf8', '-u', '{code_root}/' + script],
           'project_ids': [project], 'entrypoints': [script], 'config_paths': [pattern],
           'required_metrics': metrics, 'consumes_data_path': consumes_data_path,
           'validation': {'environment': {'MARS_VALIDATE_ONLY': '1'}, 'timeout_seconds': 60,
                          'max_input_bytes': 1073741824, 'max_output_bytes': 1048576}}
    path = registration_path()
    with path_lock(path.with_suffix('.lock')):
        records = registered_commands()
        records[project] = row
        atomic_write_json(path, records)
    return training_status(project)
