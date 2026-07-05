"""Local dataset registry and lightweight preview generation."""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import struct
import subprocess
import tempfile
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np

from app.settings import repo_root

_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9._-]+")
_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_-]+")
_MAX_PREVIEW_POINTS = 65_536
_MAX_PLOT_POINTS = 8_192
_MAX_PREVIEW_ENTRIES = 48
_MAX_JSON_PREVIEW_BYTES = 5 * 1024 * 1024


class DataSourceStore:
    """Store user-selected simulation datasets under ``workspace/uploads``."""

    def __init__(self, base: Path | None = None) -> None:
        self.base = base or (repo_root() / "workspace" / "uploads" / "datasets")
        self.base.mkdir(parents=True, exist_ok=True)

    def allocate(self, *, original_name: str, project: str) -> tuple[str, Path]:
        created = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
        stem = _safe_stem(Path(original_name).stem)
        source_id = _safe_id(f"{created}_{project}_{stem}_{uuid4().hex[:8]}")
        target_dir = self.base / source_id
        target_dir.mkdir(parents=True, exist_ok=False)
        filename = _safe_filename(original_name)
        return source_id, target_dir / filename

    def profile_uploaded_file(
        self,
        *,
        source_id: str,
        path: Path,
        project: str,
        original_name: str,
        fs_mhz: float | None = None,
        kind: str = "auto",
        channel_count: int | None = None,
        description: str = "",
        checksum: str = "",
    ) -> dict[str, Any]:
        source_id = _safe_id(source_id)
        source_dir = self._source_dir(source_id)
        source_dir.mkdir(parents=True, exist_ok=True)
        profile = _build_profile(
            source_id=source_id,
            path=path,
            project=project,
            original_name=original_name,
            fs_mhz=fs_mhz,
            kind=kind,
            channel_count=channel_count,
            description=description,
            checksum=checksum,
            spectrum_path=source_dir / "spectrum.png",
        )
        self.save_profile(profile)
        return profile

    def update_metadata(
        self,
        *,
        source_id: str,
        fs_mhz: float | None = None,
        kind: str | None = None,
        channel_count: int | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        current = self.load(source_id)
        path = Path(str(current.get("stored_path") or ""))
        if not path.is_file():
            raise FileNotFoundError(f"dataset file not found: {path}")
        return self.profile_uploaded_file(
            source_id=source_id,
            path=path,
            project=str(current.get("project") or "pimc"),
            original_name=str(current.get("original_name") or path.name),
            fs_mhz=fs_mhz if fs_mhz is not None else _optional_float(current.get("fs_mhz")),
            kind=kind if kind is not None else str(current.get("kind") or "auto"),
            channel_count=(
                channel_count
                if channel_count is not None
                else _optional_int(current.get("channel_count"))
            ),
            description=(
                description
                if description is not None
                else str(current.get("description") or "")
            ),
            checksum=str(current.get("checksum") or ""),
        )

    def load(self, source_id: str) -> dict[str, Any]:
        profile_path = self.profile_path(source_id)
        if not profile_path.is_file():
            raise FileNotFoundError(source_id)
        data = json.loads(profile_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"invalid data source profile: {profile_path}")
        project = str(data.get("project") or "")
        if project:
            data["is_default"] = self.default_id(project) == source_id
        return data

    def list(self, *, project: str = "") -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        default_id = self.default_id(project) if project else ""
        if not self.base.exists():
            return out
        for profile_path in sorted(self.base.glob("*/profile.json")):
            try:
                profile = json.loads(profile_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(profile, dict):
                continue
            if project and str(profile.get("project") or "") != project:
                continue
            if default_id:
                profile["is_default"] = str(profile.get("id") or "") == default_id
            out.append(profile)
        out.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return out

    def set_default(self, *, project: str, source_id: str) -> dict[str, Any]:
        profile = self.load(source_id)
        if str(profile.get("project") or "") != project:
            raise ValueError("data source belongs to a different project")
        defaults = self._read_defaults()
        defaults[project] = source_id
        self._defaults_path().write_text(
            json.dumps(defaults, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        profile["is_default"] = True
        return profile

    def default_id(self, project: str) -> str:
        return str(self._read_defaults().get(project) or "")

    def default_profile(self, project: str) -> dict[str, Any] | None:
        source_id = self.default_id(project)
        if not source_id:
            return None
        try:
            profile = self.load(source_id)
        except FileNotFoundError:
            return None
        profile["is_default"] = True
        return profile

    def save_profile(self, profile: dict[str, Any]) -> None:
        source_id = str(profile.get("id") or "")
        self.profile_path(source_id).write_text(
            json.dumps(profile, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def profile_path(self, source_id: str) -> Path:
        return self._source_dir(source_id) / "profile.json"

    def spectrum_path(self, source_id: str) -> Path:
        return self._source_dir(source_id) / "spectrum.png"

    def _defaults_path(self) -> Path:
        self.base.mkdir(parents=True, exist_ok=True)
        return self.base / "_defaults.json"

    def _read_defaults(self) -> dict[str, str]:
        path = self._defaults_path()
        if not path.is_file():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        if not isinstance(raw, dict):
            return {}
        return {str(k): str(v) for k, v in raw.items() if str(k) and str(v)}

    def _source_dir(self, source_id: str) -> Path:
        clean = _safe_id(source_id)
        if not clean:
            raise ValueError("data source id is empty")
        target = (self.base / clean).resolve()
        base = self.base.resolve()
        if not target.is_relative_to(base):
            raise ValueError("data source id escapes upload directory")
        return target


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def selection_summary(profile: dict[str, Any]) -> str:
    """Render a compact selected-data block for agent context."""
    keys = [
        ("id", "id"),
        ("kind", "kind"),
        ("file", "original_name"),
        ("stored_path", "stored_path"),
        ("checksum", "checksum"),
        ("fs_mhz", "fs_mhz"),
        ("channel_count", "channel_count"),
        ("shape", "shape"),
        ("dtype", "dtype"),
        ("preview_key", "preview_key"),
        ("sample_points", "sample_points"),
        ("spectrum_available", "spectrum_available"),
        ("spectrum", "spectrum_path"),
    ]
    lines = ["## Selected simulation data"]
    for label, key in keys:
        value = profile.get(key)
        if value not in (None, "", []):
            lines.append(f"- {label}: {value}")
    warnings = profile.get("warnings")
    if isinstance(warnings, list) and warnings:
        lines.append("- warnings: " + "; ".join(str(item) for item in warnings))
    entries = profile.get("dict_entries")
    if isinstance(entries, list) and entries:
        lines.append("- dict_entries:")
        for item in entries:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or item.get("preview_key") or "")
            shape = item.get("shape")
            dtype = str(item.get("dtype") or "")
            points = item.get("sample_points")
            suffix = []
            if shape not in (None, "", []):
                suffix.append(f"shape={shape}")
            if dtype:
                suffix.append(f"dtype={dtype}")
            if points not in (None, "", []):
                suffix.append(f"sample_points={points}")
            lines.append(f"  - {key}: " + ", ".join(suffix))
    return "\n".join(lines)


def _build_profile(
    *,
    source_id: str,
    path: Path,
    project: str,
    original_name: str,
    fs_mhz: float | None,
    kind: str,
    channel_count: int | None,
    description: str,
    checksum: str,
    spectrum_path: Path,
) -> dict[str, Any]:
    warnings: list[str] = []
    sample, inspected = _inspect_numeric_file(path, warnings=warnings)
    preview_entries = _preview_entries_from_inspected(inspected)
    if checksum == "":
        checksum = sha256_file(path)
    spectrum_written = False
    if preview_entries:
        spectrum_written = _write_spectrum_overview(
            entries=preview_entries,
            fs_mhz=fs_mhz,
            target=spectrum_path,
            warnings=warnings,
        )
    elif sample is not None:
        spectrum_written = _write_spectrum(
            sample=sample,
            fs_mhz=fs_mhz,
            target=spectrum_path,
            warnings=warnings,
        )
    else:
        warnings.append("未能从文件中提取数值样本，暂无法生成频谱预览。")
    dict_entries = _public_preview_entries(preview_entries)

    size = path.stat().st_size if path.exists() else 0
    return {
        "id": source_id,
        "project": project,
        "kind": kind or "auto",
        "original_name": original_name,
        "stored_path": str(path),
        "size_bytes": size,
        "checksum": checksum,
        "fs_mhz": fs_mhz,
        "sample_rate_hz": (float(fs_mhz) * 1_000_000.0) if fs_mhz else None,
        "channel_count": channel_count,
        "description": description,
        "format": path.suffix.lower().lstrip(".") or "unknown",
        "shape": inspected.get("shape"),
        "dtype": inspected.get("dtype"),
        "preview_key": inspected.get("preview_key", ""),
        "sample_points": inspected.get("sample_points", 0),
        "dict_entries": dict_entries,
        "spectrum_available": spectrum_written,
        "spectrum_path": str(spectrum_path) if spectrum_written else "",
        "warnings": warnings,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
    }


def _inspect_numeric_file(
    path: Path,
    *,
    warnings: list[str],
) -> tuple[np.ndarray[Any, Any] | None, dict[str, Any]]:
    suffix = path.suffix.lower()
    try:
        if suffix == ".npy":
            arr = np.load(path, mmap_mode="r", allow_pickle=False)
            return _sample_from_array(arr), _array_info(arr, preview_key=path.name)
        if suffix == ".npz":
            with np.load(path, allow_pickle=False) as data:
                keys = list(data.files)
                if not keys:
                    warnings.append("npz 文件没有数组。")
                    return None, {"preview_key": ""}
                arrays = [(key, np.asarray(data[key])) for key in keys]
                sample, info = _profile_from_numeric_arrays(
                    arrays,
                    warnings=warnings,
                )
                return sample, info
        if suffix == ".csv":
            arr = np.genfromtxt(
                path,
                delimiter=",",
                max_rows=_MAX_PREVIEW_POINTS,
                invalid_raise=False,
            )
            return _sample_from_array(arr), _array_info(arr, preview_key=path.name)
        if suffix == ".json":
            if path.stat().st_size > _MAX_JSON_PREVIEW_BYTES:
                warnings.append("json 文件过大，仅登记文件，不解析预览。")
                return None, {"preview_key": path.name}
            payload = json.loads(path.read_text(encoding="utf-8"))
            arr = _first_numeric_array(payload)
            if arr is None:
                warnings.append("json 中未找到数值数组。")
                return None, {"preview_key": path.name}
            return _sample_from_array(arr), _array_info(arr, preview_key=path.name)
        if suffix in {".pth", ".pt"}:
            return _inspect_torch_file(path, warnings=warnings)
        if suffix == ".mat":
            return _inspect_mat_file(path, warnings=warnings)
    except Exception as exc:
        warnings.append(f"数据预览失败：{exc}")
        return None, {"preview_key": path.name}
    warnings.append(f"暂不支持直接预览 {suffix or 'unknown'}，已完成文件登记。")
    return None, {"preview_key": path.name}


def _inspect_torch_file(
    path: Path,
    *,
    warnings: list[str],
) -> tuple[np.ndarray[Any, Any] | None, dict[str, Any]]:
    try:
        torch = importlib.import_module("torch")
    except ModuleNotFoundError:
        external = _inspect_torch_file_external(path, warnings=warnings)
        if external is not None:
            return external
        warnings.append("当前后端环境未安装 torch，无法解析 .pth 频谱预览。")
        return None, {"preview_key": path.name}
    try:
        payload = torch.load(str(path), map_location="cpu", weights_only=True)
    except TypeError:
        payload = torch.load(str(path), map_location="cpu")
    except Exception as exc:
        try:
            payload = torch.load(str(path), map_location="cpu", weights_only=False)
        except Exception:
            warnings.append(f"torch.load(weights_only=True) 失败：{exc}")
            return None, {"preview_key": path.name}
    arrays = _numeric_arrays(payload)
    if not arrays:
        warnings.append(".pth 中未找到可预览的数值 tensor/array。")
        return None, {"preview_key": path.name}
    return _profile_from_numeric_arrays(arrays, warnings=warnings)


def _inspect_torch_file_external(
    path: Path,
    *,
    warnings: list[str],
) -> tuple[np.ndarray[Any, Any] | None, dict[str, Any]] | None:
    python = _paper_static_python()
    if not python:
        return None
    candidate = Path(python).expanduser()
    if candidate.is_absolute() and not candidate.exists():
        return None
    script = r"""
import json
import sys

import numpy as np
import torch

path = sys.argv[1]
limit = int(sys.argv[2])
entry_limit = int(sys.argv[3])

def add_array(out, value, key):
    if len(out) >= entry_limit:
        return
    try:
        data = np.asarray(value)
    except Exception:
        return
    if data.size == 0 or not np.issubdtype(data.dtype, np.number):
        return
    if np.iscomplexobj(data):
        flat = data.reshape(-1)
        real = np.real(flat[:limit]).astype(float).tolist()
        imag = np.imag(flat[:limit]).astype(float).tolist()
    elif data.ndim >= 1 and data.shape[-1] == 2:
        real_arr = np.asarray(data[..., 0], dtype=float)
        imag_arr = np.asarray(data[..., 1], dtype=float)
        flat = (real_arr + 1j * imag_arr).reshape(-1)
        real = np.real(flat[:limit]).astype(float).tolist()
        imag = np.imag(flat[:limit]).astype(float).tolist()
    else:
        flat = np.asarray(data, dtype=float).reshape(-1)
        real = flat[:limit].astype(float).tolist()
        imag = []
    out.append({
        "key": key,
        "shape": list(data.shape),
        "dtype": str(data.dtype),
        "sample_points": int(min(data.size, limit)),
        "real": real,
        "imag": imag,
    })

def collect_arrays(value, key="root", out=None):
    if out is None:
        out = []
    if len(out) >= entry_limit:
        return out
    if isinstance(value, torch.Tensor):
        try:
            add_array(out, value.detach().cpu().numpy(), key)
        except Exception:
            pass
        return out
    if isinstance(value, np.ndarray) and np.issubdtype(value.dtype, np.number):
        add_array(out, value, key)
        return out
    if isinstance(value, dict):
        for k, v in value.items():
            collect_arrays(v, f"{key}.{k}", out)
            if len(out) >= entry_limit:
                break
        return out
    if isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            collect_arrays(v, f"{key}[{i}]", out)
            if len(out) >= entry_limit:
                break
        if out:
            return out
        try:
            arr = np.asarray(value)
        except Exception:
            return out
        if np.issubdtype(arr.dtype, np.number):
            add_array(out, arr, key)
    return out

try:
    try:
        payload = torch.load(path, map_location="cpu", weights_only=True)
    except Exception:
        payload = torch.load(path, map_location="cpu", weights_only=False)
    entries = collect_arrays(payload)
    if not entries:
        print(json.dumps({"ok": False, "error": "no numeric tensor/array found"}))
        raise SystemExit(0)
    print(json.dumps({
        "ok": True,
        "entries": entries,
        "entry_limit": entry_limit,
    }))
except Exception as exc:
    print(json.dumps({"ok": False, "error": str(exc)}))
"""
    try:
        result = subprocess.run(
            [
                python,
                "-c",
                script,
                str(path),
                str(_MAX_PREVIEW_POINTS),
                str(_MAX_PREVIEW_ENTRIES),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        warnings.append(f"外部 torch 解析失败：{exc}")
        return None
    if result.returncode != 0 and result.stderr.strip():
        warnings.append(f"外部 torch 解析 stderr：{result.stderr.strip()[:300]}")
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        warnings.append(f"外部 torch 解析输出无效：{exc}")
        return None
    if not isinstance(payload, dict) or not payload.get("ok"):
        error = payload.get("error") if isinstance(payload, dict) else "unknown"
        warnings.append(f"外部 torch 未找到可预览数组：{error}")
        return None
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        return None
    preview_entries: list[dict[str, Any]] = []
    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        real = item.get("real")
        imag = item.get("imag")
        if not isinstance(real, list):
            continue
        if isinstance(imag, list) and imag:
            sample = np.asarray(real, dtype=np.float64) + 1j * np.asarray(
                imag,
                dtype=np.float64,
            )
        else:
            sample = np.asarray(real, dtype=np.float64)
        preview_entries.append(
            {
                "key": item.get("key") or path.name,
                "preview_key": item.get("key") or path.name,
                "shape": item.get("shape"),
                "dtype": item.get("dtype"),
                "sample_points": item.get("sample_points") or len(real),
                "_sample": sample,
            }
        )
    if not preview_entries:
        return None
    primary = preview_entries[0]
    info = {
        "shape": primary.get("shape"),
        "dtype": primary.get("dtype"),
        "preview_key": primary.get("preview_key") or path.name,
        "sample_points": primary.get("sample_points") or 0,
        "_preview_entries": preview_entries,
    }
    if len(preview_entries) >= _MAX_PREVIEW_ENTRIES:
        warnings.append(
            f".pth 数值项超过 {_MAX_PREVIEW_ENTRIES} 个，预览仅展示前 {_MAX_PREVIEW_ENTRIES} 个。"
        )
    warnings.append(f"使用外部 Python 解析 .pth：{python}")
    primary_sample = primary.get("_sample")
    if isinstance(primary_sample, np.ndarray):
        return primary_sample, info
    return None, info


def _inspect_mat_file(
    path: Path,
    *,
    warnings: list[str],
) -> tuple[np.ndarray[Any, Any] | None, dict[str, Any]]:
    local_warnings: list[str] = []
    local = _inspect_mat_file_local(path, warnings=local_warnings)
    if local is not None:
        warnings.extend(local_warnings)
        return local

    external = _inspect_mat_file_external(path, warnings=warnings)
    if external is not None:
        return external

    warnings.extend(local_warnings)
    warnings.append(
        "当前环境缺少 scipy/h5py，无法解析 .mat；已登记文件，可安装依赖或配置外部 Python。"
    )
    return None, {"preview_key": path.name}


def _inspect_mat_file_local(
    path: Path,
    *,
    warnings: list[str],
) -> tuple[np.ndarray[Any, Any] | None, dict[str, Any]] | None:
    scipy_io = None
    try:
        scipy_io = importlib.import_module("scipy.io")
    except ModuleNotFoundError:
        warnings.append("当前后端环境未安装 scipy，尝试按 HDF5/v7.3 读取 .mat。")

    if scipy_io is not None:
        try:
            payload = scipy_io.loadmat(str(path), struct_as_record=False, squeeze_me=False)
            clean_payload = {
                str(key): value
                for key, value in payload.items()
                if not str(key).startswith("__")
            }
            arrays = _numeric_arrays(clean_payload)
            if arrays:
                sample, info = _profile_from_numeric_arrays(
                    arrays,
                    warnings=warnings,
                )
                warnings.append("使用 scipy.io.loadmat 解析 .mat。")
                return sample, info
            warnings.append(".mat 中未找到可预览的数值数组。")
            return None, {"preview_key": path.name}
        except NotImplementedError as exc:
            warnings.append(f"scipy 无法读取该 .mat，尝试 HDF5/v7.3：{exc}")
        except ValueError as exc:
            warnings.append(f"scipy 读取 .mat 失败，尝试 HDF5/v7.3：{exc}")
        except Exception as exc:
            warnings.append(f"scipy 解析 .mat 失败，尝试 HDF5/v7.3：{exc}")

    hdf5_result = _inspect_hdf5_mat_file(path, warnings=warnings)
    if hdf5_result is not None:
        return hdf5_result
    return None


def _inspect_hdf5_mat_file(
    path: Path,
    *,
    warnings: list[str],
) -> tuple[np.ndarray[Any, Any] | None, dict[str, Any]] | None:
    try:
        h5py = importlib.import_module("h5py")
    except ModuleNotFoundError:
        warnings.append("当前后端环境未安装 h5py，无法读取 v7.3/HDF5 .mat。")
        return None

    preview_entries: list[dict[str, Any]] = []
    try:
        with h5py.File(str(path), "r") as handle:

            def visit(name: str, obj: Any) -> None:
                if len(preview_entries) >= _MAX_PREVIEW_ENTRIES:
                    return
                if not hasattr(obj, "dtype") or not hasattr(obj, "shape"):
                    return
                entry = _preview_entry_from_hdf5_dataset(
                    dataset=obj,
                    key=f"root.{name}",
                )
                if entry is not None:
                    preview_entries.append(entry)

            handle.visititems(visit)
    except Exception as exc:
        warnings.append(f"h5py 读取 .mat 失败：{exc}")
        return None

    if not preview_entries:
        warnings.append("HDF5/v7.3 .mat 中未找到可预览的数值 dataset。")
        return None
    if len(preview_entries) >= _MAX_PREVIEW_ENTRIES:
        warnings.append(
            f".mat 数值项超过 {_MAX_PREVIEW_ENTRIES} 个，预览仅展示前 {_MAX_PREVIEW_ENTRIES} 个。"
        )
    warnings.append("使用 h5py 解析 HDF5/v7.3 .mat。")
    return _profile_from_preview_entries(preview_entries, warnings=warnings)


def _preview_entry_from_hdf5_dataset(
    *,
    dataset: Any,
    key: str,
) -> dict[str, Any] | None:
    dtype = np.dtype(dataset.dtype)
    if not _is_hdf5_numeric_dtype(dtype):
        return None
    try:
        raw = _read_hdf5_dataset_preview(dataset)
    except Exception:
        return None
    arr = _coerce_mat_numeric_array(raw)
    if arr is None:
        return None
    sample = _sample_from_array(arr)
    if sample is None:
        return None
    shape = [int(item) for item in tuple(getattr(dataset, "shape", ())) ]
    source_size = int(np.prod(shape)) if shape else int(np.asarray(raw).size)
    return {
        "key": key,
        "preview_key": key,
        "shape": shape,
        "dtype": str(dtype),
        "sample_points": int(min(source_size, _MAX_PREVIEW_POINTS)),
        "_source_size": source_size,
        "_sample": sample,
    }


def _is_hdf5_numeric_dtype(dtype: np.dtype[Any]) -> bool:
    if np.issubdtype(dtype, np.number):
        return True
    fields = dtype.fields
    return fields is not None and "real" in fields and "imag" in fields


def _read_hdf5_dataset_preview(dataset: Any) -> np.ndarray[Any, Any]:
    shape = tuple(int(item) for item in getattr(dataset, "shape", ()))
    if not shape:
        return np.asarray(dataset[()])
    size = int(np.prod(shape))
    if size <= _MAX_PREVIEW_POINTS:
        return np.asarray(dataset[()])
    slices: list[slice] = []
    product = 1
    for dim in shape:
        remaining = max(1, _MAX_PREVIEW_POINTS // max(product, 1))
        take = min(dim, remaining)
        slices.append(slice(0, max(1, take)))
        product *= max(1, take)
    return np.asarray(dataset[tuple(slices)])


def _coerce_mat_numeric_array(value: Any) -> np.ndarray[Any, Any] | None:
    data = np.asarray(value)
    fields = data.dtype.fields
    if fields is not None and "real" in fields and "imag" in fields:
        return np.asarray(data["real"]) + 1j * np.asarray(data["imag"])
    if np.issubdtype(data.dtype, np.number):
        return data
    return None


def _inspect_mat_file_external(
    path: Path,
    *,
    warnings: list[str],
) -> tuple[np.ndarray[Any, Any] | None, dict[str, Any]] | None:
    python = _paper_static_python()
    if not python:
        return None
    candidate = Path(python).expanduser()
    if candidate.is_absolute() and not candidate.exists():
        return None
    script = r"""
import json
import sys

import numpy as np

path = sys.argv[1]
limit = int(sys.argv[2])
entry_limit = int(sys.argv[3])

def sample_array(value):
    try:
        data = np.asarray(value)
    except Exception:
        return None
    fields = data.dtype.fields or {}
    if "real" in fields and "imag" in fields:
        data = np.asarray(data["real"]) + 1j * np.asarray(data["imag"])
    if data.size == 0 or not np.issubdtype(data.dtype, np.number):
        if data.dtype == object:
            return None
        return None
    if np.iscomplexobj(data):
        flat = data.reshape(-1)
    elif data.ndim >= 1 and data.shape[-1] == 2:
        flat = (np.asarray(data[..., 0], dtype=float) + 1j * np.asarray(data[..., 1], dtype=float)).reshape(-1)
    else:
        flat = np.asarray(data, dtype=float).reshape(-1)
    flat = flat[:limit]
    if np.iscomplexobj(flat):
        return {
            "real": np.real(flat).astype(float).tolist(),
            "imag": np.imag(flat).astype(float).tolist(),
        }
    return {"real": flat.astype(float).tolist(), "imag": []}

def add_array(out, value, key, shape=None, dtype=None, source_size=None):
    if len(out) >= entry_limit:
        return
    sampled = sample_array(value)
    if sampled is None:
        return
    data = np.asarray(value)
    out.append({
        "key": key,
        "shape": list(shape if shape is not None else data.shape),
        "dtype": str(dtype if dtype is not None else data.dtype),
        "sample_points": int(min(source_size if source_size is not None else data.size, limit)),
        "source_size": int(source_size if source_size is not None else data.size),
        "real": sampled["real"],
        "imag": sampled["imag"],
    })

def collect(value, key, out):
    if len(out) >= entry_limit:
        return
    if isinstance(value, np.ndarray):
        if value.dtype == object:
            for index, item in enumerate(value.reshape(-1)[:entry_limit]):
                collect(item, f"{key}[{index}]", out)
                if len(out) >= entry_limit:
                    return
        else:
            add_array(out, value, key)
        return
    if isinstance(value, dict):
        for name, child in value.items():
            if str(name).startswith("__"):
                continue
            collect(child, f"{key}.{name}", out)
            if len(out) >= entry_limit:
                return
        return
    if hasattr(value, "_fieldnames"):
        for name in getattr(value, "_fieldnames", []) or []:
            collect(getattr(value, name), f"{key}.{name}", out)
            if len(out) >= entry_limit:
                return
        return
    add_array(out, value, key)

def read_hdf5_preview(dataset):
    shape = tuple(int(x) for x in dataset.shape)
    if not shape:
        return np.asarray(dataset[()])
    size = int(np.prod(shape))
    if size <= limit:
        return np.asarray(dataset[()])
    slices = []
    product = 1
    for dim in shape:
        remaining = max(1, limit // max(product, 1))
        take = min(dim, remaining)
        slices.append(slice(0, max(1, take)))
        product *= max(1, take)
    return np.asarray(dataset[tuple(slices)])

def collect_hdf5(path):
    import h5py
    out = []
    with h5py.File(path, "r") as handle:
        def visit(name, obj):
            if len(out) >= entry_limit:
                return
            if not hasattr(obj, "dtype") or not hasattr(obj, "shape"):
                return
            dtype = np.dtype(obj.dtype)
            fields = dtype.fields or {}
            if not (np.issubdtype(dtype, np.number) or ("real" in fields and "imag" in fields)):
                return
            data = read_hdf5_preview(obj)
            source_size = int(np.prod(tuple(int(x) for x in obj.shape))) if obj.shape else int(np.asarray(data).size)
            add_array(out, data, f"root.{name}", shape=list(obj.shape), dtype=dtype, source_size=source_size)
        handle.visititems(visit)
    return out

entries = []
backend = ""
errors = []
try:
    import scipy.io
    payload = scipy.io.loadmat(path, struct_as_record=False, squeeze_me=False)
    clean = {str(k): v for k, v in payload.items() if not str(k).startswith("__")}
    collect(clean, "root", entries)
    backend = "scipy.io.loadmat"
except Exception as exc:
    errors.append(f"scipy: {exc}")

if not entries:
    try:
        entries = collect_hdf5(path)
        backend = "h5py"
    except Exception as exc:
        errors.append(f"h5py: {exc}")

if not entries:
    print(json.dumps({"ok": False, "error": "; ".join(errors) or "no numeric arrays"}))
    raise SystemExit(0)

entries.sort(key=lambda item: int(item.get("source_size") or 0), reverse=True)
print(json.dumps({"ok": True, "backend": backend, "entries": entries[:entry_limit]}))
"""
    try:
        result = subprocess.run(
            [
                python,
                "-c",
                script,
                str(path),
                str(_MAX_PREVIEW_POINTS),
                str(_MAX_PREVIEW_ENTRIES),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        warnings.append(f"外部 .mat 解析失败：{exc}")
        return None
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        stderr = result.stderr.strip()[:300]
        warnings.append(f"外部 .mat 解析输出无效：{exc}; stderr={stderr}")
        return None
    if not isinstance(payload, dict) or not payload.get("ok"):
        error = payload.get("error") if isinstance(payload, dict) else "unknown"
        warnings.append(f"外部 .mat 未找到可预览数组：{error}")
        return None
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        return None
    preview_entries = _preview_entries_from_serialized_arrays(raw_entries, default_key=path.name)
    if not preview_entries:
        return None
    backend = str(payload.get("backend") or python)
    warnings.append(f"使用外部 Python 解析 .mat：{python} ({backend})")
    return _profile_from_preview_entries(preview_entries, warnings=warnings)


def _preview_entries_from_serialized_arrays(
    raw_entries: list[Any],
    *,
    default_key: str,
) -> list[dict[str, Any]]:
    preview_entries: list[dict[str, Any]] = []
    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        real = item.get("real")
        imag = item.get("imag")
        if not isinstance(real, list):
            continue
        if isinstance(imag, list) and imag:
            sample = np.asarray(real, dtype=np.float64) + 1j * np.asarray(
                imag,
                dtype=np.float64,
            )
        else:
            sample = np.asarray(real, dtype=np.float64)
        key = item.get("key") or default_key
        preview_entries.append(
            {
                "key": key,
                "preview_key": key,
                "shape": item.get("shape"),
                "dtype": item.get("dtype"),
                "sample_points": item.get("sample_points") or len(real),
                "_source_size": item.get("source_size") or item.get("sample_points") or len(real),
                "_sample": sample,
            }
        )
    return preview_entries


def _paper_static_python() -> str:
    try:
        import yaml
    except ModuleNotFoundError:
        return ""
    path = repo_root() / "configs" / "execution.yaml"
    if not path.is_file():
        return ""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return ""
    execution = raw.get("execution", {}) if isinstance(raw, dict) else {}
    paper = execution.get("paper_static", {}) if isinstance(execution, dict) else {}
    if not isinstance(paper, dict):
        return ""
    return str(os.environ.get("MARS_PAPER_STATIC_PYTHON") or paper.get("python") or "")


def _first_numeric_array(payload: Any) -> np.ndarray[Any, Any] | None:
    if isinstance(payload, np.ndarray):
        return payload if np.issubdtype(payload.dtype, np.number) else None
    if hasattr(payload, "detach") and hasattr(payload, "cpu") and hasattr(payload, "numpy"):
        arr = payload.detach().cpu().numpy()
        return arr if np.issubdtype(arr.dtype, np.number) else None
    if isinstance(payload, dict):
        for value in payload.values():
            arr = _first_numeric_array(value)
            if arr is not None:
                return arr
    if isinstance(payload, (list, tuple)):
        for value in payload:
            arr = _first_numeric_array(value)
            if arr is not None:
                return arr
        try:
            arr = np.asarray(payload)
        except (TypeError, ValueError):
            return None
        return arr if np.issubdtype(arr.dtype, np.number) else None
    return None


def _numeric_arrays(payload: Any, *, key: str = "root") -> list[tuple[str, np.ndarray[Any, Any]]]:
    out: list[tuple[str, np.ndarray[Any, Any]]] = []
    _collect_numeric_arrays(payload, key=key, out=out)
    return out


def _collect_numeric_arrays(
    payload: Any,
    *,
    key: str,
    out: list[tuple[str, np.ndarray[Any, Any]]],
) -> None:
    if len(out) >= _MAX_PREVIEW_ENTRIES:
        return
    if isinstance(payload, np.ndarray):
        if np.issubdtype(payload.dtype, np.number):
            out.append((key, payload))
        elif payload.dtype == object:
            for index, value in enumerate(payload.reshape(-1)):
                _collect_numeric_arrays(value, key=f"{key}[{index}]", out=out)
                if len(out) >= _MAX_PREVIEW_ENTRIES:
                    return
        return
    if hasattr(payload, "detach") and hasattr(payload, "cpu") and hasattr(payload, "numpy"):
        try:
            arr = payload.detach().cpu().numpy()
        except Exception:
            return
        if np.issubdtype(arr.dtype, np.number):
            out.append((key, arr))
        return
    if isinstance(payload, dict):
        for name, value in payload.items():
            _collect_numeric_arrays(value, key=f"{key}.{name}", out=out)
            if len(out) >= _MAX_PREVIEW_ENTRIES:
                return
        return
    if hasattr(payload, "_fieldnames"):
        fields = getattr(payload, "_fieldnames", []) or []
        for name in fields:
            _collect_numeric_arrays(
                getattr(payload, str(name)),
                key=f"{key}.{name}",
                out=out,
            )
            if len(out) >= _MAX_PREVIEW_ENTRIES:
                return
        return
    if isinstance(payload, (list, tuple)):
        before = len(out)
        for index, value in enumerate(payload):
            _collect_numeric_arrays(value, key=f"{key}[{index}]", out=out)
            if len(out) >= _MAX_PREVIEW_ENTRIES:
                return
        if len(out) > before:
            return
        try:
            arr = np.asarray(payload)
        except (TypeError, ValueError):
            return
        if np.issubdtype(arr.dtype, np.number):
            out.append((key, arr))


def _profile_from_numeric_arrays(
    arrays: list[tuple[str, np.ndarray[Any, Any]]],
    *,
    warnings: list[str],
) -> tuple[np.ndarray[Any, Any] | None, dict[str, Any]]:
    preview_entries: list[dict[str, Any]] = []
    ordered = sorted(
        arrays,
        key=lambda item: int(np.asarray(item[1]).size),
        reverse=True,
    )
    for key, arr in ordered[:_MAX_PREVIEW_ENTRIES]:
        sample = _sample_from_array(arr)
        if sample is None:
            continue
        info = _array_info(arr, preview_key=key)
        info["key"] = key
        info["_source_size"] = int(np.asarray(arr).size)
        info["_sample"] = sample
        preview_entries.append(info)
    if len(arrays) > _MAX_PREVIEW_ENTRIES:
        warnings.append(
            f"数值项超过 {_MAX_PREVIEW_ENTRIES} 个，预览仅展示前 {_MAX_PREVIEW_ENTRIES} 个。"
        )
    return _profile_from_preview_entries(preview_entries, warnings=warnings)


def _profile_from_preview_entries(
    preview_entries: list[dict[str, Any]],
    *,
    warnings: list[str],
) -> tuple[np.ndarray[Any, Any] | None, dict[str, Any]]:
    if not preview_entries:
        return None, {"preview_key": ""}
    ordered = sorted(
        preview_entries,
        key=lambda item: int(
            item.get("_source_size") or item.get("sample_points") or 0
        ),
        reverse=True,
    )
    primary = ordered[0]
    sample = primary.get("_sample")
    info = {
        "shape": primary.get("shape"),
        "dtype": primary.get("dtype"),
        "preview_key": primary.get("preview_key") or primary.get("key") or "",
        "sample_points": primary.get("sample_points") or 0,
        "_preview_entries": ordered,
    }
    if isinstance(sample, np.ndarray):
        return sample, info
    return None, info


def _preview_entries_from_inspected(inspected: dict[str, Any]) -> list[dict[str, Any]]:
    raw = inspected.pop("_preview_entries", [])
    if not isinstance(raw, list):
        return []
    entries: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        sample = item.get("_sample")
        if isinstance(sample, np.ndarray):
            entries.append(item)
    return entries


def _public_preview_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    public: list[dict[str, Any]] = []
    for item in entries:
        key = item.get("key") or item.get("preview_key")
        if not key:
            continue
        public.append(
            {
                "key": str(key),
                "shape": item.get("shape"),
                "dtype": item.get("dtype"),
                "sample_points": item.get("sample_points") or 0,
            }
        )
    return public


def _sample_from_array(arr: np.ndarray[Any, Any]) -> np.ndarray[Any, Any] | None:
    data = np.asarray(arr)
    if data.size == 0 or not np.issubdtype(data.dtype, np.number):
        return None
    if np.iscomplexobj(data):
        flat = data.reshape(-1)
    elif data.ndim >= 1 and data.shape[-1] == 2:
        real = np.asarray(data[..., 0], dtype=np.float64)
        imag = np.asarray(data[..., 1], dtype=np.float64)
        flat = (real + 1j * imag).reshape(-1)
    else:
        flat = np.asarray(data, dtype=np.float64).reshape(-1)
    if flat.size > _MAX_PREVIEW_POINTS:
        flat = flat[:_MAX_PREVIEW_POINTS]
    return np.asarray(flat)


def _array_info(arr: np.ndarray[Any, Any], *, preview_key: str) -> dict[str, Any]:
    data = np.asarray(arr)
    return {
        "shape": list(data.shape),
        "dtype": str(data.dtype),
        "preview_key": preview_key,
        "sample_points": int(min(data.size, _MAX_PREVIEW_POINTS)),
    }


def _write_spectrum(
    *,
    sample: np.ndarray[Any, Any],
    fs_mhz: float | None,
    target: Path,
    warnings: list[str],
) -> bool:
    if sample.size < 4:
        warnings.append("样本点过少，无法生成频谱。")
        return False
    n = int(min(sample.size, 8192))
    n = max(4, n)
    values = np.asarray(sample[:n])
    window = np.hanning(n)
    spectrum = np.fft.fftshift(np.fft.fft(values * window))
    magnitude = 20.0 * np.log10(np.abs(spectrum) + 1e-12)
    if fs_mhz and fs_mhz > 0:
        freq = np.fft.fftshift(np.fft.fftfreq(n, d=1.0 / (fs_mhz * 1_000_000.0))) / 1_000_000.0
        x_label = "Frequency (MHz)"
    else:
        freq = np.fft.fftshift(np.fft.fftfreq(n))
        x_label = "Normalized frequency"

    try:
        mpl_dir = Path(tempfile.gettempdir()) / "mars-matplotlib"
        mpl_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("MPLCONFIGDIR", str(mpl_dir))
        import matplotlib

        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt
    except Exception as exc:
        warnings.append(f"matplotlib 不可用，使用简易 PNG 频谱预览：{exc}")
        _write_simple_series_png(values=[float(v) for v in magnitude], path=target)
        return target.is_file()

    target.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(8, 3), dpi=150)
    ax.plot(freq, magnitude, color="#60a5fa", linewidth=1.0)
    ax.set_xlabel(x_label)
    ax.set_ylabel("Magnitude (dB)")
    ax.grid(True, color="#334155", alpha=0.35)
    ax.set_title("Dataset spectrum preview")
    fig.tight_layout()
    fig.savefig(target)
    plt.close(fig)
    return target.is_file()


def _write_spectrum_overview(
    *,
    entries: list[dict[str, Any]],
    fs_mhz: float | None,
    target: Path,
    warnings: list[str],
) -> bool:
    plotted: list[tuple[str, Any, np.ndarray[Any, Any], np.ndarray[Any, Any]]] = []
    for entry in entries:
        sample = entry.get("_sample")
        if not isinstance(sample, np.ndarray):
            continue
        components = _spectrum_components(sample=sample, fs_mhz=fs_mhz)
        if components is None:
            continue
        freq, magnitude, _ = components
        label = str(entry.get("key") or entry.get("preview_key") or "array")
        plotted.append((label, entry.get("shape"), freq, magnitude))

    if not plotted:
        warnings.append("样本点过少，无法生成字典预览。")
        return False

    try:
        mpl_dir = Path(tempfile.gettempdir()) / "mars-matplotlib"
        mpl_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("MPLCONFIGDIR", str(mpl_dir))
        import matplotlib

        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt
    except Exception as exc:
        if _write_spectrum_overview_external(
            plotted=plotted,
            fs_mhz=fs_mhz,
            target=target,
            warnings=warnings,
        ):
            return True
        warnings.append(f"matplotlib 不可用，使用简易 PNG 字典预览：{exc}")
        _write_simple_series_grid_png(
            series=[[float(value) for value in magnitude] for _, _, _, magnitude in plotted],
            path=target,
        )
        return target.is_file()

    count = len(plotted)
    cols = min(3, count)
    rows = (count + cols - 1) // cols
    target.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(
        rows,
        cols,
        figsize=(4.2 * cols, 2.45 * rows),
        dpi=145,
        squeeze=False,
    )
    for index, (label, shape, freq, magnitude) in enumerate(plotted):
        row = index // cols
        col = index % cols
        ax = axes[row][col]
        ax.plot(freq, magnitude, color="#60a5fa", linewidth=0.85)
        ax.grid(True, color="#334155", alpha=0.35)
        ax.set_title(_short_plot_title(label, shape), fontsize=8)
        ax.tick_params(axis="both", labelsize=7)
        if row == rows - 1:
            ax.set_xlabel("MHz" if fs_mhz and fs_mhz > 0 else "Normalized", fontsize=7)
        if col == 0:
            ax.set_ylabel("dB", fontsize=7)
    for index in range(count, rows * cols):
        row = index // cols
        col = index % cols
        axes[row][col].axis("off")
    fig.suptitle("Dataset dictionary spectrum overview", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(target)
    plt.close(fig)
    return target.is_file()


def _write_spectrum_overview_external(
    *,
    plotted: list[tuple[str, Any, np.ndarray[Any, Any], np.ndarray[Any, Any]]],
    fs_mhz: float | None,
    target: Path,
    warnings: list[str],
) -> bool:
    python = _paper_static_python()
    if not python:
        return False
    candidate = Path(python).expanduser()
    if candidate.is_absolute() and not candidate.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    payload_path = target.with_name(f".{target.name}.{uuid4().hex}.json")
    payload = {
        "fs_mhz": fs_mhz,
        "series": [
            {
                "label": label,
                "shape": shape,
                "freq": freq.astype(float).tolist(),
                "magnitude": magnitude.astype(float).tolist(),
            }
            for label, shape, freq, magnitude in plotted
        ],
    }
    payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    script = r"""
import json
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

payload_path = sys.argv[1]
target = sys.argv[2]
with open(payload_path, "r", encoding="utf-8") as fh:
    payload = json.load(fh)
series = payload.get("series") or []
count = len(series)
if count == 0:
    raise SystemExit(2)
cols = min(3, count)
rows = (count + cols - 1) // cols
fig, axes = plt.subplots(rows, cols, figsize=(4.2 * cols, 2.45 * rows), dpi=145, squeeze=False)
colors = ["#60a5fa", "#22d3ee", "#34d399", "#fbbf24", "#f87171", "#a78bfa"]

def short_title(label, shape):
    text = str(label)
    if len(text) > 42:
        text = "..." + text[-39:]
    if shape not in (None, "", []):
        text += " " + str(shape)
    return text

for index, item in enumerate(series):
    row = index // cols
    col = index % cols
    ax = axes[row][col]
    ax.plot(item["freq"], item["magnitude"], color=colors[index % len(colors)], linewidth=0.85)
    ax.grid(True, color="#334155", alpha=0.35)
    ax.set_title(short_title(item.get("label", "array"), item.get("shape")), fontsize=8)
    ax.tick_params(axis="both", labelsize=7)
    if row == rows - 1:
        ax.set_xlabel("MHz" if payload.get("fs_mhz") else "Normalized", fontsize=7)
    if col == 0:
        ax.set_ylabel("dB", fontsize=7)
for index in range(count, rows * cols):
    axes[index // cols][index % cols].axis("off")
fig.suptitle("Dataset dictionary spectrum overview", fontsize=10)
fig.tight_layout(rect=(0, 0, 1, 0.96))
fig.savefig(target)
plt.close(fig)
"""
    try:
        result = subprocess.run(
            [python, "-c", script, str(payload_path), str(target)],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        warnings.append(f"外部 matplotlib 预览失败：{exc}")
        return False
    finally:
        try:
            payload_path.unlink()
        except OSError:
            pass
    if result.returncode != 0:
        stderr = result.stderr.strip()[:300]
        warnings.append(f"外部 matplotlib 预览失败：{stderr or result.returncode}")
        return False
    warnings.append(f"使用外部 Python/matplotlib 生成字典预览：{python}")
    return target.is_file()


def _spectrum_components(
    *,
    sample: np.ndarray[Any, Any],
    fs_mhz: float | None,
) -> tuple[np.ndarray[Any, Any], np.ndarray[Any, Any], str] | None:
    if sample.size < 4:
        return None
    n = int(min(sample.size, _MAX_PLOT_POINTS))
    n = max(4, n)
    values = np.asarray(sample[:n])
    window = np.hanning(n)
    spectrum = np.fft.fftshift(np.fft.fft(values * window))
    magnitude = 20.0 * np.log10(np.abs(spectrum) + 1e-12)
    if fs_mhz and fs_mhz > 0:
        freq = (
            np.fft.fftshift(np.fft.fftfreq(n, d=1.0 / (fs_mhz * 1_000_000.0)))
            / 1_000_000.0
        )
        x_label = "Frequency (MHz)"
    else:
        freq = np.fft.fftshift(np.fft.fftfreq(n))
        x_label = "Normalized frequency"
    return freq, magnitude, x_label


def _short_plot_title(label: str, shape: Any) -> str:
    clean = label if len(label) <= 42 else "..." + label[-39:]
    if shape not in (None, "", []):
        clean = f"{clean} {shape}"
    return clean


def _write_simple_series_png(*, values: list[float], path: Path) -> None:
    width = 720
    height = 320
    margin = 34
    pixels = bytearray([15, 23, 42] * width * height)

    def put(x: int, y: int, color: tuple[int, int, int]) -> None:
        if 0 <= x < width and 0 <= y < height:
            offset = (y * width + x) * 3
            pixels[offset : offset + 3] = bytes(color)

    for x in range(margin, width - margin):
        put(x, height - margin, (71, 85, 105))
    for y in range(margin, height - margin):
        put(margin, y, (71, 85, 105))
    if not values:
        values = [0.0]
    low = min(values)
    high = max(values)
    span = max(high - low, 1e-9)
    x_span = max(len(values) - 1, 1)
    points = [
        (
            margin + int((width - 2 * margin) * index / x_span),
            height - margin - int((height - 2 * margin) * (value - low) / span),
        )
        for index, value in enumerate(values)
    ]
    for left, right in zip(points, points[1:], strict=False):
        _draw_png_line(pixels, width=width, height=height, start=left, end=right)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(
        f".{target.name}.{datetime.now(tz=timezone.utc).timestamp()}.tmp"
    )
    tmp.write_bytes(_png_bytes(width=width, height=height, pixels=pixels))
    tmp.replace(target)


def _write_simple_series_grid_png(*, series: list[list[float]], path: Path) -> None:
    count = max(1, len(series))
    cols = min(3, count)
    rows = (count + cols - 1) // cols
    panel_width = 360
    panel_height = 220
    width = panel_width * cols
    height = panel_height * rows
    pixels = bytearray([15, 23, 42] * width * height)
    colors = [
        (96, 165, 250),
        (34, 211, 238),
        (52, 211, 153),
        (251, 191, 36),
        (248, 113, 113),
        (167, 139, 250),
    ]

    def put(x: int, y: int, color: tuple[int, int, int]) -> None:
        if 0 <= x < width and 0 <= y < height:
            offset = (y * width + x) * 3
            pixels[offset : offset + 3] = bytes(color)

    for index, values in enumerate(series):
        row = index // cols
        col = index % cols
        x0 = col * panel_width
        y0 = row * panel_height
        margin = 26
        left = x0 + margin
        right = x0 + panel_width - margin
        top = y0 + margin
        bottom = y0 + panel_height - margin
        for x in range(left, right):
            put(x, bottom, (71, 85, 105))
        for y in range(top, bottom):
            put(left, y, (71, 85, 105))
        if not values:
            values = [0.0]
        low = min(values)
        high = max(values)
        span = max(high - low, 1e-9)
        x_span = max(len(values) - 1, 1)
        points = [
            (
                left + int((right - left) * point_index / x_span),
                bottom - int((bottom - top) * (value - low) / span),
            )
            for point_index, value in enumerate(values)
        ]
        color = colors[index % len(colors)]
        for start, end in zip(points, points[1:], strict=False):
            _draw_png_line(
                pixels,
                width=width,
                height=height,
                start=start,
                end=end,
                color=color,
            )

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(
        f".{target.name}.{datetime.now(tz=timezone.utc).timestamp()}.tmp"
    )
    tmp.write_bytes(_png_bytes(width=width, height=height, pixels=pixels))
    tmp.replace(target)


def _draw_png_line(
    pixels: bytearray,
    *,
    width: int,
    height: int,
    start: tuple[int, int],
    end: tuple[int, int],
    color: tuple[int, int, int] = (96, 165, 250),
) -> None:
    x0, y0 = start
    x1, y1 = end
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        if 0 <= x0 < width and 0 <= y0 < height:
            offset = (y0 * width + x0) * 3
            pixels[offset : offset + 3] = bytes(color)
        if x0 == x1 and y0 == y1:
            break
        doubled = 2 * err
        if doubled >= dy:
            err += dy
            x0 += sx
        if doubled <= dx:
            err += dx
            y0 += sy


def _png_bytes(*, width: int, height: int, pixels: bytearray) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFF_FFFF)
        )

    rows = b"".join(
        b"\x00" + bytes(pixels[y * width * 3 : (y + 1) * width * 3])
        for y in range(height)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, level=6))
        + chunk(b"IEND", b"")
    )


def _safe_filename(value: str) -> str:
    name = Path(value).name.strip() or "dataset.bin"
    clean = _SAFE_NAME_RE.sub("_", name).strip("._")
    return clean or "dataset.bin"


def _safe_stem(value: str) -> str:
    clean = _SAFE_NAME_RE.sub("_", value).strip("._")
    return clean[:48] or "dataset"


def _safe_id(value: str) -> str:
    return _SAFE_ID_RE.sub("_", value).strip("_")


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
