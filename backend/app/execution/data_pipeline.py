"""Deterministic capture processing; originals and the linked baseline are read-only."""
from __future__ import annotations

import ast
import hashlib
import math
from pathlib import Path
from typing import Any, Callable, cast

import numpy as np
from numpy.typing import NDArray
from scipy import io, signal  # type: ignore[import-untyped]

Array = NDArray[np.complex128]


def load_arrays(path: Path, max_bytes: int) -> dict[str, Any]:
    if path.stat().st_size > max_bytes:
        raise ValueError("数据文件超过当前处理容量")
    suffix = path.suffix.lower()
    if suffix == '.mat':
        try:
            raw = io.loadmat(path)
        except NotImplementedError as exc:
            raise ValueError("MAT v7.3 请先导出为普通 MAT 或 NPZ") from exc
    elif suffix == '.npz':
        import zipfile
        with zipfile.ZipFile(path) as archive:
            if sum(item.file_size for item in archive.infolist()) > max_bytes:
                raise ValueError("NPZ 解压后的数据超过处理容量")
        with np.load(path, allow_pickle=False) as archive_data:
            raw = {key: archive_data[key] for key in archive_data.files}
    elif suffix in {'.pth', '.pt'}:
        import torch
        from app.harness.tensor_archive import read_tensor_archive
        try:
            raw = read_tensor_archive(path)
        except Exception as exc:
            raise ValueError("PTH 需为数值 Tensor / NumPy 字典；无法安全读取的 pickle 请转换为 NPZ") from exc
        if not isinstance(raw, dict):
            raise ValueError("PTH 需要以字段名保存 Tensor 的字典")
        raw = {str(k): (v.detach().cpu().numpy() if isinstance(v, torch.Tensor) else v) for k, v in raw.items() if isinstance(v, (torch.Tensor, np.ndarray))}
    elif suffix == '.npy':
        raw = {'signal': np.load(path, allow_pickle=False)}
    else:
        raise ValueError("支持 MAT、NPZ、PTH、NPY")
    return {str(k): v for k, v in raw.items() if not str(k).startswith('__')}


def capture_array(value: Any, sample_axis: int, policy: dict[str, Any]) -> Array:
    # Input [C,T] or [T,C]; output is always complex [C,T].
    data = np.asarray(value)
    if data.ndim == 1:
        data = data[None, :]
    elif data.ndim == 2 and sample_axis == 0:
        data = data.T
    if (data.ndim != 2 or data.dtype.kind not in 'fciu' or not np.isfinite(data).all()
            or data.shape[1] < 1024 or data.shape[0] < 1):
        raise ValueError("需要有限数值信号，形状 [通道,采样]，至少 1024 个采样点")
    if data.shape[0] > policy['max_channels'] or data.shape[1] > policy['max_samples']:
        raise ValueError("数据维度超过处理容量，请分段选择数据")
    return np.asarray(data, dtype=np.complex128)


def source_function(repo: Path, relative: str, name: str, namespace: dict[str, Any] | None = None) -> tuple[Callable[..., Any], dict[str, str]]:
    """Load only the referenced numerical function, avoiding training module side effects.

    Source stays in the user's linked repository. Imports/config/training entrypoints
    in that source are deliberately not executed; provenance pins the complete file.
    """
    path = (repo / relative).resolve(strict=True)
    if not path.is_relative_to(repo.resolve()):
        raise ValueError("数值实现必须位于关联代码仓内")
    source = path.read_bytes()
    definitions = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == name]
    if len(definitions) != 1 or definitions[0].decorator_list:
        raise ValueError(f"代码仓缺少可用实现：{relative}:{name}")
    module = ast.Module(body=list[ast.stmt](definitions), type_ignores=[])
    scope: dict[str, Any] = {'np': np, 'math': math, 'signal': signal, **(namespace or {})}
    exec(compile(module, str(path), 'exec'), scope)
    return cast(Callable[..., Any], scope[name]), {'path': relative, 'function': name, 'sha256': hashlib.sha256(source).hexdigest()}


def crop_delay(data: Array, reference: Array, delay: int) -> tuple[Array, Array]:
    if data.shape != reference.shape or abs(delay) >= data.shape[1] - 1024:
        raise ValueError("对齐参考必须维度相同，且裁边后至少保留 1024 点")
    # [C,T] -> [C,T-|delay|], no circular wrap or padding.
    if delay > 0:
        return data[:, delay:], reference[:, :-delay]
    if delay < 0:
        return data[:, :delay], reference[:, -delay:]
    return data, reference


def spectrum_metrics(frequencies: Any, density: Any) -> dict[str, Any]:
    power = np.asarray(density, dtype=float).mean(axis=1)
    total = float(power.sum())
    if not math.isfinite(total) or total <= 0:
        return {'peak_MHz': None, 'occupied_99_percent_MHz': None}
    cumulative = np.cumsum(power) / total
    bounds = [float(frequencies[min(int(np.searchsorted(cumulative, q)), len(frequencies) - 1)]) for q in (.005, .995)]
    return {'peak_MHz': float(frequencies[int(np.argmax(power))]), 'occupied_99_percent_MHz': bounds}


def process_capture(path: Path, repo: Path, out: Path, params: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    raw = load_arrays(path, int(policy['max_file_bytes']))
    key = params['signal_key']
    if key not in raw:
        raise ValueError(f"找不到信号字段 {key}")
    before = capture_array(raw[key], params['sample_axis'], policy)
    if params['channel'] >= before.shape[0]:
        raise ValueError('显示通道超出数据范围')
    data = before.copy()
    fs = params['fs_mhz']
    if (repo / policy['sources']['spectrum']).is_file():
        spectrum, spectrum_source = source_function(repo, policy['sources']['spectrum'], 'spectrum')
    else:
        psd, spectrum_source = source_function(repo, policy['sources']['spectrum_fallback'], '_psd_db', {'stl': None, 'args': {'fs': fs}})
        def spectrum(value: Array, fs: float) -> tuple[Any, Any]:
            rows = [psd(row) for row in value]
            return rows[0][0], np.stack([np.power(10, row[1] / 10) for row in rows], axis=1)
        spectrum_source['mode'] = 'explicit scipy fallback; std_lib calibration not used'
    sources = [spectrum_source]
    steps: list[dict[str, Any]] = []
    if params['shift_mhz']:
        shift, provenance = source_function(repo, policy['sources']['frequency_shift'], 'data_sft')
        data = np.asarray(shift(data, params['shift_mhz'], fs), dtype=np.complex128)
        sources.append(provenance)
        steps.append({'operation': 'frequency_shift', 'MHz': params['shift_mhz']})
    delay = params['delay_samples']
    reference_key = params.get('reference_key', '')
    reference: Array | None = None
    if reference_key:
        if reference_key not in raw:
            raise ValueError("找不到时延参考字段")
        reference = capture_array(raw[reference_key], params['sample_axis'], policy)
        if reference.shape != data.shape:
            raise ValueError("时延参考与输入必须有相同通道数和采样数")
    if params['auto_align']:
        if reference is None:
            raise ValueError("自动对齐需要选择参考字段")
        # One explicitly selected digital channel estimates a shared capture delay.
        x, y = data[params['channel']], reference[params['channel']]
        if params['reference_mode'] == 'cubic':
            y = y * np.abs(y) ** 2
        if not np.any(x) or not np.any(y):
            raise ValueError("零信号无法估计时延")
        correlation = signal.correlate(x, y, mode='full', method='fft')
        lags = signal.correlation_lags(len(x), len(y))
        delay = int(lags[np.argmax(np.abs(correlation))])
    if delay or params['auto_align']:
        if reference is None:
            raise ValueError("时延裁边需要参考字段，以保存配对结果")
        data, reference = crop_delay(data, reference, delay)
        steps.append({'operation': 'delay_crop', 'samples': delay, 'method': params['reference_mode'], 'estimated': params['auto_align']})
    cutoff = params.get('lowpass_mhz')
    if cutoff is not None:
        taps = int(policy['fir_taps'])
        if data.shape[1] <= taps + 1024:
            raise ValueError("滤波裁边后采样不足")
        coefficients = signal.firwin(taps, cutoff, fs=fs, window='hamming')
        # Symmetric valid convolution drops both edge transients; [C,T] -> [C,T-taps+1].
        data = np.stack([signal.convolve(row, coefficients, mode='valid') for row in data])
        if reference is not None:
            edge = (taps - 1) // 2
            reference = reference[:, edge:-edge]
        steps.append({'operation': 'lowpass_fir', 'cutoff_MHz': cutoff, 'taps': taps, 'implementation': 'scipy.signal.firwin (Hamming); not historical std_lib'})
    if not np.isfinite(data).all():
        raise ValueError("处理产生非有限值")
    out.mkdir(parents=True, exist_ok=True)
    arrays = {'signal': data}
    if reference is not None:
        arrays['reference'] = reference
    np.savez(out / 'processed.npz', **cast(dict[str, Any], arrays))
    # Chart and numeric PSD use an explicit prefix; statistics cover the full selected capture.
    count = int(policy['spectrum_samples'])
    freqs, old_psd = spectrum(before[:, :count], fs)
    _, new_psd = spectrum(data[:, :count], fs)
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    fig = Figure(figsize=(10, 5), facecolor='#0b0f15')
    FigureCanvasAgg(fig)
    ax = fig.subplots()
    ax.set_facecolor('#0b0f15')
    for density, label, color in [(old_psd, 'Before', '#94a3b8'), (new_psd, 'After', '#34d399')]:
        ax.plot(freqs, 10 * np.log10(np.maximum(density[:, params['channel']], 1e-30)), label=label, color=color)
    ax.set(xlabel='Digital baseband frequency (MHz)', ylabel='PSD (dB / MHz)', title=f"Channel {params['channel']} · Welch / Kaiser(1024,10) / FFT 2048")
    ax.tick_params(colors='#cbd5e1')
    for item in [ax.xaxis.label, ax.yaxis.label, ax.title]:
        item.set_color('#cbd5e1')
    ax.grid(alpha=.2); ax.legend(); fig.tight_layout()
    fig.savefig(out / 'spectrum.png', dpi=140)
    result: dict[str, Any] = {'input_shape': list(before.shape), 'output_shape': list(data.shape),
        'fs_mhz': fs, 'channel': params['channel'],
        'duration_ms': before.shape[1] / (fs * 1000),
        'processing_scope': 'all channels; channel selects plot and shared delay estimator only',
        'missing_samples': 'unknown; cannot infer from zero fraction',
        'spectrum_before': spectrum_metrics(freqs, old_psd),
        'spectrum_after': spectrum_metrics(freqs, new_psd),
        'rms_by_channel_after': np.sqrt(np.mean(np.abs(data) ** 2, axis=1)).tolist(), 'steps': steps, 'sources': sources,
        'rms_before': float(np.sqrt(np.mean(np.abs(before) ** 2))),
        'rms_after': float(np.sqrt(np.mean(np.abs(data) ** 2))),
        'zero_fraction': float(np.mean(before == 0)),
        'spectrum_samples': min(count, data.shape[1], before.shape[1]),
        'spectrum_unit': 'dB/MHz (uncalibrated digital amplitude, not dBm)',
        'warnings': ['互相关时延是数值估计，不证明物理同步。', '频谱为选定数字通道；不能据此推断 RF 频段或 IMD3 归属。']}
    return result
