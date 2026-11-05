"""Application-owned project folders. Every run, search and check lives in a project folder; MARS itself holds none."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import shutil
import threading
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable

import yaml

from mars_app import artifacts

selected: ContextVar[dict[str, Any] | None] = ContextVar("mars_project", default=None)
SOURCE_EXTENSIONS = {".md", ".txt", ".pdf"}


def saved_events(run: Path, since: int = 0) -> list[dict[str, Any]]:
    found = []
    try:
        lines = (run / "events.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return found
    for index, line in enumerate(lines[since:], since):
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                found.append({**item, "index": index})
        except ValueError:
            break
    return found


def saved_status(run: Path) -> dict[str, Any]:
    rounds = sorted((p for p in (run / "step1").glob("r*") if p.name[1:].isdigit()),
                    key=lambda p: int(p.name[1:]))
    latest = rounds[-1] if rounds else None
    phase = "finished" if (run / "report.md").is_file() or (run / "implement/result.json").is_file() else "step2"
    if latest and (latest / "ranking.json").is_file() and not (latest / "handoff.json").exists():
        phase = "awaiting_approval"
    events = saved_events(run)
    return {"phase": phase, "running": False, "pid": None, "step1_round": latest.name if latest else None,
            "kind": "implement" if (run / "implement").is_dir() else "refine" if (run / "step1/r0/user_proposal.md").exists() else "rsi",
            "health": {"last_event_ts": events[-1].get("ts") if events else None},
            "pending_approval": artifacts._json(latest / "ranking.json", []) if latest else []}



class ProjectStore:
    def __init__(self, data_dir: Path, core_root: Path):
        self.data_dir, self.core_root = data_dir, core_root.resolve()
        self.registry = data_dir / "projects.json"
        self.lock = threading.RLock()

    def _records(self) -> list[dict[str, str]]:
        return artifacts._json(self.registry, []) or []

    def _write_records(self, records: list[dict[str, str]]) -> None:
        self.registry.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.registry.with_suffix(".tmp")
        tmp.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.registry)

    def executable(self) -> bool:
        """Whether the loaded core can run work inside a project folder (set by main once mars.api loads)."""
        return self.supports_projects()

    supports_projects: Callable[[], bool] = staticmethod(lambda: False)

    def none(self) -> dict[str, Any]:
        """No project yet: views are empty and every action asks the user to create or open a project folder."""
        root = self.data_dir / "no-project"
        return {"id": "none", "name": "未选择项目", "path": str(root), "runs_dir": str(root / "runs"),
                "reports_dir": str(self.data_dir / "exports"), "connected": False, "builtin": True, "demo": False, "config": {}}

    def get(self, pid: str) -> dict[str, Any]:
        if pid in ("current", "none"):
            rows = self._records()
            preferred = os.environ.get("MARS_DEFAULT_PROJECT")
            row = next((r for r in rows if preferred and Path(r["path"]).resolve() == Path(preferred).resolve()), rows[0] if rows else None)
            if row is None or pid == "none":
                return self.none()
            pid = row["id"]
        row = next((r for r in self._records() if r.get("id") == pid), None)
        if row is None:
            raise ValueError("项目不存在，请重新选择")
        root = Path(row["path"]).resolve()
        path = root / ".mars/project.yaml"
        if not path.is_file():
            raise ValueError("找不到项目声明，请重新打开项目文件夹")
        config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(config, dict):
            raise ValueError("项目声明格式错误")
        connected = self.executable()
        return {"id": pid, "name": str(config.get("name") or root.name), "path": str(root),
                "runs_dir": str(root / "runs"), "reports_dir": str(root / "reports"),
                "preferred": str(root) == os.environ.get("MARS_DEFAULT_PROJECT"),
                "connected": connected, "builtin": False, "demo": bool(config.get("demo")), "config": config}

    def register(self, path: str | Path) -> None:
        """Add an existing project folder (e.g. the installer's demo project) to the list once; never writes into it."""
        root = Path(path).expanduser().resolve()
        if not (root / ".mars/project.yaml").is_file():
            return
        with self.lock:
            rows = self._records()
            if not any(Path(r.get("path", "")).resolve() == root for r in rows):
                self._write_records([*rows, {"id": hashlib.sha256(str(root).encode()).hexdigest()[:16], "path": str(root)}])

    def list(self) -> list[dict[str, Any]]:
        found = []
        for row in self._records():
            try:
                found.append(self.get(row["id"]))
            except (OSError, ValueError, yaml.YAMLError):
                continue
        return found

    def _safe_root(self, value: str) -> Path:
        if not value.strip():
            raise ValueError("请填写项目文件夹路径")
        root = Path(value).expanduser().resolve()
        if root == self.core_root or self.core_root in root.parents or root in self.core_root.parents:
            raise ValueError("请选择独立的项目文件夹，不能写入 mars-core 或其父目录")
        if "sources" in root.parts and ".chatgpt-projects" in root.parts:
            raise ValueError("同步项目资料只读，请选择其他位置")
        return root

    @staticmethod
    def clean_config(config: dict[str, Any]) -> dict[str, Any]:
        """The fields the project form edits (schema_version 2). API keys, passwords and private keys never enter a
        manifest; evaluation, metrics, checks and the other declarations are kept by update() as they are."""
        allowed = ("name", "objective", "repo", "datasets", "context_sources", "remote")
        result = {k: config[k] for k in allowed if k in config}
        result["schema_version"] = 2
        repo = result.get("repo") or {}
        if not isinstance(repo, dict):
            raise ValueError("代码仓声明必须是映射")
        result["repo"] = {k: repo.get(k, "") for k in ("path", "ref", "commit", "editable_files")} | \
            {k: repo[k] for k in ("context_files", "required_files", "setup_patch") if repo.get(k)}
        remote = result.get("remote") or {}
        if not isinstance(remote, dict):
            raise ValueError("执行环境声明必须是映射")
        if not isinstance(result.get("datasets", []), list) or any(not isinstance(row, dict) for row in result.get("datasets", [])):
            raise ValueError("数据集声明必须是列表")
        result["remote"] = {k: remote.get(k) for k in ("enabled", "name", "device", "parallel", "data_path")}
        result["datasets"] = [{k: row[k] for k in ("name", "config", "data") if row.get(k)} for row in result.get("datasets", [])]
        if not isinstance(result.get("context_sources", []), list):
            raise ValueError("背景资料必须是文件或文件夹列表")
        result["context_sources"] = [str(p) for p in result.get("context_sources", [])]
        return result

    @staticmethod
    def materialize(root: Path, sources: list[str]) -> list[str]:
        """Archive references by original name. Code/data repositories are only referenced, never copied."""
        jobs: list[tuple[Path, Path]] = []
        context = root / "context"
        if not context.resolve().is_relative_to(root.resolve()):
            raise ValueError("context 目录不能链接到项目以外的位置")
        for value in sources:
            source = Path(value).expanduser()
            if not source.is_absolute():
                source = root / source
            source = source.resolve()
            if not source.exists():
                raise ValueError(f"背景资料不存在：{source.name}")
            if source.is_file():
                if source.suffix.lower() not in SOURCE_EXTENSIONS:
                    raise ValueError("背景资料支持 Markdown、TXT 和 PDF")
                target = source if source.is_relative_to(context.resolve()) else context / source.name
                jobs.append((source, target))
            else:
                for directory, folders, names in os.walk(source, followlinks=False):
                    folders[:] = [n for n in folders if not n.startswith(".") and n not in {"node_modules", "__pycache__"}]
                    for name in names:
                        file = Path(directory) / name
                        if file.suffix.lower() in SOURCE_EXTENSIONS and not file.is_symlink():
                            target = context / source.name / file.relative_to(source)
                            jobs.append((file, target))
        if len(jobs) > 500 or sum(file.stat().st_size for file, _ in jobs) > 100 * 1024 * 1024:
            raise ValueError("背景资料超过 500 个文件或 100 MB，请缩小选择范围")
        result = []
        for source, target in jobs:
            if not target.resolve().is_relative_to(context.resolve()):
                raise ValueError("资料必须保存在项目 context 目录中")
            if target.exists() and source != target.resolve() and target.read_bytes() != source.read_bytes():
                target = context / source.parent.name / source.name
                if target.exists() and target.read_bytes() != source.read_bytes():
                    raise ValueError(f"背景资料同名：{source.name}，请先重命名原文件")
            if not target.resolve().is_relative_to(context.resolve()):
                raise ValueError("资料目标链接到了项目以外的位置")
            target.parent.mkdir(parents=True, exist_ok=True)
            if source != target.resolve() and not target.exists():
                shutil.copy2(source, target)
            result.append(target.relative_to(root).as_posix())
        return list(dict.fromkeys(result))

    def create(self, path: str, config: dict[str, Any], opening: bool = False) -> dict[str, Any]:
        root = self._safe_root(path)
        manifest = root / ".mars/project.yaml"
        if not manifest.resolve().is_relative_to(root):
            raise ValueError("项目声明不能链接到项目以外的位置")
        if opening and not root.is_dir():
            raise ValueError("项目文件夹不存在")
        if not str(config.get("name", root.name)).strip():
            raise ValueError("请填写项目名称")
        with self.lock:
            if manifest.exists() and not opening:
                raise ValueError("这里已有一个项目，请使用打开已有文件夹")
            if not manifest.exists():
                cleaned = self.clean_config({"name": root.name, **config})
                root.mkdir(parents=True, exist_ok=True)
                (root / ".mars").mkdir(exist_ok=True)
                for folder in self.FOLDERS:
                    (root / folder).mkdir(exist_ok=True)
                cleaned["context_sources"] = self.materialize(root, cleaned.get("context_sources", []))
                manifest.write_text(yaml.safe_dump(cleaned, allow_unicode=True, sort_keys=False), encoding="utf-8")
            pid = hashlib.sha256(str(root).encode()).hexdigest()[:16]
            rows = [r for r in self._records() if r.get("id") != pid]
            self._write_records([{"id": pid, "path": str(root)}, *rows])
        return self.get(pid)

    def update(self, pid: str, config: dict[str, Any]) -> dict[str, Any]:
        project = self.get(pid)
        cleaned = self.clean_config(config)
        if not str(cleaned.get("name", "")).strip():
            raise ValueError("请填写项目名称")
        with self.lock:
            if project["builtin"]:
                raise ValueError("请先新建或打开一个项目文件夹")
            else:
                path = Path(project["path"]) / ".mars/project.yaml"
                if not path.resolve().is_relative_to(Path(project["path"])):
                    raise ValueError("项目声明不能链接到项目以外的位置")
                # Fields the settings form does not edit (evaluate, metrics, checks, prompts, ...) are kept.
                kept = {k: v for k, v in (project["config"] or {}).items() if k not in cleaned and k not in config}
                old_repo = (project["config"] or {}).get("repo") or {}
                cleaned["repo"] = {**{k: v for k, v in old_repo.items() if k not in cleaned["repo"]}, **cleaned["repo"]}
                cleaned = {**kept, **cleaned}
                cleaned["context_sources"] = self.materialize(Path(project["path"]), cleaned.get("context_sources", []))
                tmp = path.with_suffix(".tmp")
                tmp.write_text(yaml.safe_dump(cleaned, allow_unicode=True, sort_keys=False), encoding="utf-8")
                tmp.replace(path)
        return self.get(pid)

    FOLDERS = ("context", "tasks", "runs", "reports", "workspace")

    def move(self, pid: str, target: str) -> dict[str, Any]:
        """Change a project's location: the whole folder moves (.mars/project_id included), so its runs, caches and
        bindings follow; the core repairs worktree links on next use. The caller makes sure nothing is running."""
        project = self.get(pid)
        if project["builtin"]:
            raise ValueError("请先新建或打开一个项目文件夹")
        source = Path(project["path"]).resolve()
        destination = self._safe_root(target)
        if destination == source:
            return project
        if source in destination.parents:
            raise ValueError("不能移到项目自己的子文件夹里")
        if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
            raise ValueError(f"目标位置已存在且不是空文件夹：{destination}")
        with self.lock:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                destination.rmdir()  # an empty folder the user picked; shutil.move would otherwise nest inside it
            shutil.move(str(source), str(destination))
            for folder in self.FOLDERS:
                (destination / folder).mkdir(exist_ok=True)
            new_pid = hashlib.sha256(str(destination).encode()).hexdigest()[:16]
            rows = [r for r in self._records() if r.get("id") not in (pid, new_pid)]
            self._write_records([{"id": new_pid, "path": str(destination)}, *rows])
        return self.get(new_pid)

    def preflight(self, path: str, config: dict[str, Any]) -> list[dict[str, str]]:
        config = self.clean_config(config)
        checks = []
        def check(key: str, title: str, status: str, detail: str):
            checks.append({"key": key, "title": title, "status": status, "detail": detail})
        try:
            root = self._safe_root(path)
            parent = root
            while not parent.exists():
                parent = parent.parent
            ok = parent.is_dir() and os.access(parent, os.W_OK)
            check("folder", "项目文件夹", "pass" if ok else "error", str(root) if ok else "该位置不可写")
        except ValueError as error:
            check("folder", "项目文件夹", "error", str(error))
        repo = config.get("repo") or {}
        project_root = Path(path).expanduser().resolve()
        repo_root = Path(repo.get("path") or ".").expanduser()
        if not repo_root.is_absolute():
            repo_root = project_root / repo_root
        if repo.get("path"):
            target = repo_root
            ref = repo.get("commit") or repo.get("ref") or "HEAD"
            try:
                result = subprocess.run(["git", "-C", str(target), "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
                                        capture_output=True, text=True, timeout=5)
                check("repo", "研究代码固定版本", "pass" if result.returncode == 0 else "error",
                      result.stdout.strip()[:40] if result.returncode == 0 else "代码仓或指定版本不存在")
            except (OSError, subprocess.TimeoutExpired):
                check("repo", "研究代码固定版本", "error", "无法读取 Git 仓库")
        else:
            check("repo", "研究代码固定版本", "warn", "尚未绑定研究代码，可先保存项目")
        for i, row in enumerate(config.get("datasets") or []):
            for field, title in (("config", "数据集配置"), ("data", "数据")):
                value = row.get(field, "")
                if not value:
                    if field == "data":
                        check(f"dataset-{i}-{field}", f"{row.get('name', '')} {title}", "warn", "尚未设置")
                    continue
                target = Path(value).expanduser()
                if not target.is_absolute():
                    target = (repo_root if field == "config" else project_root) / target
                check(f"dataset-{i}-{field}", f"{row.get('name', '')} {title}", "pass" if target.exists() else "error", str(target))
        for i, source in enumerate(config.get("context_sources") or []):
            target = Path(source).expanduser()
            if not target.is_absolute():
                target = Path(path).expanduser() / target
            check(f"source-{i}", "背景资料", "pass" if target.exists() else "error", str(target))
        check("engine", "研究执行连接", "pass" if self.executable() else "warn",
              "内核支持独立项目执行；启动前仍会核验实际绑定。" if self.executable() else "当前内核不支持独立项目执行。")
        check("evaluation", "评测声明", "warn", "创建后在 .mars/project.yaml 中声明 evaluate、metrics（及可选的 checks），"
              "并把评测脚本放进 .mars/pack/；声明完整后项目才能执行。格式见 mars-core/docs/项目接口.md。")
        check("models", "模型与远程连接", "warn", "此次只做本地文件预检；连接测试请在模型连接页中手动执行，不会自动调用模型或训练。")
        return checks


def browse(path: str = "", files: bool = False) -> dict[str, Any]:
    root = Path(path).expanduser().resolve() if path else Path.home()
    if not root.is_dir():
        raise ValueError("文件夹不存在")
    entries = []
    for p in root.iterdir():
        if p.name.startswith("."):
            continue
        try:
            directory = p.is_dir()
            if directory or files:
                entries.append({"name": p.name, "path": str(p), "directory": directory})
        except OSError:
            continue
    return {"path": str(root), "parent": str(root.parent),
            "entries": sorted(entries, key=lambda e: (not e["directory"], e["name"].casefold()))[:1000]}
