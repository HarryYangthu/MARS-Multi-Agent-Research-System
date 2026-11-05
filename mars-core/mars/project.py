"""Project scope, read-only inspection of .mars/project.yaml and immutable execution bindings.

MARS ships no domain code: the project folder declares the code repository, datasets, the evaluation command, metrics,
checks and domain prompts (.mars/pack/ holds the project's own scripts). See docs/通用项目接口设计.md.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")
SCHEMA_VERSION = 2
PLACEHOLDERS = {"python", "pack", "project", "workdir", "dataset", "data", "config", "steps", "seed", "output", "baseline",
                "overrides", "checkpoint"}
SECRET_KEYS = {"api_key", "password", "private_key", "ssh_key", "api_token", "secret"}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


@lru_cache(maxsize=256)
def _hash_file(path: str, signature: tuple[int, ...]) -> str:
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def fingerprint(path: Path) -> str:
    """SHA-256 of a file, or of a directory's relative names and file hashes (hidden entries skipped)."""
    if path.is_dir():
        files = sorted(p for p in path.rglob("*") if p.is_file() and not any(x.startswith(".") for x in p.relative_to(path).parts)
                       and "__pycache__" not in p.parts)
        return digest([[p.relative_to(path).as_posix(), fingerprint(p)] for p in files])
    before = path.stat()
    signature = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
    result = _hash_file(str(path), signature)
    after = path.stat()
    if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != signature:
        raise ValueError(f"读取期间文件发生变化：{path}")
    return result


def rebase(value: Any, old: str, new: str) -> Any:
    """Replace a project-root prefix in every path string of a snapshot (a project folder may be moved)."""
    if isinstance(value, str):
        return new + value[len(old):] if value == old or value.startswith(old + os.sep) else value
    if isinstance(value, list):
        return [rebase(v, old, new) for v in value]
    if isinstance(value, dict):
        return {k: rebase(v, old, new) for k, v in value.items()}
    return value


def binding_id(binding: dict[str, Any], root: str | Path) -> str:
    """Identity of a binding with the project root factored out, so moving the folder keeps its id, workspace
    and cached evaluations."""
    return digest(rebase({k: v for k, v in binding.items() if k != "id"}, str(root), "<project>"))[:20]


def project_id(scope: "Scope", create: bool = False) -> str | None:
    """Stable id stored inside the project (.mars/project_id); travels with the folder."""
    path = scope.path(".mars/project_id")
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    if not create:
        return None
    import uuid
    value = uuid.uuid4().hex
    path.write_text(value + "\n", encoding="utf-8")
    return value


def git_read(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", timeout=30)
    if result.returncode:
        raise ValueError(f"无法读取绑定代码版本：{result.stderr.strip()[:300]}")
    return result.stdout.strip()


def resolve_path(value: str | Path, root: Path) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else root / path).resolve()


def command_tokens(command: Any) -> list[str]:
    """A declared command as an argument list; a string is split like a POSIX shell (placeholders stay inside tokens)."""
    tokens = shlex.split(command) if isinstance(command, str) else command
    if not isinstance(tokens, list) or not tokens or not all(isinstance(t, str) for t in tokens):
        raise ValueError("命令必须是非空字符串或字符串列表")
    unknown = {name for t in tokens for name in re.findall(r"\{(\w+)\}", t)} - PLACEHOLDERS
    if unknown:
        raise ValueError(f"命令含未知占位符 {sorted(unknown)}；可用 {sorted(PLACEHOLDERS)}")
    return tokens


def fill(tokens: list[str], values: dict[str, Any]) -> list[str]:
    def one(match: re.Match[str]) -> str:
        if match.group(1) not in values:
            raise ValueError(f"命令占位符 {{{match.group(1)}}} 在此处没有取值")
        return str(values[match.group(1)])
    return [re.sub(r"\{(\w+)\}", one, t) for t in tokens]


@dataclass(frozen=True)
class Scope:
    project_root: Path | None = None

    def __post_init__(self) -> None:
        if self.project_root is None:
            default = os.environ.get("MARS_DEFAULT_PROJECT")
            if not default:
                raise ValueError("未指定项目：请传入 project_root（--project），或设置 MARS_DEFAULT_PROJECT")
            object.__setattr__(self, "project_root", default)
        object.__setattr__(self, "project_root", Path(self.project_root).expanduser().resolve())
        for name in ("runs", "workspace", ".mars"):
            self.path(name)

    @property
    def root(self) -> Path:
        return self.project_root

    def path(self, name: str) -> Path:
        path = self.root / name
        if not path.resolve().is_relative_to(self.root):
            raise ValueError(f"项目路径链接到项目以外：{path}")
        return path

    @property
    def runs(self) -> Path:
        return self.path("runs")

    @property
    def workspace(self) -> Path:
        return self.path("workspace")

    def run(self, run_id: str) -> Path:
        if not RUN_ID.fullmatch(run_id):
            raise ValueError("run_id 只能包含字母、数字、点、下划线和连字符，长度 1–80")
        return self.path(f"runs/{run_id}")


def machine_config() -> dict[str, Any]:
    """This machine's settings outside any project (configs/machine.yaml, not in the repository): default python and
    the SSH profile of a remote GPU server."""
    path = Path(os.environ.get("MARS_MACHINE_CONFIG") or ROOT / "configs/machine.yaml")
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.is_file() else {}


def _profile(name: str | None) -> dict[str, Any]:
    remote = machine_config().get("remote") or {}
    return remote if name and remote.get("name") == name else {}


def _profile_id(remote: dict[str, Any]) -> str | None:
    keys = ("name", "host", "port", "user", "key", "root")
    return digest({k: remote.get(k) for k in keys}) if all(remote.get(k) for k in keys) else None


@dataclass(frozen=True)
class ExecutionContext:
    scope: Scope
    binding: dict[str, Any]
    environment: str
    moved_from: str | None = None   # the project root recorded in the snapshot, when the folder has been moved

    @property
    def workspace(self) -> Path:
        return self.scope.path(f"workspace/bindings/{self.binding['id']}")

    def snapshot(self) -> dict[str, Any]:
        return {"snapshot_version": 2, "project_root": str(self.scope.root), "environment": self.environment,
                "binding": self.binding}

    def config(self) -> dict[str, Any]:
        cfg = copy.deepcopy(self.binding["config"])
        remote = cfg["remote"]
        if self.environment == "remote":
            profile = _profile(remote.get("name"))
            if not self.binding.get("remote_profile_id") or _profile_id(profile) != self.binding["remote_profile_id"]:
                raise ValueError("本机远程配置缺失或与启动时不一致，请检查 configs/machine.yaml 的 remote")
            private_key = resolve_path(profile["key"], ROOT)
            if not private_key.is_file() or not os.access(private_key, os.R_OK):
                raise ValueError("本机远程配置引用的 SSH 私钥文件缺失或不可读")
            if set(remote.get("data") or {}) != set(cfg["datasets"]) or not all(PurePosixPath(p).is_absolute() for p in remote["data"].values()):
                raise ValueError("运行绑定的远程数据映射不完整，不能切换到 remote")
            cfg["remote"] = {**profile, **remote, "enabled": True,
                             "root": profile["root"].rstrip("/") + "/projects/" + digest(project_id(self.scope, create=True))[:16] + "/" + self.binding["id"]}
        else:
            remote["enabled"] = False
        return cfg

    def validate(self) -> None:
        binding, cfg = self.binding, self.binding["config"]
        repo = cfg["repo"]
        if git_read(Path(repo["source"]), "rev-parse", "--verify", repo["commit"] + "^{commit}") != repo["commit"]:
            raise ValueError("运行绑定的源代码 commit 已不可用")
        for name, item in cfg["datasets"].items():
            path = Path(item["data"])
            if not path.exists() or fingerprint(path) != binding["data_hashes"][name]:
                raise ValueError(f"数据集 {name} 缺失或内容与启动快照不一致：{path}")
        pack = Path(cfg["pack"])
        if (fingerprint(pack) if pack.is_dir() else None) != binding["pack_hash"]:
            raise ValueError("项目 .mars/pack 已改变（评测语义可能改变），此运行不能续跑，请用新的 run-id")
        if not Path(cfg["python"]).is_file():
            raise ValueError(f"运行绑定的 python 不存在：{cfg['python']}")
        self.config()

    @classmethod
    def restore(cls, scope: Scope, path: Path, validate: bool = False, environment: str | None = None) -> "ExecutionContext":
        data = json.loads(path.read_text(encoding="utf-8"))
        recorded = data.get("project_root")
        if data.get("snapshot_version") != 2 or not recorded:
            raise ValueError(f"执行快照来自旧版内核（项目接口 v1），只能查看，不能续跑：{path}")
        binding = data["binding"]
        if binding["id"] != binding_id(binding, recorded):
            raise ValueError(f"执行快照绑定校验失败：{path}")
        moved = recorded != str(scope.root)
        if moved:  # project-internal paths follow the folder; external data and code are re-checked by validate()
            binding = {**rebase(binding, recorded, str(scope.root)), "id": binding["id"]}
        ctx = cls(scope, binding, environment or data["environment"], recorded if moved else None)
        if ctx.environment not in ("local", "remote"):
            raise ValueError("执行环境必须是 local 或 remote")
        if validate:
            ctx.validate()
        return ctx


def _commands(field: str, value: Any, require: Any) -> dict[str, dict[str, Any]]:
    """checks / diagnostics: {name: command | {command, ...}} with validated tokens."""
    require(field, value is None or isinstance(value, dict), f"{field} 必须是映射：名称 → 命令")
    result = {}
    for name, spec in (value or {}).items():
        spec = spec if isinstance(spec, dict) else {"command": spec}
        require(f"{field}.{name}", all(isinstance(k, str) for k in spec),
                f"{field}.{name} 的字段名必须是字符串（YAML 会把 on/off/yes/no 解析成布尔值，请改名或加引号）")
        try:
            tokens = command_tokens(spec.get("command"))
        except ValueError as error:
            require(f"{field}.{name}", False, f"{field}.{name}：{error}")
        result[str(name)] = {**spec, "command": tokens}
    return result


def inspect(project_root: str | Path) -> tuple[dict[str, Any], ExecutionContext | None]:
    scope = Scope(Path(project_root))
    checks: list[dict[str, Any]] = []
    def check(field: str, status: str, message: str, required_for: list[str] | None = None) -> None:
        checks.append({"field": field, "status": status, "message": message, "required_for": required_for or ["local", "remote"]})
    def require(field: str, condition: Any, message: str) -> None:
        check(field, "pass" if condition else "fail", message)
        if not condition:
            raise ValueError(message)
    ctx = None
    version = None
    remote_option: dict[str, Any] = {"id": "remote", "label": "远程 GPU", "configured": False}
    try:
        manifest = scope.path(".mars/project.yaml")
        require("project_yaml", manifest.is_file(), f"缺少项目声明：{manifest}")
        spec = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        require("project_yaml", isinstance(spec, dict), "项目声明必须是 YAML 映射")
        version = spec.get("schema_version")
        require("schema_version", version == SCHEMA_VERSION, f"schema_version 必须为 {SCHEMA_VERSION}（旧版项目请按 docs/通用项目接口设计.md 迁移）")
        pack = scope.path(".mars/pack")
        interpreter = Path(str(spec.get("python") or machine_config().get("python") or sys.executable)).expanduser()
        python = Path(os.path.abspath(interpreter if interpreter.is_absolute() else scope.root / interpreter))
        require("python", python.is_file() and os.access(python, os.X_OK), f"python 需指向本机可执行解释器：{python}")

        repo = spec.get("repo") if isinstance(spec.get("repo"), dict) else {}
        require("repo.path", bool(repo.get("path")), "缺少 repo.path：请绑定只读来源仓库")
        source = resolve_path(repo["path"], scope.root)
        require("repo.ref/commit", bool(repo.get("ref") or repo.get("commit")), "缺少 repo.ref 或 repo.commit：请明确代码版本")
        commit = git_read(source, "rev-parse", "--verify", "--end-of-options", str(repo.get("commit") or repo["ref"]) + "^{commit}")
        if repo.get("commit") and repo.get("ref"):
            require("repo.ref/commit", git_read(source, "rev-parse", "--verify", "--end-of-options", str(repo["ref"]) + "^{commit}") == commit, "repo.ref 与 repo.commit 指向不同提交，请明确要使用的版本")
        check("repo.version", "pass", commit)
        patterns = repo.get("editable_files")
        if isinstance(patterns, str):
            patterns = [x.strip() for x in re.split(r"[,\n]", patterns) if x.strip()]
        require("repo.editable_files", isinstance(patterns, list) and bool(patterns) and all(isinstance(p, str) and p and not PurePosixPath(p).is_absolute() and ".." not in PurePosixPath(p).parts and not p.startswith(".git") for p in patterns), "repo.editable_files 必须是非空的仓内相对路径或 glob 列表")
        context_files = repo.get("context_files") or []
        require("repo.context_files", isinstance(context_files, list) and all(isinstance(p, str) for p in context_files), "repo.context_files 必须是仓内相对路径列表")
        for file in [*(repo.get("required_files") or []), *context_files]:
            git_read(source, "cat-file", "-e", f"{commit}:{file}")
        setup_patch = resolve_path(repo["setup_patch"], scope.root) if repo.get("setup_patch") else None
        require("repo.setup_patch", setup_patch is None or setup_patch.is_file(), f"repo.setup_patch 不存在：{setup_patch}")

        datasets, data_hashes = {}, {}
        rows = spec.get("datasets")
        require("datasets", isinstance(rows, list) and bool(rows), "缺少 datasets：至少声明一个数据集")
        for i, row in enumerate(rows):
            require(f"datasets[{i}]", isinstance(row, dict) and row.get("name") and row.get("data"), f"datasets[{i}] 需要 name 和 data")
            name = str(row["name"])
            require(f"datasets[{i}].name", re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", name) and name not in datasets, "datasets[].name 只能用字母、数字、._-，且不重复")
            data = resolve_path(row["data"], scope.root)
            require(f"datasets[{i}].data", data.exists(), f"数据不存在：{data}")
            data_hashes[name] = fingerprint(data)
            datasets[name] = {"data": str(data), "config": str(row.get("config") or "")}

        evaluate = spec.get("evaluate") if isinstance(spec.get("evaluate"), dict) else {}
        try:
            tokens = command_tokens(evaluate.get("command"))
        except ValueError as error:
            require("evaluate.command", False, f"evaluate.command：{error}")
        evaluate = {"command": tokens, "timeout_seconds": int(evaluate.get("timeout_seconds", 43200)),
                    "max_parallel": int(evaluate.get("max_parallel", 1))}
        require("evaluate", evaluate["timeout_seconds"] > 0 and evaluate["max_parallel"] > 0, "evaluate.timeout_seconds 与 max_parallel 必须为正整数")
        metrics = spec.get("metrics") if isinstance(spec.get("metrics"), dict) else {}
        fields = metrics.get("fields") if isinstance(metrics.get("fields"), dict) else {}
        require("metrics.fields", bool(fields) and all(isinstance(v, dict) and v.get("better") in ("lower", "higher") for v in fields.values()),
                "metrics.fields 至少声明一个指标，每个指标写明 better: lower 或 higher")
        require("metrics.primary", metrics.get("primary") in fields, "metrics.primary 必须是 metrics.fields 中的指标")
        checks_cfg = _commands("checks", spec.get("checks"), require)
        diagnostics = _commands("diagnostics", spec.get("diagnostics"), require)
        keys = spec.get("editable_config_keys") or []
        require("editable_config_keys", isinstance(keys, list) and all(isinstance(k, str) for k in keys), "editable_config_keys 必须是字符串列表")
        search_space = spec.get("search_space") or {}
        require("search_space", isinstance(search_space, dict) and set(search_space) <= set(keys), "search_space 的键必须在 editable_config_keys 中")
        prompts = {}
        for key, value in (spec.get("prompts") or {}).items():
            path = resolve_path(value, scope.root)
            require(f"prompts.{key}", key in ("domain", "implement", "decide") and path.is_file(), f"prompts 只支持 domain/implement/decide，且文件必须存在：{path}")
            prompts[key] = path.read_text(encoding="utf-8")
        require("project_yaml", not {str(k).lower() for k in spec} & SECRET_KEYS, "项目声明不能包含模型或 SSH 凭据，请移到本机配置")

        documents, document_bytes = [], 0
        sources = spec.get("context_sources") or []
        require("context_sources", isinstance(sources, list) and all(isinstance(p, str) for p in sources), "context_sources 必须是路径字符串列表")
        for value in sources:
            path = resolve_path(value, scope.root)
            require("context_sources", path.exists(), f"背景资料不存在：{path}")
            for file in sorted(path.rglob("*")) if path.is_dir() else [path]:
                if file.is_symlink() or any(part.startswith(".") for part in file.relative_to(path).parts if path.is_dir()):
                    continue
                if file.is_file() and file.suffix.lower() in (".md", ".txt"):
                    if any(doc["source"] == str(file) for doc in documents):
                        continue
                    document_bytes += file.stat().st_size
                    require("context_sources", len(documents) < 500 and document_bytes <= 100 * 1024 * 1024, "背景资料最多 500 份、总计不超过 100 MB")
                    documents.append({"source": str(file), "text": file.read_text(encoding="utf-8"), "kind": "project_context"})
                elif file.is_file():
                    check("context_sources.unsupported", "not_checked", f"尚未支持此格式，未纳入知识库：{file}")

        remote = spec.get("remote") or {}
        require("remote", isinstance(remote, dict) and not set(remote) - {"enabled", "name", "device", "parallel", "data_path"}, "remote 只允许 enabled/name/device/parallel/data_path；SSH 凭据须保存在本机配置")
        profile = _profile(remote.get("name"))
        profile_id = _profile_id(profile)
        data_path = remote.get("data_path")
        remote_data = ({str(k): str(v) for k, v in data_path.items()} if isinstance(data_path, dict) else
                       {name: str(PurePosixPath(data_path) / Path(item["data"]).name) for name, item in datasets.items()} if isinstance(data_path, str) and data_path else {})
        key_ok = bool(profile_id) and resolve_path(profile["key"], ROOT).is_file() and os.access(resolve_path(profile["key"], ROOT), os.R_OK)
        parallel = remote["parallel"] if remote.get("parallel") is not None else profile.get("max_parallel", 1)
        device = remote.get("device") or profile.get("device", "cuda:0")
        configured = bool(profile_id and key_ok and set(remote_data) == set(datasets) and all(PurePosixPath(p).is_absolute() for p in remote_data.values())
                          and type(parallel) is int and parallel > 0)
        check("remote.configuration", "pass" if configured else "fail", "本机远程配置、密钥文件及远程数据映射完整" if configured else "请补充 remote.name、remote.data_path，并在本机 configs/machine.yaml 配置同名 SSH 连接", ["remote"])
        remote_option.update(name=remote.get("name"), device=device, configured=configured)

        cfg = {"repo": {"source": str(source), "commit": commit, "setup_patch": str(setup_patch) if setup_patch else None,
                        "context_files": context_files},
               "python": str(python), "pack": str(pack), "datasets": datasets, "evaluate": evaluate,
               "metrics": {"primary": metrics["primary"], "fields": fields}, "checks": checks_cfg, "diagnostics": diagnostics,
               "editable_files": patterns, "editable_config_keys": keys, "search_space": search_space, "prompts": prompts,
               "name": str(spec.get("name") or scope.root.name), "objective": str(spec.get("objective") or ""),
               "remote": {"enabled": bool(remote.get("enabled")), "name": remote.get("name"), "device": device,
                          "max_parallel": parallel, "data": remote_data}}
        binding = {"schema_version": version, "config": cfg, "data_hashes": data_hashes, "documents": documents,
                   "remote_profile_id": profile_id, "pack_hash": fingerprint(pack) if pack.is_dir() else None,
                   "setup_patch_hash": fingerprint(setup_patch) if setup_patch else None,
                   "models_config": str(Path(os.environ.get("MARS_MODELS_CONFIG") or ROOT / "configs/models.yaml").resolve())}
        check("models.shared_config", "pass" if Path(binding["models_config"]).is_file() else "fail", "共享模型配置引用：" + binding["models_config"], ["rsi", "refine", "implement"])
        check("models.connectivity", "not_checked", "未测试模型服务或凭据", ["rsi", "refine", "implement"])
        binding["id"] = binding_id(binding, scope.root)
        ctx = ExecutionContext(scope, binding, "remote" if remote.get("enabled") else "local")
        check("evaluation", "not_checked", "未执行评测命令；评测有效性在首次运行基线时验证")
    except (ValueError, OSError, TypeError, KeyError, subprocess.TimeoutExpired, yaml.YAMLError) as error:
        if not checks or checks[-1]["status"] != "fail":
            check("configuration", "fail", str(error))
    common_errors = [c for c in checks if c["status"] == "fail" and "local" in c["required_for"]]
    options = [{"id": "local", "label": "本地", "configured": not common_errors and ctx is not None}, remote_option]
    if common_errors:
        remote_option["configured"] = False
    report = {"project_root": str(scope.root), "runs_dir": str(scope.runs), "workspace_dir": str(scope.workspace),
              "schema_version": version, "api_version": 3, "checks": checks,  # failures: status "fail", see required_for
              "execution_environments": {"options": options}, "ready": {o["id"]: o["configured"] for o in options},
              "binding_id": ctx.binding["id"] if ctx else None}
    if ctx:
        cfg = ctx.binding["config"]
        report["binding"] = {"id": ctx.binding["id"], "workspace_dir": str(ctx.workspace), "name": cfg["name"],
                             "repo": {"path": cfg["repo"]["source"], "commit": cfg["repo"]["commit"], "editable_files": cfg["editable_files"]},
                             "datasets": [{"id": name, **item, "sha256": ctx.binding["data_hashes"][name]} for name, item in cfg["datasets"].items()],
                             "metrics": cfg["metrics"], "checks": sorted(cfg["checks"]), "diagnostics": sorted(cfg["diagnostics"]),
                             "python": cfg["python"], "models_config_ref": ctx.binding["models_config"]}
    return report, ctx


def resolve(project_root: str | Path, environment: str | None = None) -> ExecutionContext:
    report, ctx = inspect(project_root)
    selected = environment or (ctx.environment if ctx else "local")
    if selected not in ("local", "remote") or not report["ready"].get(selected):
        errors = [c["message"] for c in report["checks"] if c["status"] == "fail" and selected in c["required_for"]]
        raise ValueError("项目不能执行：" + "；".join(errors or ["执行环境必须是 local 或 remote"]))
    return ExecutionContext(ctx.scope, ctx.binding, selected)


def prepare_run(run_dir: Path, task_path: Path, project_root: str | Path, environment: str | None = None) -> None:
    """Freeze a project run before its CLI process starts: contract with execution binding plus execution.json.
    No model code is imported here, so callers (mars-app) need not load litellm."""
    scope = Scope(Path(project_root))
    if run_dir.resolve() != scope.run(run_dir.name).resolve():
        raise ValueError("运行目录与项目归属不一致")
    if not scope.path(".mars/project.yaml").is_file():
        raise ValueError("项目不能执行：缺少项目声明 .mars/project.yaml")
    contract = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    if not isinstance(contract, dict):
        raise ValueError("研究约定必须是 YAML 映射")
    execution = contract.get("execution") or {}
    saved = execution.get("environment")
    if saved and environment and saved != environment:
        raise ValueError("研究约定的 execution.environment 与本次选择不一致")
    context = resolve(scope.root, environment or saved)
    cfg = context.binding["config"]
    if not Path(context.binding["models_config"]).is_file():
        raise ValueError("缺少本机共享模型配置：" + context.binding["models_config"])
    pq = (contract.get("budget") or {}).get("prequick") or {}
    if pq.get("dataset") is not None and pq["dataset"] not in cfg["datasets"]:
        raise ValueError(f"budget.prequick.dataset={pq['dataset']} 未在本项目的数据集中声明")
    targets = contract.get("targets") or {}
    for gate in targets.get("gates") or []:
        if gate.get("metric") not in cfg["metrics"]["fields"]:
            raise ValueError(f"targets.gates 的指标 {gate.get('metric')} 未在项目 metrics.fields 中声明")
    for gate in targets.get("checks") or []:
        if gate.get("check") not in cfg["checks"]:
            raise ValueError(f"targets.checks 的检查 {gate.get('check')} 未在项目 checks 中声明")
    if execution.get("project_root") and resolve_path(execution["project_root"], scope.root) != scope.root:
        raise ValueError("研究约定的 execution.project_root 属于另一个项目")
    if execution.get("binding_id") and execution["binding_id"] != context.binding["id"]:
        raise ValueError("研究约定绑定与当前项目不一致；继续旧运行请 resume，新运行请使用未绑定的任务约定")
    contract["execution"] = {**execution, "environment": context.environment,
                             "project_root": str(scope.root), "binding_id": context.binding["id"], "datasets": list(cfg["datasets"])}
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        run_dir.mkdir()
    except FileExistsError:
        raise ValueError(f"运行 {run_dir.name} 已存在；请使用 resume 或新的 run_id") from None
    (run_dir / "execution.json").write_text(json.dumps({**context.snapshot(), "contract_hash": digest(contract)}, ensure_ascii=False, indent=1), encoding="utf-8")
    (run_dir / "contract.yaml").write_text(yaml.safe_dump(contract, allow_unicode=True, sort_keys=False), encoding="utf-8")
