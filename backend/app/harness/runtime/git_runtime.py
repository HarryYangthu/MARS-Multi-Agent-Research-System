"""Verified host Git selection shared by branches, patches and source history.

Never invokes a shell, installs software or accepts a platform license. Explicit
configuration fails closed; discovery skips unusable candidates on PATH.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import subprocess
import sys
from threading import RLock
import time
from typing import Any

import yaml

from app.harness.tools.process_runtime import sanitized_subprocess_environment
from app.settings import env_or_local, repo_root


class GitRuntimeError(ValueError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.reason = {"code": code}


@dataclass(frozen=True)
class GitRuntime:
    executable: str
    version: str
    source: str


def git_failure(stderr: str, *, returncode: int | None = None) -> GitRuntimeError:
    """Classify known errors without exposing arbitrary hook output or secrets."""
    text = stderr.casefold()
    if "xcode" in text and "license" in text:
        return GitRuntimeError("系统 Git 被 Xcode 许可检查阻止。请安装独立 Git，或通过 MARS_GIT_EXECUTABLE 指定可用 Git。",
                               "git_license_required")
    if "permission denied" in text or "access is denied" in text:
        return GitRuntimeError("Git 无法访问代码仓，请检查文件权限。", "git_permission_denied")
    if "dubious ownership" in text:
        return GitRuntimeError("Git 检测到代码仓所有者不匹配，请核对目录归属与 Git 信任设置。", "git_unsafe_repository")
    if "index.lock" in text or "another git process" in text:
        return GitRuntimeError("代码仓正在被其他 Git 操作占用，请等待完成后重试。", "git_repository_locked")
    if "not a git repository" in text:
        return GitRuntimeError("所选目录不是 Git 代码仓，请重新关联已提交基线的代码仓。", "git_repository_missing")
    if "unknown option" in text or "unknown switch" in text or "not a git command" in text:
        return GitRuntimeError("当前 Git 不支持所需操作，请升级 Git 或配置其他 Git 路径。", "git_unsupported")
    return GitRuntimeError(f"Git 操作失败（退出码 {returncode}），请检查仓库状态；未强制切换或重置。",
                           "git_operation_failed")


def candidate_paths(*, platform: str, environment: Mapping[str, str],
                    fallback_paths: Sequence[str]) -> tuple[str, ...]:
    """Pure platform path planning; never implicitly search the working directory."""
    windows = platform == "win32"
    path_type = PureWindowsPath if windows else PurePosixPath
    name, separator = ("git.exe", ";") if windows else ("git", ":")
    candidates: list[str] = []
    for directory in environment.get("PATH", "").split(separator):
        base = path_type(directory.strip('"'))
        if directory and base.is_absolute():
            candidates.append(str(base / name))
    for template in fallback_paths:
        fields = re.findall(r"\{([^}]+)\}", template)
        if any(not environment.get(field) for field in fields):
            continue
        value = template
        for field in fields:
            value = value.replace("{" + field + "}", environment[field])
        if path_type(value).is_absolute():
            candidates.append(str(path_type(value)))
    seen: set[str] = set()
    result: list[str] = []
    for candidate in candidates:
        key = candidate.casefold() if windows else candidate
        if key not in seen:
            seen.add(key)
            result.append(candidate)
    return tuple(result)


def git_environment(environment: Mapping[str, str] | None = None) -> dict[str, str]:
    source = sanitized_subprocess_environment(inherited=environment)
    # Prevent ambient repository routing and numbered config injection. Preserve
    # explicit archive isolation settings (GIT_CONFIG_GLOBAL / NOSYSTEM).
    excluded = {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
                "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS"}
    result = {key: value for key, value in source.items() if key.upper() not in excluded
              and not key.upper().startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))}
    result["GIT_TERMINAL_PROMPT"] = "0"
    return result


def _policy(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("fallback_paths"), dict):
            raise ValueError("policy")
        for key in ("probe_timeout_seconds", "command_timeout_seconds", "cache_seconds"):
            if type(raw.get(key)) not in (int, float) or not 0 < raw[key] <= 300:
                raise ValueError("timeout")
        for values in raw["fallback_paths"].values():
            if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
                raise ValueError("paths")
        return dict(raw)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise GitRuntimeError("Git 运行配置缺失或无效，请检查 configs/git_runtime.yaml。", "git_config_invalid") from exc


_LOCK = RLock()
_CACHE: dict[tuple[object, ...], tuple[float, GitRuntime | GitRuntimeError]] = {}


def resolve_git(*, environment: Mapping[str, str] | None = None,
                config_path: Path | None = None) -> GitRuntime:
    env = os.environ if environment is None else environment
    policy_path = config_path or repo_root() / "configs/git_runtime.yaml"
    policy = _policy(policy_path)
    configured = env.get("MARS_GIT_EXECUTABLE", env_or_local("MARS_GIT_EXECUTABLE")).strip()
    paths = (str(Path(configured).expanduser()),) if configured else candidate_paths(
        platform=sys.platform, environment=env, fallback_paths=policy["fallback_paths"].get(sys.platform, []))
    if configured and not Path(paths[0]).is_absolute():
        raise GitRuntimeError("MARS_GIT_EXECUTABLE 必须是 Git 可执行文件的完整路径。", "git_path_invalid")
    stamps: list[tuple[str, int, int]] = []
    for path in paths:
        try:
            stat = Path(path).stat()
            stamps.append((path, stat.st_mtime_ns, stat.st_size))
        except OSError:
            stamps.append((path, 0, 0))
    key = (configured, tuple(stamps), env.get("PATH", ""),
           float(policy["probe_timeout_seconds"]), str(policy_path))
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and time.monotonic() < cached[0]:
            if isinstance(cached[1], GitRuntimeError):
                raise GitRuntimeError(str(cached[1]), cached[1].reason["code"])
            return cached[1]
        failures: list[GitRuntimeError] = []
        selected: GitRuntime | None = None
        for path in paths:
            if not Path(path).is_file():
                continue
            try:
                probe = subprocess.run([path, "--version"], cwd=repo_root(), env=git_environment(env),
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=policy["probe_timeout_seconds"])
                if probe.returncode:
                    failures.append(git_failure(probe.stderr, returncode=probe.returncode))
                elif re.fullmatch(r"git version \d+\.\d+[^\r\n]*", probe.stdout.strip()):
                    selected = GitRuntime(path, probe.stdout.strip(), "configured" if configured else "discovered")
                    break
                else:
                    failures.append(GitRuntimeError("所选文件不是可用的 Git 程序。", "git_executable_invalid"))
            except subprocess.TimeoutExpired:
                failures.append(GitRuntimeError("Git 响应超时，请检查安装或配置其他 Git 路径。", "git_timeout"))
            except OSError:
                failures.append(GitRuntimeError("Git 程序无法启动，请检查安装和执行权限。", "git_executable_invalid"))
        error = next((item for item in failures if item.reason["code"] == "git_license_required"), None)
        value: GitRuntime | GitRuntimeError = selected or error or (failures[0] if failures else GitRuntimeError(
            "未找到可用 Git。请安装 Git 或配置 MARS_GIT_EXECUTABLE（完整路径）。", "git_not_found"))
        if len(_CACHE) >= 16:
            _CACHE.clear()
        _CACHE[key] = (time.monotonic() + policy["cache_seconds"], value)
        if isinstance(value, GitRuntimeError):
            raise value
        return value


def run_git(arguments: Sequence[str], *, cwd: Path | None = None,
            environment: Mapping[str, str] | None = None, check: bool = True,
            timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    runtime = resolve_git(environment=environment)
    limit = timeout or float(_policy(repo_root() / "configs/git_runtime.yaml")["command_timeout_seconds"])
    child_environment = git_environment(environment)
    # Local transports invoke Git again (for example upload-pack during a
    # file fetch). They must discover the verified host binary before an
    # unusable system shim, just like configured coding commands do.
    child_environment["PATH"] = str(Path(runtime.executable).parent) + os.pathsep + child_environment.get("PATH", "")
    try:
        result = subprocess.run([runtime.executable, *arguments], cwd=cwd, env=child_environment,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=limit)
    except subprocess.TimeoutExpired as exc:
        raise GitRuntimeError("Git 操作超时，进度已保留，请核对后重试。", "git_timeout") from exc
    except OSError as exc:
        raise GitRuntimeError("Git 程序无法启动，请检查安装和执行权限。", "git_executable_invalid") from exc
    if check and result.returncode:
        raise git_failure(result.stderr, returncode=result.returncode)
    return result


def git_child_environment() -> dict[str, str]:
    """Use the same verified Git for a coding program's own child commands."""
    env = git_environment()
    directory = str(Path(resolve_git().executable).parent)
    env["PATH"] = directory + os.pathsep + env.get("PATH", "")
    return env
