"""Explicit project removal, with file-preserving detach and reversible recycle."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import yaml

from app.harness.persistence import atomic_write_json, path_lock
from app.harness.project_workspace import _entries, _read_project, registry_path, open_folder
from app.settings import repo_root
from app.storage.run_state_store import RunStateStore
from app.storage.run_store import RunStore


def require_idle_project(project: str, store: RunStore) -> None:
    for run in store.list():
        if run.project != project:
            continue
        try:
            state = RunStateStore(run).load()
        except (OSError, ValueError, RuntimeError) as exc:
            raise ValueError('任务状态尚未核实，请先检查任务详情：' + run.run_id) from exc
        if state and (state.status in {'running', 'queued', 'stopping'}
                      or any(node.value == 'running' for node in state.graph.all_states().values())):
            raise ValueError('项目仍有运行中的任务，请先在任务详情停止：' + run.run_id)


def _removals_path() -> Path:
    return registry_path().with_name('removed_projects.json')


def removed_projects() -> dict[str, Any]:
    path = _removals_path()
    raw = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if not isinstance(raw, dict):
        raise ValueError('已移除项目索引损坏')
    return raw


def removal_preview(project: str, store: RunStore) -> dict[str, Any]:
    raw = _entries(registry_path()).get(project)
    if raw is None:
        raise ValueError('此功能仅支持已登记的文件夹项目')
    root = Path(raw)
    blockers: list[str] = []
    try:
        require_idle_project(project, store)
    except ValueError as exc:
        blockers.append(str(exc))
    recycle_blockers = list(blockers)
    name = root.name
    try:
        folder = _read_project(root)
        if folder.name != project:
            raise ValueError('项目身份已变化，不能移动文件')
        name = folder.display_name
        resolved = root.resolve()
        if (not root.is_absolute() or root.is_symlink() or root != resolved
                or resolved == Path(resolved.anchor)
                or any(path.resolve().is_relative_to(resolved) for path in
                       (repo_root(), Path.home(), registry_path()))):
            raise ValueError('该目录包含服务、用户目录或链接，不能整体回收')
        for other, location in _entries(registry_path()).items():
            if other == project:
                continue
            other_root = Path(location).resolve()
            if other_root.is_relative_to(resolved) or resolved.is_relative_to(other_root):
                raise ValueError('该目录与其他项目重叠，只能从列表移除')
            from app.harness.tools.project_repo import load_project_repo
            try:
                linked = load_project_repo(other).root.resolve()
            except (OSError, ValueError):
                continue
            if linked.is_relative_to(resolved):
                raise ValueError('其他项目仍引用这里的代码，只能从列表移除')
    except (OSError, ValueError, yaml.YAMLError) as exc:
        recycle_blockers.append(str(exc))
    return {'project': project, 'display_name': name, 'path': str(root),
            'blockers': blockers, 'recycle_blockers': recycle_blockers,
            'message': '仅处理此项目文件夹；外部原始代码和外部数据不会移动。回收文件仍占磁盘空间，可恢复。'}


def remove_project(project: str, *, mode: str, expected_path: str, confirmation: str,
                   store: RunStore) -> dict[str, Any]:
    if mode not in {'detach', 'recycle'}:
        raise ValueError('请选择保留文件或回收文件')
    index = registry_path()
    with path_lock(index.with_suffix('.lock')):
        preview = removal_preview(project, store)
        if preview['path'] != expected_path or preview['display_name'] != confirmation:
            raise ValueError('项目名称或路径已变化，请重新确认')
        blockers = preview['recycle_blockers'] if mode == 'recycle' else preview['blockers']
        if blockers:
            raise ValueError('；'.join(blockers))
        records = removed_projects()
        prior_records = dict(records)
        root = Path(expected_path)
        removal_id = uuid.uuid4().hex
        target = root
        if mode == 'recycle':
            # Resolve and constrain both ends immediately before the only directory move.
            parent = root.resolve().parent / '.mars-project-trash'
            if parent.exists() and (parent.is_symlink() or parent.resolve() != parent):
                raise ValueError('项目回收目录不能是链接')
            target = parent / removal_id / root.name
            if not target.resolve().is_relative_to(root.resolve().parent) or root.resolve() == target.resolve():
                raise ValueError('回收目标越过项目所在目录')
            target.parent.mkdir(parents=True, exist_ok=False)
            root.rename(target)
        try:
            record = {**preview, 'id': removal_id, 'mode': mode, 'stored_path': str(target),
                      'removed_at': datetime.now(timezone.utc).isoformat()}
            records[removal_id] = record
            atomic_write_json(_removals_path(), records)
            entries = _entries(index)
            entries.pop(project)
            atomic_write_json(index, entries)
        except (OSError, ValueError):
            if mode == 'recycle' and target.exists() and not root.exists():
                target.rename(root)
            atomic_write_json(_removals_path(), prior_records)
            raise
        return record


def restore_project(removal_id: str) -> dict[str, Any]:
    index = registry_path()
    with path_lock(index.with_suffix('.lock')):
        records = removed_projects()
        record = records.get(removal_id)
        if not isinstance(record, dict):
            raise ValueError('移除记录不存在')
        root, stored = Path(record['path']), Path(record['stored_path'])
        if record['project'] in _entries(index):
            raise ValueError('项目已在列表中，请先刷新')
        identity = _read_project(stored)
        if identity.name != record['project']:
            raise ValueError('文件夹身份已变化，不能恢复')
        moved = record['mode'] == 'recycle'
        if moved:
            expected = root.parent / '.mars-project-trash' / removal_id / root.name
            if stored.resolve() != expected or stored.is_symlink() or root.exists():
                raise ValueError('原目录已存在或回收目录已变化，不能覆盖恢复')
            stored.rename(root)
        try:
            folder = open_folder(str(root))
        except (OSError, ValueError):
            if moved and root.exists() and not stored.exists():
                root.rename(stored)
            raise
        records.pop(removal_id)
        atomic_write_json(_removals_path(), records)
        return {'project': folder.name, 'path': str(root)}
