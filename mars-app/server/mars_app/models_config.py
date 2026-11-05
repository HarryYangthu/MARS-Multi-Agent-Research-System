"""Model connection settings: the models.yaml that MARS_MODELS_CONFIG points the core at.

Contract with the deployment line (mars-deploy/docs/models.md): the app reads and writes only that file and the env
file(s) it lists under env_files; it never writes into mars-core/configs. API keys go only into the env file
(owner-only permissions) and the API only ever says whether a key is set, never its value. "Test connection" runs
mars-deploy's checks/check_models.py, which makes real requests and suggests settings from what it observes.
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import yaml

ROLES = ("strong", "fast", "coder")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
NAME = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
PROVIDER_FIELDS = {"api_base", "api_key_env"}
# single_user: gateways that accept only one user message (the core folds system + history into it)
ROLE_FIELDS = {"provider", "model", "max_tokens", "timeout", "idle_timeout", "stream", "json_mode", "thinking",
               "reasoning_effort", "extra_body", "concurrency", "single_user"}
POSITIVE_INTS = ("max_tokens", "timeout", "idle_timeout", "concurrency")
BACKUPS_KEPT = 5
HEADER = "# 由 mars-app 模型连接配置页保存；上一版本备份在同目录 *.bak-<时间>。字段说明见 mars-deploy/docs/models.md。\n"


def config_path() -> Path | None:
    raw = os.environ.get("MARS_MODELS_CONFIG", "").strip()
    return Path(raw).expanduser().resolve() if raw else None


def load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} 不是 YAML 映射")
    return data


def env_paths(cfg: dict[str, Any], path: Path) -> list[Path]:
    return [(path.parent / str(p)).resolve() for p in cfg.get("env_files") or []]


def read_env(path: Path) -> dict[str, str]:
    """KEY=VALUE lines, parsed the way mars-core's load_env_files does (quotes stripped, comments skipped)."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def key_status(cfg: dict[str, Any], path: Path) -> dict[str, dict[str, Any]]:
    """Per api_key_env: whether it is set and where. The process environment wins over the file in the core
    (load_env_files uses setdefault), so both are reported. Values are never returned."""
    files = [(p, read_env(p)) for p in env_paths(cfg, path)]
    status = {}
    for provider in (cfg.get("providers") or {}).values():
        name = str((provider or {}).get("api_key_env") or "")
        if not name:
            continue
        in_file = next((str(p) for p, values in files if values.get(name)), None)
        status[name] = {"set": bool(in_file or os.environ.get(name)), "in_file": in_file,
                        "in_environment": bool(os.environ.get(name))}
    return status


def view(core_root: Path) -> dict[str, Any]:
    path = config_path()
    editable = path is not None
    source = path if editable else core_root / "configs/models.yaml"
    try:
        cfg = load(source)
        error = None
    except (OSError, ValueError, yaml.YAMLError) as exc:
        cfg, error = {}, f"{type(exc).__name__}: {exc}"
    return {"editable": editable, "path": str(source), "exists": source.exists(), "error": error,
            "env_files": [str(p) for p in env_paths(cfg, source)] if cfg else [],
            "providers": cfg.get("providers") or {}, "roles": cfg.get("roles") or {},
            "fallbacks": cfg.get("fallbacks") or {}, "retries": cfg.get("retries"),
            "keys": key_status(cfg, source) if cfg else {}}


def _clean(entry: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    """Known fields from the form; None or "" means "not set" and removes the field."""
    return {k: v for k, v in entry.items() if k in allowed and v is not None and v != ""}


def merge(original: dict[str, Any], edited: dict[str, Any]) -> dict[str, Any]:
    """Apply the form to the file: fields the app does not know (and top-level keys such as env_files) are kept."""
    merged = copy.deepcopy(original)
    for section, allowed in (("providers", PROVIDER_FIELDS), ("roles", ROLE_FIELDS)):
        before = original.get(section) or {}
        def prior(name: str) -> dict[str, Any]:
            source = (original.get("roles") or {}).get(name, {}).get("provider") if section == "providers" else None
            return before.get(name) or before.get(source) or {}
        merged[section] = {name: {**{k: v for k, v in prior(name).items() if k not in allowed},
                                  **_clean(entry or {}, allowed)}
                           for name, entry in (edited.get(section) or {}).items()}
    merged["fallbacks"] = {k: v for k, v in (edited.get("fallbacks") or {}).items() if v}
    if edited.get("retries") is not None:
        merged["retries"] = edited["retries"]
    return merged


def validate(cfg: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    providers, roles = cfg.get("providers") or {}, cfg.get("roles") or {}
    if not providers:
        problems.append("至少需要一个模型服务（provider）")
    for name, p in providers.items():
        if not NAME.match(str(name)):
            problems.append(f"服务名 {name} 只能包含字母、数字、下划线和连字符")
        if not re.match(r"^https?://\S+$", str(p.get("api_base") or "")):
            problems.append(f"{name}：地址必须以 http:// 或 https:// 开头")
        if not ENV_NAME.match(str(p.get("api_key_env") or "")):
            problems.append(f"{name}：Key 变量名必须是字母、数字、下划线组成的环境变量名")
    for role in ROLES:
        if role not in roles:
            problems.append(f"缺少角色 {role}")
    for name, r in roles.items():
        if r.get("provider") not in providers:
            problems.append(f"{name}：服务 {r.get('provider')} 不存在")
        if not str(r.get("model") or "").strip():
            problems.append(f"{name}：模型 ID 不能为空")
        for key in POSITIVE_INTS:
            if key in r and (isinstance(r[key], bool) or not isinstance(r[key], int) or r[key] <= 0):
                problems.append(f"{name}：{key} 必须是正整数")
        for key in ("max_tokens", "timeout"):
            if key not in r:
                problems.append(f"{name}：缺少 {key}")
        for key in ("stream", "json_mode", "thinking", "single_user"):
            if key in r and not isinstance(r[key], bool):
                problems.append(f"{name}：{key} 只能是 true / false 或不设置")
        if "extra_body" in r and not isinstance(r["extra_body"], dict):
            problems.append(f"{name}：extra_body 必须是映射（JSON 对象）")
    for source, target in (cfg.get("fallbacks") or {}).items():
        if source not in roles or target not in roles or source == target:
            problems.append(f"降级 {source} → {target} 无效")
    retries = cfg.get("retries")
    if retries is not None and (isinstance(retries, bool) or not isinstance(retries, int) or not 0 <= retries <= 10):
        problems.append("重试次数必须是 0–10 的整数")
    return problems


def _backup(path: Path) -> None:
    if not path.exists():
        return
    shutil.copy2(path, path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}"))
    for old in sorted(path.parent.glob(f"{path.name}.bak-*"))[:-BACKUPS_KEPT]:
        old.unlink(missing_ok=True)


def save(path: Path, cfg: dict[str, Any]) -> None:
    problems = validate(cfg)
    if problems:
        raise ValueError("；".join(problems))
    _backup(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(HEADER + yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    tmp.replace(path)


def _owner_only(path: Path) -> str | None:
    """Restrict a secrets file to the current user; returns a warning if that could not be done."""
    try:
        if os.name == "nt":
            user = os.environ.get("USERNAME", "")
            result = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                                    capture_output=True, text=True, check=False)
            return None if result.returncode == 0 else f"icacls 失败：{result.stderr.strip()[:200]}"
        path.chmod(0o600)
        return None
    except OSError as error:
        return f"无法限制文件权限：{error}"


def set_key(path: Path, cfg: dict[str, Any], name: str, value: str) -> dict[str, Any]:
    """Write (or with an empty value, remove) one key in the env file that holds it, else the first env file."""
    known = {str((p or {}).get("api_key_env")) for p in (cfg.get("providers") or {}).values()}
    if name not in known:
        raise ValueError(f"{name} 不是配置里任何服务的 Key 变量")
    if any(c in value for c in "\r\n"):
        raise ValueError("Key 不能包含换行")
    files = env_paths(cfg, path)
    if not files:
        raise ValueError("配置里没有 env_files，无法保存 Key；请先在配置文件里加上 env_files（例如 models.env）")
    target = next((p for p in files if name in read_env(p)), files[0])
    lines = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
    kept = [line for line in lines if line.split("=", 1)[0].strip() != name]
    if value:
        kept.append(f"{name}={value.strip()}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    return {"env_file": str(target), "warning": _owner_only(target)}


class CheckRunner:
    """Runs check_models.py in the background (up to 12 short real requests per role, possibly taking minutes)."""

    def __init__(self, core_root: Path, deploy_root: Path, out_dir: Path) -> None:
        self.core_root, self.deploy_root, self.out_dir = core_root, deploy_root, out_dir
        self._checks: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    @property
    def script(self) -> Path:
        return self.deploy_root / "checks/check_models.py"

    def python(self) -> str:
        """The core's environment (check_models imports mars.llm and litellm); in a deployment the app shares it."""
        for candidate in (self.core_root / ".venv/bin/python", self.core_root / ".venv/Scripts/python.exe"):
            if candidate.exists():
                return str(candidate)
        return sys.executable

    def start(self, role: str, skip_thinking: bool) -> str:
        if role not in ROLES:
            raise ValueError(f"未知角色 {role}")
        if not self.script.is_file():
            raise ValueError(f"找不到连接检查脚本 {self.script}（由 mars-deploy 提供）")
        with self._lock:
            if any(c["role"] == role and c["status"] == "running" for c in self._checks.values()):
                raise RuntimeError(f"{role} 的检查正在进行")
            cid = f"{time.strftime('%Y%m%d-%H%M%S')}-{role}"
            self._checks[cid] = {"id": cid, "role": role, "status": "running", "started": time.strftime("%Y-%m-%d %H:%M:%S"),
                                 "skip_thinking": skip_thinking, "report": None, "error": None, "config": str(config_path() or "")}
        threading.Thread(target=self._run, args=(cid,), name=f"model-check-{cid}", daemon=True).start()
        return cid

    def _run(self, cid: str) -> None:
        check = self._checks[cid]
        out = self.out_dir / cid
        command = [self.python(), str(self.script), "--role", check["role"], "--out", str(out)]
        if check["skip_thinking"]:
            command.append("--skip-thinking")
        env = {**os.environ, "MARS_CORE_ROOT": str(self.core_root), "PYTHONUTF8": "1"}
        try:
            result = subprocess.run(command, cwd=self.deploy_root, env=env, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=3600)
            reports = sorted(out.glob("models-*.json"))
            if reports:
                check["report"] = json.loads(reports[-1].read_text(encoding="utf-8")).get(check["role"])
            check["passed"] = result.returncode == 0
            check["status"] = "done" if reports else "failed"
            if not reports:
                check["error"] = (result.stderr or result.stdout).strip()[-1500:] or f"退出码 {result.returncode}"
        except (OSError, subprocess.TimeoutExpired, ValueError) as error:
            check.update(status="failed", error=f"{type(error).__name__}: {error}"[:800])
        check["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")

    def get(self, cid: str) -> dict[str, Any]:
        if cid not in self._checks:
            raise KeyError(cid)
        return self._checks[cid]

    def latest(self) -> dict[str, dict[str, Any]]:
        """The most recent check per role (this app session)."""
        found: dict[str, dict[str, Any]] = {}
        for check in sorted(self._checks.values(), key=lambda c: c["id"]):
            found[check["role"]] = check
        return found
