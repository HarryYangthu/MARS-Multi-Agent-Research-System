"""Resolve user-selected data before starting an ordinary research pipeline."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.storage.data_source_store import DataSourceStore


def resolve_run_data_source(
    project: str, *, entrypoint: str, standalone: bool,
    selection: dict[str, Any] | None = None, store: DataSourceStore | None = None,
) -> dict[str, Any] | None:
    datasets = store if store is not None else DataSourceStore()
    profile = selection
    if profile is None:
        default_id = datasets.default_id(project)
        if default_id:
            try:
                profile = datasets.load(default_id)
            except FileNotFoundError as exc:
                raise ValueError('已选研究数据的登记记录不存在，请在「研究数据」重新选择；尚未创建任务。') from exc
    if profile is not None:
        if profile.get('project') != project:
            raise ValueError('所选数据不属于当前项目，请重新选择；尚未创建任务。')
        path = str(profile.get('stored_path') or '')
        if not path or not Path(path).is_file():
            raise ValueError('所选研究数据文件不存在或无法访问，请重新接入；尚未创建任务。')
        return dict(profile)
    needs_execution = entrypoint == 'execution' or (
        not standalone and entrypoint in {'pipeline', 'idea', 'experiment', 'coding'})
    if needs_execution and datasets.list(project=project):
        raise ValueError('本项目已有上传数据，但尚未选定研究数据。请在项目配置的「研究数据」中选择文件，'
                         '点击「用于本项目研究」后再启动；尚未创建任务，也未调用阶段模型。')
    # No upload is not itself an error: the project may use a registered loader,
    # explicit configuration path or generated data, checked at execution time.
    return None
