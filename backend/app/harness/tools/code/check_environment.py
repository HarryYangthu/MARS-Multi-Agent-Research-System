"""Trusted project bindings for host-configured Coding check subprocesses."""
from __future__ import annotations

import json
from pathlib import Path

from app.harness.tools.execution.local_command import LocalCommandJob, _bound_workspace
from app.harness.tools.execution.command_preflight import _checked_path
from app.harness.tools.process_runtime import sanitized_subprocess_environment
from app.harness.tools.registry import ToolContext
from app.settings import repo_root

_EXECUTION_BINDINGS = frozenset({
    'MARS_CODE_ROOT', 'MARS_PROJECT_ROOT', 'MARS_DATA_PATH', 'MARS_RUN_ROOT',
    'MARS_RUN_ID', 'MARS_PROJECT', 'MARS_EXPERIMENT_ID', 'MARS_JOB_REQUEST',
    'MARS_RESULT_PATH', 'MARS_VALIDATE_ONLY',
    'MARS_TEST_PROJECT_ROOT', 'MARS_TEST_DATA_PATH',
})


def check_environment(ctx: ToolContext, root: Path, *, project_binding: str = 'test') -> dict[str, str]:
    """Reuse execution's host workspace and input boundary, never model args.

    Tests receive MARS_TEST_* bindings and deliberately opt into them in the
    children needing real project data; temporary fixtures keep their own roots.
    A host-declared runtime binding is available for standalone check commands.
    Tests may inspect project-owned data beside an isolated code checkout. The
    registered folder is the boundary; an inherited or Agent-supplied root is
    not an access grant. An absent selected dataset must not inherit another
    research invocation's dataset or request/result controls.
    """
    if project_binding not in {'test', 'runtime'}:
        raise ValueError('configured check project_binding must be test or runtime')
    run_root = (Path(str(ctx.extra['run_root'])) if ctx.extra.get('run_root')
                else repo_root() / 'runs' / ctx.run_id).resolve()
    job = LocalCommandJob(ctx.run_id, 'coding_check', ctx.project, run_root)
    code_root, project_path = _bound_workspace(job)
    current_root = root.resolve()
    if code_root is not None and code_root.resolve() != current_root:
        raise ValueError('Coding check workspace disagrees with the host-bound research checkout')
    from app.harness.tools.git_branch import current_git_branch
    if current_git_branch(ctx.project, ctx.run_id) is not None:
        from app.harness.runtime.git_runtime import git_child_environment
        environment = git_child_environment()
    else:
        environment = sanitized_subprocess_environment()
    environment = {name: value for name, value in environment.items()
                   if name.upper() not in _EXECUTION_BINDINGS}
    environment.update({
        'MARS_CODE_ROOT': str(current_root), 'MARS_RUN_ROOT': str(run_root),
        'MARS_RUN_ID': ctx.run_id, 'MARS_PROJECT': ctx.project,
    })
    prefix = 'MARS_TEST_' if project_binding == 'test' else 'MARS_'
    if project_path is not None:
        environment[prefix + 'PROJECT_ROOT'] = str(project_path)
    selected = run_root / 'input/selected_data_source.json'
    if selected.exists() or selected.is_symlink():
        if selected.is_symlink() or not selected.is_file() or selected.stat().st_size > 65536:
            raise ValueError('selected Coding check data binding must be a bounded host-owned JSON file')
        raw = json.loads(selected.read_text(encoding='utf-8'))
        path_text = raw.get('stored_path') if isinstance(raw, dict) else None
        if not isinstance(path_text, str) or not path_text.strip():
            raise ValueError('selected Coding check data binding requires stored_path')
        path = Path(path_text).expanduser()
        if not path.is_absolute():
            path = current_root / path
        resolved = _checked_path(job, str(path))
        if not resolved.is_file():
            raise ValueError('selected Coding check data must identify an existing regular file')
        environment[prefix + 'DATA_PATH'] = str(resolved)
    return environment
