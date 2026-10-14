"""Save project background as README reference material and bind baseline code."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any
import os
import uuid
import fnmatch
import hashlib
import shutil
import yaml

from app.harness.context.folder_context import discover_folder_context
from app.harness.persistence import atomic_write_text, fsync_directory, path_lock
from app.harness.project_workspace import FolderProject
from app.harness.runtime.project_scope import safe_scope_path
from app.settings import get_settings
from app.harness.project_storage import folder_directory, storage_policy
from app.harness.runtime.git_runtime import run_git


def save_model_references(project: FolderProject, rows: list[dict[str, Any]]) -> None:
    """Record shared connection references, never credential values."""
    allowed = {"agent", "enabled", "provider", "model", "api_key_env", "base_url_env", "temperature", "max_tokens"}
    public = [{key: value for key, value in row.items() if key in allowed} for row in rows]
    target = safe_scope_path(project.root, ".mars/model_connections.yaml")
    atomic_write_text(target, yaml.safe_dump({"schema": "model_connections.v1", "scope": "shared_host_connections",
        "credentials": "host_secure_store", "agents": public}, allow_unicode=True, sort_keys=False))


def save_background(project: FolderProject, filename: str, data: bytes) -> dict[str, Any]:
    """Replace README atomically, preserving its previous bytes outside context."""
    if (not filename or PurePosixPath(filename).name != filename or "\\" in filename
            or filename.startswith(".") or any(ord(char) < 32 for char in filename)
            or PurePosixPath(filename).suffix.lower() not in {".md", ".txt"}):
        raise ValueError("请选择文件名不含路径的 Markdown 或 TXT 文本")
    settings = get_settings()
    if len(data) > settings.mars_folder_context_max_chars * 4:
        raise ValueError("背景文档过大，请整理必要内容后上传")
    text = data.decode("utf-8-sig")
    if not text.strip() or "\x00" in text:
        raise ValueError("背景文档必须是非空 UTF-8 文本")
    # The incoming name never grants instruction authority, even AGENTS.md.
    relative = "README.md"
    lock = safe_scope_path(project.root, ".mars/background-upload.lock")
    with path_lock(lock):
        target = safe_scope_path(project.root, relative)
        # Account for a replacement, and permit replacing an oversized README.
        before = discover_folder_context(project, exclude_paths=frozenset({relative}))
        if len(before["files"]) + 1 > settings.mars_folder_context_max_files:
            raise ValueError("项目背景文档数量已达到上限")
        if before["total_chars"] + len(text) > settings.mars_folder_context_max_chars:
            raise ValueError("项目背景文档总长度超过上限，请精简后上传")
        previous: Path | None = None
        if target.exists():
            previous = safe_scope_path(project.root, f".mars/background-history/README.{uuid.uuid4().hex}.md")
            previous.parent.mkdir(exist_ok=True)
            fsync_directory(previous.parent.parent)
            # Back up bytes without normalizing line endings or a UTF-8 BOM.
            created = False
            try:
                with previous.open("xb") as stream:
                    created = True
                    stream.write(target.read_bytes())
                    stream.flush()
                    os.fsync(stream.fileno())
                fsync_directory(previous.parent)
            except Exception:
                if created:
                    previous.unlink(missing_ok=True)
                raise
        try:
            atomic_write_text(target, text)
            record = discover_folder_context(project)
            document = next((item for item in record["files"] if item["path"] == relative), None)
            if document is None:
                raise ValueError("项目 context_files 未包含 README.md，请先调整项目背景范围")
            result = {key: value for key, value in document.items() if key != "content"}
            if previous is not None:
                result["previous_path"] = previous.relative_to(project.root).as_posix()
            return result
        except Exception:
            if previous is not None:
                os.replace(previous, target)
                fsync_directory(previous.parent)
            else:
                target.unlink(missing_ok=True)
            fsync_directory(target.parent)
            raise


def bind_code_folder(project: FolderProject, path: str, *, role: str = "simulation_baseline") -> Path:
    """Import external code as an independent project-local Git snapshot.

    ``simulation_baseline`` is the read-only research baseline (default).
    ``ainative`` binds a generated AI Native working repo that agents may
    write to; the previous baseline path is preserved as ``baseline_repo_path``.
    """
    if role not in {"simulation_baseline", "ainative"}:
        raise ValueError(f"未知的代码仓角色：{role}")
    candidate = Path(path).expanduser()
    if not path.strip() or not candidate.is_absolute():
        raise ValueError("请选择代码工程文件夹的完整路径")
    code = candidate.resolve(strict=True)
    if not code.is_dir():
        raise ValueError("代码工程必须是已存在的文件夹")
    with os.scandir(code):
        pass  # Check accessibility without writing to the selected repository.
    link = safe_scope_path(project.root, ".mars/repo_link.yaml", must_exist=True)
    with path_lock(safe_scope_path(project.root, ".mars/repo-link.lock")):
        raw = yaml.safe_load(link.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("project") != project.name:
            raise ValueError("代码关联配置与当前项目不匹配")
        if raw.get("repo_mode") != "local_path":
            raise ValueError("当前代码关联不是本地文件夹模式，请保留现有工程配置")
        if not code.is_relative_to(project.root):
            if project.root.is_relative_to(code):
                raise ValueError("不能导入包含当前项目的父目录，请选择独立的代码仓")
            code = _import_code(project, code)
        if role == "ainative" and raw.get("repo_role") == "simulation_baseline":
            raw["baseline_repo_path"] = str(raw.get("repo_path", ""))
        raw["repo_path"] = os.path.relpath(code, project.metadata_root).replace(os.sep, "/")
        raw["repo_role"] = role
        raw["read_only"] = role != "ainative"
        atomic_write_text(link, yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
    return code


def _import_code(project: FolderProject, source: Path) -> Path:
    """Never overwrite an existing import or modify the source checkout."""
    code_root = folder_directory(project, "code")
    token = uuid.uuid4().hex
    staging = safe_scope_path(project.root, f"code/.import-{token}")
    target = code_root / f"{source.name}-{token[:8]}"
    excludes = [str(item) for item in storage_policy()["code_excludes"]]
    records: list[dict[str, Any]] = []
    excluded: list[str] = []
    data_imports: list[dict[str, str]] = []
    data_records: list[dict[str, Any]] = []

    def ignore(directory: str, names: list[str]) -> list[str]:
        rejected = []
        for name in names:
            item = Path(directory) / name
            if any(fnmatch.fnmatch(name, pattern) for pattern in excludes):
                rejected.append(name)
                excluded.append(item.relative_to(source).as_posix())
            elif item.is_symlink():
                raise ValueError(f"代码仓含符号链接，请先将其转换为实际文件：{item.relative_to(source)}")
        return rejected

    def ignore_data(directory: str, names: list[str]) -> list[str]:
        # Dataset directories may themselves be named data/datasets/runs.
        # Keep their real inputs while still omitting credentials and caches.
        rejected = []
        for name in names:
            item = Path(directory) / name
            if any(fnmatch.fnmatch(name, pattern) for pattern in excludes if pattern not in {"data", "datasets", "runs", "outputs"}):
                rejected.append(name)
            elif item.is_symlink():
                raise ValueError(f"数据目录含符号链接，请先将其转换为实际文件：{item.relative_to(source)}")
        return rejected

    try:
        shutil.copytree(source, staging, ignore=ignore)
        for item in sorted(staging.rglob("*")):
            if item.is_file():
                content = item.read_bytes()
                relative = item.relative_to(staging).as_posix()
                original = source / relative
                if hashlib.sha256(original.read_bytes()).digest() != hashlib.sha256(content).digest():
                    raise ValueError(f"导入期间源文件发生变化：{relative}，请重试")
                records.append({"path": relative, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
        if not records:
            raise ValueError("所选文件夹没有可导入的代码文件")
        # Repository-local input data becomes a real project data copy.
        # Rebase only declared data paths, never arbitrary external files.
        mappings: dict[Path, Path] = {}
        for name in ("data", "datasets"):
            original_data = source / name
            if not original_data.is_dir() or original_data.is_symlink():
                continue
            destination = folder_directory(project, "data") / f"{source.name}-{token[:8]}-{name}"
            data_imports.append({"source": str(original_data), "destination": destination.relative_to(project.root).as_posix()})
            shutil.copytree(original_data, destination, ignore=ignore_data)
            from app.storage.data_source_store import sha256_file
            for copied in sorted(destination.rglob("*")):
                if copied.is_file():
                    data_relative = copied.relative_to(destination)
                    checksum = sha256_file(copied)
                    if checksum != sha256_file(original_data / data_relative):
                        raise ValueError(f"导入期间数据发生变化：{data_relative}，请重试")
                    data_records.append({"path": copied.relative_to(project.root).as_posix(),
                                         "bytes": copied.stat().st_size, "sha256": checksum})
            mappings[original_data.resolve()] = destination

        def rebase(value: Any) -> Any:
            if isinstance(value, dict):
                return {key: rebase(item) for key, item in value.items()}
            if isinstance(value, list):
                return [rebase(item) for item in value]
            if isinstance(value, str):
                original = (source / value).resolve()
                for old, new in mappings.items():
                    if original.is_relative_to(old) and original.is_file():
                        copied = new / original.relative_to(old)
                        if copied.is_file():
                            return os.path.relpath(copied, target).replace(os.sep, "/")
            return value

        if mappings:
            for config in (staging / "configs").glob("*.yaml"):
                original = yaml.safe_load(config.read_text(encoding="utf-8"))
                updated = rebase(original)
                if original != updated:
                    config.write_text(yaml.safe_dump(updated, allow_unicode=True, sort_keys=False), encoding="utf-8")
        data_imports.extend(localize_checkpoint_files(project, staging, source_root=source, final_root=target))
        for record in records:
            record["source_sha256"] = record["sha256"]
            record["sha256"] = hashlib.sha256((staging / record["path"]).read_bytes()).hexdigest()
            record["bytes"] = (staging / record["path"]).stat().st_size
        run_git(("init", "-b", "main", str(staging)))
        run_git(("-C", str(staging), "add", "."))
        run_git(("-C", str(staging), "-c", "user.name=MARS", "-c", "user.email=mars@localhost",
                 "commit", "-m", "Import project baseline snapshot"))
        staging.rename(target)
        receipt = safe_scope_path(project.root, f".mars/code-imports/{token}.yaml")
        receipt.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(receipt, yaml.safe_dump({"schema": "code_import.v1", "source": str(source),
            "destination": target.relative_to(project.root).as_posix(), "files": records,
            "excluded": excluded, "data_imports": data_imports, "data_files": data_records}, allow_unicode=True, sort_keys=False))
        return target
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        if target.exists():
            shutil.rmtree(target)
        for imported_data in data_imports:
            destination = project.root / imported_data["destination"]
            if destination.is_dir():
                shutil.rmtree(destination)
            elif destination.is_file():
                destination.unlink()
        raise


def localize_checkpoint_files(project: FolderProject, code: Path, *, source_root: Path,
                              final_root: Path | None = None) -> list[dict[str, str]]:
    """Retain explicitly declared initialization weights with the project."""
    from app.storage.data_source_store import sha256_file
    policy = storage_policy()
    imported: list[dict[str, str]] = []
    target_code = final_root or code

    def visit(value: Any) -> Any:
        if isinstance(value, list):
            return [visit(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: visit(item) for key, item in value.items()}
        for key in policy["checkpoint_keys"]:
            raw = result.get(key)
            if not isinstance(raw, str) or not raw.strip():
                continue
            source = (source_root / raw).resolve()
            if not source.is_file():
                continue  # Admission reports missing inputs; never invent a checkpoint.
            if source.is_relative_to(project.root):
                result[key] = os.path.relpath(source, target_code).replace(os.sep, "/")
                continue
            if source.suffix.lower() not in policy["checkpoint_suffixes"] or source.stat().st_size > policy["max_checkpoint_bytes"]:
                raise ValueError("初始化权重类型或大小不符合项目导入范围")
            checksum = sha256_file(source)
            destination = folder_directory(project, "data") / "checkpoints" / checksum.split(":", 1)[1] / source.name
            checked = safe_scope_path(project.root, destination.relative_to(project.root).as_posix())
            checked.parent.mkdir(parents=True, exist_ok=True)
            if not checked.exists():
                shutil.copy2(source, checked)
                if sha256_file(checked) != checksum:
                    checked.unlink()
                    raise ValueError("初始化权重复制校验失败")
                imported.append({"source": str(source), "destination": checked.relative_to(project.root).as_posix(), "sha256": checksum})
            elif sha256_file(checked) != checksum:
                raise ValueError("项目内初始化权重与源文件不一致")
            result[key] = os.path.relpath(checked, target_code).replace(os.sep, "/")
        return result

    for config in (code / "configs").glob("*.yaml"):
        original = yaml.safe_load(config.read_text(encoding="utf-8"))
        updated = visit(original)
        if updated != original:
            config.write_text(yaml.safe_dump(updated, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return imported


def import_existing_workspace(project: FolderProject, *, legacy_root: Path | None = None) -> dict[str, Any]:
    """Copy legacy records into their project after verifying they are inactive.

    Originals are retained so historical absolute evidence references stay valid.
    Existing local records always win and are never overwritten.
    """
    import json
    from app.settings import repo_root
    from app.storage.data_source_store import DataSourceStore
    from app.storage.run_store import RunStore, LEGACY_OWNERS_DIR
    from app.bridge.commander_session import CommanderSessionStore
    runtime = legacy_root or repo_root()
    result: dict[str, Any] = {"runs": 0, "conversations": 0, "datasets": 0}
    sources: list[tuple[Path, Path, str]] = []
    for run in RunStore(runtime / "runs").list():
        if run.project != project.name:
            continue
        state = run.root / "run_state.json"
        if state.is_file() and json.loads(state.read_text()).get("status") in {"running", "pending", "queued"}:
            raise ValueError("项目仍有运行中的任务，请先暂停后再归拢已有记录")
        sources.append((run.root, folder_directory(project, "runs") / run.run_id, "runs"))
    legacy = runtime / "conversations"
    sessions = CommanderSessionStore(legacy)
    for session in sessions.list():
        if session.project == project.name:
            sources.append((legacy / session.conv_id, folder_directory(project, "conversations") / session.conv_id, "conversations"))
    data = DataSourceStore(runtime / "workspace/uploads/datasets")
    for profile in data.list(project=project.name):
        identifier = str(profile["id"])
        sources.append((data.profile_path(identifier).parent, folder_directory(project, "data") / identifier, "datasets"))
    with path_lock(safe_scope_path(project.root, ".mars/workspace-import.lock")):
        for source, target, kind in sources:
            owner = safe_scope_path(target.parent, f"{LEGACY_OWNERS_DIR}/{source.name}.json") if kind == "runs" else None
            if target.exists() or (owner is not None and owner.exists()):
                if owner is not None and not owner.exists():
                    atomic_write_text(owner, json.dumps({"legacy_source": str(source)}))
                continue
            def reject_links(directory: str, names: list[str]) -> list[str]:
                for name in names:
                    if (Path(directory) / name).is_symlink():
                        raise ValueError("旧任务包含符号链接，无法自动归拢")
                return []
            staging = target.with_name(".import-" + uuid.uuid4().hex)
            try:
                shutil.copytree(source, staging, ignore=reject_links)
                staging.rename(target)
                if owner is not None:
                    atomic_write_text(owner, json.dumps({"legacy_source": str(source)}))
            except Exception:
                if staging.exists():
                    shutil.rmtree(staging)
                raise
            result[kind] += 1
        default = data.default_id(project.name)
        if default and not (folder_directory(project, "data") / "_defaults.json").exists():
            from app.harness.persistence import atomic_write_json
            atomic_write_json(folder_directory(project, "data") / "_defaults.json", {project.name: default})
        result["schema"] = "workspace_import.v1"
        result["originals_retained"] = True
        result["sources"] = [{"source": str(source), "destination": target.relative_to(project.root).as_posix()} for source, target, _ in sources]
        atomic_write_text(safe_scope_path(project.root, ".mars/workspace-import.yaml"), yaml.safe_dump(result, allow_unicode=True, sort_keys=False))
    return result
