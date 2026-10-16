from pathlib import Path

import numpy as np
import pytest
from fastapi import HTTPException

from app.api.runs import DataSourceSelection, _resolve_data_source_selection
from app.bridge.run_data_selection import resolve_run_data_source
from app.storage.data_source_store import DataSourceStore


def dataset(store: DataSourceStore, project: str) -> str:
    identifier, path = store.allocate(original_name='input.npy', project=project)
    np.save(path, np.arange(32))
    store.profile_uploaded_file(source_id=identifier, path=path, project=project, original_name='input.npy')
    return identifier


def test_api_and_commander_resolver_agree_on_selection_and_missing_selection(tmp_path: Path) -> None:
    store = DataSourceStore(base=tmp_path)
    identifier = dataset(store, 'project')
    with pytest.raises(ValueError, match='尚未选定'):
        resolve_run_data_source('project', entrypoint='experiment', standalone=False, store=store)
    with pytest.raises(HTTPException) as error:
        _resolve_data_source_selection(None, project='project', store=store)
    assert error.value.status_code == 422
    assert '尚未创建任务' in error.value.detail
    explicit = _resolve_data_source_selection(DataSourceSelection(id=identifier, fs_mhz=245.76),
                                               project='project', store=store)
    assert explicit is not None and explicit['id'] == identifier and explicit['fs_mhz'] == 245.76
    assert store.default_id('project') == ''  # An explicit run selection does not rewrite project defaults.
    store.set_default(project='project', source_id=identifier)
    assert _resolve_data_source_selection(None, project='project', store=store) == resolve_run_data_source(
        'project', entrypoint='experiment', standalone=False, store=store)


def test_no_uploads_and_standalone_research_do_not_require_arbitrary_data(tmp_path: Path) -> None:
    store = DataSourceStore(base=tmp_path)
    assert resolve_run_data_source('project', entrypoint='coding', standalone=False, store=store) is None
    dataset(store, 'project')
    assert resolve_run_data_source('project', entrypoint='idea', standalone=True, store=store) is None
    assert resolve_run_data_source('other', entrypoint='coding', standalone=False, store=store) is None


def test_selected_file_deleted_is_reported_before_stages_start(tmp_path: Path) -> None:
    store = DataSourceStore(base=tmp_path)
    identifier = dataset(store, 'project')
    selected = store.set_default(project='project', source_id=identifier)
    Path(selected['stored_path']).unlink()
    with pytest.raises(ValueError, match='文件不存在'):
        resolve_run_data_source('project', entrypoint='coding', standalone=False, store=store)
    with pytest.raises(HTTPException) as error:
        _resolve_data_source_selection(None, project='project', store=store)
    assert error.value.status_code == 422


def test_missing_selected_profile_does_not_fall_back_to_another_upload(tmp_path: Path) -> None:
    store = DataSourceStore(base=tmp_path)
    identifier = dataset(store, 'project')
    store.set_default(project='project', source_id=identifier)
    dataset(store, 'project')
    store.profile_path(identifier).unlink()
    with pytest.raises(ValueError, match='登记记录不存在'):
        resolve_run_data_source('project', entrypoint='coding', standalone=False, store=store)


def test_explicit_selection_cannot_cross_project_boundaries(tmp_path: Path) -> None:
    store = DataSourceStore(base=tmp_path)
    identifier = dataset(store, 'other')
    with pytest.raises(HTTPException) as error:
        _resolve_data_source_selection(DataSourceSelection(id=identifier), project='project', store=store)
    assert error.value.status_code == 422 and '不属于当前项目' in error.value.detail
