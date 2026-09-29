"""Real numerical fixtures and files; no provider or service substitutes."""
from pathlib import Path

import numpy as np
import pytest
savemat = pytest.importorskip("scipy.io").savemat

from app.bridge.data_pipeline import PipelineParameters, policy, summary_document
from app.execution.data_pipeline import capture_array, crop_delay, load_arrays, process_capture
from app.harness.schema.validator import validate_document


def test_numeric_formats_roundtrip(tmp_path: Path) -> None:
    data = np.exp(1j * np.arange(2048))[None, :]
    mat = tmp_path / 'capture.mat'
    npz = tmp_path / 'capture.npz'
    savemat(mat, {'rx': data})
    np.savez(npz, rx=data)
    for path in (mat, npz):
        np.testing.assert_array_equal(load_arrays(path, 100000)['rx'], data)
    torch = pytest.importorskip('torch')
    pth = tmp_path / 'capture.pth'
    torch.save({'rx': torch.from_numpy(data), 'numpy_rx': data}, pth)
    np.testing.assert_array_equal(load_arrays(pth, 100000)['rx'], data)
    np.testing.assert_array_equal(load_arrays(pth, 100000)['numpy_rx'], data)


def test_reject_object_npz_and_invalid_shape(tmp_path: Path) -> None:
    path = tmp_path / 'object.npz'
    np.savez(path, rx=np.array([{'not': 'a signal'}], dtype=object))
    with pytest.raises(ValueError):
        load_arrays(path, 100000)
    with pytest.raises(ValueError):
        capture_array(np.full((1, 2048), np.nan), 1, policy())
    with pytest.raises(ValueError):
        capture_array(np.zeros((2048, 2)), 1, policy())
    assert capture_array(np.zeros((2048, 2)), 0, policy()).shape == (2, 2048)


def test_delay_crop_is_paired_not_circular() -> None:
    reference = np.arange(2048, dtype=np.complex128)[None, :]
    delayed = np.concatenate([np.zeros((1, 7)), reference[:, :-7]], axis=1)
    actual, target = crop_delay(delayed, reference, 7)
    np.testing.assert_array_equal(actual, target)
    actual, target = crop_delay(reference, delayed, -7)
    np.testing.assert_array_equal(actual, target)
    with pytest.raises(ValueError):
        crop_delay(delayed, reference, 2047)


def test_parameters_and_schema() -> None:
    args = dict(source_id='source', signal_key='rx', fs_mhz=100)
    for extra in ({'shift_mhz': 50}, {'lowpass_mhz': 51}, {'auto_align': True}, {'fs_mhz': float('nan')}):
        with pytest.raises(ValueError):
            PipelineParameters.model_validate(args | extra)
    doc = summary_document('project', 'a' * 32, '数据已处理，RF 频段尚未提供。', 600)
    assert validate_document(doc).valid
    with pytest.raises(ValueError):
        summary_document('project', 'a' * 32, 'a' * 601, 600)


@pytest.mark.parametrize('repository', ['/Users/harry/Documents/20_paper/code/exports/static_pimc_minimal_20260908', '/Users/harry/Desktop/pimc'])
def test_real_pimc_functions_and_output_provenance(tmp_path: Path, repository: str) -> None:
    # The real reference stays outside MARS. CI without this repository skips this integration check.
    repo = Path(repository)
    if not (repo / 'libs/dsp.py').exists():
        pytest.skip('External PIMC reference not installed')
    time = np.arange(8192)
    ref = (np.exp(2j * np.pi * .05 * time) + .3 * np.exp(2j * np.pi * .3 * time))[None, :]
    raw = tmp_path / 'capture.npz'
    np.savez(raw, rx=ref, tx=ref)
    original = raw.read_bytes()
    params = PipelineParameters(source_id='source', signal_key='rx', reference_key='tx', fs_mhz=100, shift_mhz=5, delay_samples=3, lowpass_mhz=20)
    out = tmp_path / 'result'
    result = process_capture(raw, repo, out, params.model_dump(), policy())
    assert raw.read_bytes() == original
    assert result['output_shape'] == [1, 8192 - 3 - 128]
    assert result['sources'][0]['function'] in {'spectrum', '_psd_db'}
    assert result['sources'][1]['function'] == 'data_sft'
    assert result['spectrum_before']['peak_MHz'] == pytest.approx(5, abs=.1)
    assert result['spectrum_after']['peak_MHz'] == pytest.approx(10, abs=.1)
    assert (out / 'spectrum.png').stat().st_size > 1000
    with np.load(out / 'processed.npz') as saved:
        assert saved['signal'].shape == saved['reference'].shape
        assert np.isfinite(saved['signal']).all()
        assert np.mean(abs(saved['signal']) ** 2) < np.mean(abs(ref) ** 2)
    rng = np.random.default_rng(11)
    reference = (rng.normal(size=(1, 4096)) + 1j * rng.normal(size=(1, 4096)))
    delayed = np.concatenate([np.zeros((1, 7)), reference[:, :-7]], axis=1)
    paired = tmp_path / 'paired.npz'
    np.savez(paired, rx=delayed, tx=reference)
    aligned = tmp_path / 'aligned'
    parameters = PipelineParameters(source_id='source', signal_key='rx', reference_key='tx', fs_mhz=100, auto_align=True)
    aligned_result = process_capture(paired, repo, aligned, parameters.model_dump(), policy())
    assert aligned_result['steps'][0]['samples'] == 7
    with np.load(aligned / 'processed.npz') as saved:
        np.testing.assert_array_equal(saved['signal'], saved['reference'])
