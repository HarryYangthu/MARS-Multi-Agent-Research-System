from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from app.storage.data_source_store import DataSourceStore, selection_summary
from app.storage.run_store import RunStore


def test_sanitized_upload_aliases_remain_distinct_in_research_context(tmp_path: Path) -> None:
    store = DataSourceStore(base=tmp_path / 'data')
    run_store = RunStore(runs_root=tmp_path / 'runs')
    sources = []
    for index, original in enumerate(('capture_T+F.npz', 'capture_T_F.npz')):
        source_id, path = store.allocate(original_name=original, project='pimc')
        np.savez(path, samples=np.arange(16) + index)
        profile = store.profile_uploaded_file(source_id=source_id, path=path,
            project='pimc', original_name=original)
        run = run_store.create(task=f'alias-{index}', project='pimc', user_request='Use this file',
                               data_source=profile)
        saved = json.loads((run.root / 'input/selected_data_source.json').read_text(encoding='utf-8'))
        summary = (run.root / 'input/user_request.md').read_text(encoding='utf-8')
        assert saved['original_name'] == original and saved['stored_path'] == str(path)
        assert f'file: {original}' in summary and f'stored_path: {path}' in summary
        assert 'stored_filename: capture_T_F.npz' in summary
        assert ('This is a storage alias' in summary) == (index == 0)
        assert 'keep logical dataset/registry keys separate' in summary
        sources.append(saved)
    # A lossy filename conversion must never merge different uploads or provenance.
    assert Path(sources[0]['stored_path']).name == Path(sources[1]['stored_path']).name
    for field in ('id', 'stored_path', 'checksum'):
        assert sources[0][field] != sources[1][field]


def test_numeric_scalars_are_retained_without_inventing_units(tmp_path: Path) -> None:
    store = DataSourceStore(base=tmp_path / 'data')
    source_id, path = store.allocate(original_name='capture.npz', project='pimc')
    np.savez(path, txa=np.ones((2, 64)), txb=np.ones((2, 64)), rxa=np.zeros((2, 64)),
             lo_txb=np.array([[3450]]), sample_rate=np.array([[245.76]]),
             unknown=np.array([[np.nan]]), complex_scalar=np.array([[2 + 1j]]))
    profile = store.profile_uploaded_file(source_id=source_id, path=path, project='pimc', original_name='capture.npz')
    entries = {item['key']: item for item in profile['dict_entries']}
    assert entries['lo_txb']['scalar_value'] == 3450
    assert entries['sample_rate']['scalar_value'] == 245.76
    assert 'scalar_value' not in entries['txa']
    assert 'scalar_value' not in entries['unknown']
    assert 'scalar_value' not in entries['complex_scalar']
    assert profile['fs_mhz'] is None  # unitless field is evidence, not a unit declaration
    summary = selection_summary(profile)
    assert 'observed_scalar=3450.0' in summary and 'do not infer units' in summary
    assert 'Data compatibility requirement' in summary


def test_data_source_profile_generates_spectrum(tmp_path: Path) -> None:
    store = DataSourceStore(base=tmp_path / "datasets")
    source_id, target = store.allocate(original_name="capture.npz", project="pimc")
    x = np.exp(1j * np.linspace(0, 8 * np.pi, 1024))
    y = 0.5 * np.exp(1j * np.linspace(0, 4 * np.pi, 512))
    np.savez(target, iq=x, ref=y)

    profile = store.profile_uploaded_file(
        source_id=source_id,
        path=target,
        project="pimc",
        original_name="capture.npz",
        fs_mhz=184.32,
        kind="paper_static",
        channel_count=16,
        description="unit",
    )

    assert profile["id"] == source_id
    assert profile["shape"] == [1024]
    assert profile["dtype"].startswith("complex")
    assert len(profile["dict_entries"]) == 2
    assert profile["dict_entries"][0]["key"] == "iq"
    assert profile["spectrum_available"] is True
    assert (tmp_path / "datasets" / source_id / "spectrum.png").is_file()
    assert "fs_mhz: 184.32" in selection_summary(profile)
    assert "dict_entries" in selection_summary(profile)


def test_data_source_profile_reads_mat_with_external_python(tmp_path: Path) -> None:
    python = Path("/opt/anaconda3/bin/python")
    if not python.exists():
        pytest.skip("external scipy python is not configured on this machine")
    probe = subprocess.run(
        [str(python), "-c", "import scipy.io"],
        check=False,
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        pytest.skip("external python does not have scipy.io")

    store = DataSourceStore(base=tmp_path / "datasets")
    source_id, target = store.allocate(original_name="capture.mat", project="pimc")
    subprocess.run(
        [
            str(python),
            "-c",
            (
                "import numpy as np, scipy.io, sys; "
                "x=np.exp(1j*np.linspace(0,8*np.pi,2048)).reshape(16,128); "
                "y=np.arange(32); "
                "scipy.io.savemat(sys.argv[1], {'x': x, 'y': y, 'fs': 245.76})"
            ),
            str(target),
        ],
        check=True,
    )

    profile = store.profile_uploaded_file(
        source_id=source_id,
        path=target,
        project="pimc",
        original_name="capture.mat",
        fs_mhz=245.76,
        kind="paper_static",
        channel_count=16,
    )

    assert profile["format"] == "mat"
    assert profile["shape"] == [16, 128]
    assert str(profile["dtype"]).startswith("complex")
    assert profile["preview_key"] == "root.x"
    assert profile["spectrum_available"] is True
    assert (tmp_path / "datasets" / source_id / "spectrum.png").is_file()
    keys = [item["key"] for item in profile["dict_entries"]]
    assert "root.x" in keys
    assert "root.y" in keys
    assert not any("暂不支持直接预览" in item for item in profile["warnings"])


def test_run_store_persists_selected_data_source_context(tmp_path: Path) -> None:
    data_source = {
        "id": "ds1",
        "kind": "paper_static",
        "original_name": "capture.npz",
        "stored_path": "/tmp/capture.npz",
        "checksum": "sha256:abc",
        "fs_mhz": 184.32,
    }
    run = RunStore(runs_root=tmp_path / "runs").create(
        task="real data",
        project="pimc",
        user_request="请用真实数据仿真",
        data_source=data_source,
    )

    selected = json.loads(
        (run.subdir("input") / "selected_data_source.json").read_text(encoding="utf-8")
    )
    assert selected["id"] == "ds1"
    assert (run.subdir("context") / "selected_data_source.json").is_file()
    prompt = (run.subdir("input") / "user_request.md").read_text(encoding="utf-8")
    assert "Selected simulation data" in prompt
    assert "stored_path: /tmp/capture.npz" in prompt


def test_data_source_store_project_default(tmp_path: Path) -> None:
    store = DataSourceStore(base=tmp_path / "datasets")
    source_id, target = store.allocate(original_name="default.npz", project="pimc")
    np.savez(target, iq=np.arange(16))
    store.profile_uploaded_file(
        source_id=source_id,
        path=target,
        project="pimc",
        original_name="default.npz",
        fs_mhz=184.32,
        kind="paper_static",
    )

    default = store.set_default(project="pimc", source_id=source_id)

    assert default["id"] == source_id
    assert store.default_id("pimc") == source_id
    assert store.default_profile("pimc") is not None
    listed = store.list(project="pimc")
    assert listed[0]["is_default"] is True
