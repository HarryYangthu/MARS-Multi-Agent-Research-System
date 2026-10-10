"""Per-project execution target: local simulation by default, optional remote GPU.

The wizard writes ``<project metadata>/execution.yaml`` (schema
``project_execution.v1``). Absent file means ``local`` — the project runs its
simulation on this machine exactly as before. Selecting ``remote_gpu`` records
the SSH prerequisites for the project and is validated fail-closed against the
same rules as the global readiness check (binary presence, key files, host and
user format) without any network I/O; the actual remote dispatch remains the
existing ``remote_gpu`` execution machinery.
"""
from __future__ import annotations

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from app.harness.persistence import atomic_write_text, path_lock
from app.harness.project_workspace import project_root
from app.settings import get_settings
from app.execution.remote.executor import RemoteExecutorConfig, load_remote_executor_config

_SCHEMA = "project_execution.v1"
_HOST_RE = re.compile(r"[A-Za-z0-9._-]+|\[[0-9a-fA-F:]+\]")
_USER_RE = re.compile(r"[a-z_][a-z0-9._-]*", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[A-Za-z0-9_./-]+")


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _config_path(project: str) -> Path:
    root = project_root(project)
    if not root.is_dir():
        raise ValueError("项目不存在")
    return root / "execution.yaml"


def load_execution_config(project: str) -> dict[str, Any]:
    """Absent file means local simulation; the default never changes silently."""
    return load_execution_file(_config_path(project))


def load_execution_file(path: Path) -> dict[str, Any]:
    """Read a real file, shared by product resolution and filesystem checks."""
    if not path.is_file():
        return {"schema": _SCHEMA, "device": "local"}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema") != _SCHEMA:
        raise ValueError("无效的项目执行配置 execution.yaml")
    device = raw.get("device")
    if device not in {"local", "remote_gpu"}:
        raise ValueError("execution.yaml 的 device 必须是 local 或 remote_gpu")
    if device == "local":
        return {"schema": _SCHEMA, "device": "local", "updated_at": str(raw.get("updated_at", ""))}
    remote = raw.get("remote_gpu", {})
    if not isinstance(remote, dict):
        raise ValueError("无效的 remote_gpu 配置")
    return {"schema": _SCHEMA, "device": device,
            "remote_gpu": _validate_remote(remote, files_required=False),
            "updated_at": str(raw.get("updated_at", ""))}


def save_execution_config(project: str, payload: dict[str, Any]) -> dict[str, Any]:
    return save_execution_file(_config_path(project), payload)


def save_execution_file(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    device = payload.get("device")
    if device not in {"local", "remote_gpu"}:
        raise ValueError("device 必须是 local 或 remote_gpu")
    remote: dict[str, Any] = {}
    if device == "remote_gpu":
        supplied = payload.get("remote_gpu")
        if not isinstance(supplied, dict):
            raise ValueError("选择 GPU 时必须提供 remote_gpu 配置")
        remote = _validate_remote(supplied)
    record = {"schema": _SCHEMA, "device": device,
              "updated_at": _now(), **({"remote_gpu": remote} if remote else {})}
    with path_lock(path.with_name(f".{path.name}.lock")):
        atomic_write_text(path, yaml.safe_dump(record, allow_unicode=True, sort_keys=False))
    return load_execution_file(path)


def _validate_remote(supplied: dict[str, Any], *, files_required: bool = True) -> dict[str, Any]:
    def text(key: str) -> str:
        value = supplied.get(key)
        return str(value).strip() if isinstance(value, str) else ""

    host, user = text("host"), text("user")
    if not host or _HOST_RE.fullmatch(host) is None:
        raise ValueError("GPU 主机名无效")
    if not user or _USER_RE.fullmatch(user) is None:
        raise ValueError("GPU SSH 用户名无效")
    port_value = supplied.get("port", "22")
    port_raw = str(port_value).strip() if type(port_value) in {int, str} else ""
    if not port_raw.isdigit() or not 1 <= int(port_raw) <= 65535:
        raise ValueError("GPU SSH 端口必须是 1-65535 的整数")
    key_path = Path(text("key_path")).expanduser() if text("key_path") else None
    if key_path is None or not key_path.is_absolute() or (files_required and not key_path.is_file()):
        raise ValueError("SSH 私钥路径必须是本机已存在的绝对路径文件")
    known_hosts = Path(text("known_hosts")).expanduser() if text("known_hosts") else None
    if known_hosts is None or not known_hosts.is_absolute() or (files_required and not known_hosts.is_file()):
        raise ValueError("known_hosts 路径必须是本机已存在的绝对路径文件")
    remote_root = text("remote_root")
    if (not remote_root.startswith("/") or ".." in PurePosixPath(remote_root).parts
            or str(PurePosixPath(remote_root)) != remote_root
            or _TOKEN_RE.fullmatch(remote_root) is None):
        raise ValueError("远端工作根目录必须是远端机上的绝对路径")
    python = text("python") or "python3"
    if _TOKEN_RE.fullmatch(python) is None:
        raise ValueError("远端 Python 解释器路径无效")
    gpu_ids = text("gpu_ids")
    ids = [item.strip() for item in gpu_ids.split(",")] if gpu_ids else []
    if any(not item.isdigit() for item in ids) or len(set(ids)) != len(ids):
        raise ValueError("GPU 编号格式无效（应为逗号分隔的编号）")
    return {"host": host, "port": int(port_raw), "user": user,
            "key_path": str(key_path), "known_hosts": str(known_hosts),
            "remote_root": remote_root, "python": python, "gpu_ids": gpu_ids}


def execution_config_status(project: str) -> dict[str, Any]:
    """Config plus fail-closed local prerequisite findings; no network I/O."""
    config = load_execution_config(project)
    settings = get_settings()
    remote_config = load_remote_executor_config() if config["device"] == "remote_gpu" else None
    return execution_status(config, runtime_backend=settings.mars_execution_backend,
                            remote_capable=settings.effective_execution_device == "gpu",
                            dispatched=remote_config)


def execution_status(config: dict[str, Any], *, runtime_backend: str,
                     remote_capable: bool,
                     dispatched: RemoteExecutorConfig | None = None) -> dict[str, Any]:
    """Check local prerequisites and the exact dispatcher binding, never SSH health."""
    findings: list[str] = []
    missing: list[str] = []
    if config["device"] == "local" and remote_capable:
        findings.append("local_project_remote_runtime")
    if config["device"] == "remote_gpu":
        remote = config.get("remote_gpu", {})
        for key, label in (("host", "GPU 主机"), ("user", "SSH 用户"),
                           ("key_path", "SSH 私钥"), ("known_hosts", "known_hosts"),
                           ("remote_root", "远端工作根目录")):
            if not str(remote.get(key, "")).strip():
                missing.append(label)
        if shutil.which("ssh") is None:
            findings.append("ssh_binary_missing")
        if shutil.which("scp") is None:
            findings.append("scp_binary_missing")
        for key, finding in (("key_path", "ssh_key_path_not_file"),
                             ("known_hosts", "known_hosts_path_not_file")):
            value = str(remote.get(key, "")).strip()
            if value and not Path(value).expanduser().is_file():
                findings.append(finding)
        if not remote_capable:
            findings.append("remote_runtime_not_enabled")
        if dispatched is None or not dispatched.enabled:
            findings.append("remote_dispatch_not_enabled")
        elif dispatched.auth_method != "key" or dispatched.transport != "system_ssh":
            findings.append("remote_dispatch_auth_not_compatible")
        else:
            expected = {"host": dispatched.host, "port": dispatched.port,
                        "user": dispatched.user, "key_path": str(dispatched.key_path or ""),
                        "known_hosts": str(dispatched.known_hosts_path or ""),
                        "remote_root": dispatched.remote_root, "python": dispatched.python,
                        "gpu_ids": ",".join(dispatched.gpu_ids)}
            for key, expected_value in expected.items():
                if str(remote.get(key, "")) != str(expected_value):
                    findings.append(f"remote_dispatch_mismatch:{key}")
    return {
        **config,
        "ready": not missing and not findings,
        "missing": missing,
        "findings": findings,
        "runtime_backend": runtime_backend,
        "remote_capable_runtime": remote_capable,
        "connection_verified": False,
    }


def require_project_execution(project: str) -> None:
    """Apply explicit project presets at the shared UI/Commander/CLI boundary."""
    if not _config_path(project).is_file():
        return  # Existing projects retain their explicitly configured runtime.
    status = execution_config_status(project)
    if not status["ready"]:
        raise ValueError("项目执行环境尚未核对：" + "；".join([*status["missing"], *status["findings"]]))
