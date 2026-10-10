"""AI Native simulation repo detection and one-click generation.

Detection is deterministic and fast: torch dependency, a repo-level
AGENTS.md, tensorboard integration, checkpointing, and an entrypoint with
configs. Generation is a project-owned background job (the data_pipeline
pattern): survey the read-only baseline, plan with the coding model, generate
a torch repository into ``<project>/ainative``, let a separate model session
write the repo's AGENTS.md, really execute compile + dry-run verification,
then bind the generated repo as the project's writable working repo while the
original baseline stays read-only. A failed job reports its error explicitly;
nothing simulates success.
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from app.harness.agent_loop.trace import atomic_json
from app.harness.llm.model_registry import get_agent_config, select_provider
from app.harness.llm.provider_base import Completion, LLMConfig, LLMProvider, Message
from app.harness.persistence import atomic_write_text, path_lock
from app.harness.project_workspace import folder_project, project_root
from app.harness.runtime.project_scope import safe_scope_path

_TASKS: dict[str, asyncio.Task[None]] = {}

_SCAN_EXTS = {".py", ".md", ".yaml", ".yml", ".toml", ".txt", ".cfg", ".ini"}
_FORBIDDEN_SUFFIXES = {".pt", ".pth", ".ckpt", ".mat", ".npy", ".npz", ".pkl", ".bin", ".h5", ".hdf5", ".pth.tar"}
_IGNORE_DIRS = {".git", "__pycache__", ".venv", "venv", "env", "node_modules", ".mars",
                ".idea", ".vscode", "runs", "experiments", "outputs", "wandb", ".pytest_cache"}
_MAX_SCAN_FILES = 2000
_MAX_FILE_BYTES = 200_000
_SURVEY_TOTAL_CHARS = 300_000
_KEY_FILE_HINTS = ("model", "network", "net", "train", "run", "main", "entry", "engine", "data", "dataset", "sim")
_VERIFY_TIMEOUT_SECONDS = 240

_CODE_FILES: tuple[tuple[str, str], ...] = (
    ("libs/model.py", "torch nn.Module 模型定义"),
    ("libs/data.py", "数据加载与切分"),
    ("libs/engine.py", "训练/验证单步与 checkpoint 保存"),
    ("train.py", "训练入口（支持 --dry-run 冒烟）"),
    ("configs/static.yaml", "默认超参配置"),
    ("tests/test_smoke.py", "pytest 冒烟测试"),
)


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _project_base(project: str) -> Path:
    root = project_root(project)
    return root.parent if root.name == ".mars" else root


def _jobs_dir(project: str) -> Path:
    root = project_root(project)
    if not root.is_dir():
        raise ValueError("项目不存在")
    directory = root / "ainative_jobs"
    directory.mkdir(exist_ok=True)
    return directory


def _bound_repo(project: str) -> Path:
    link_path = project_root(project) / "repo_link.yaml"
    if not link_path.is_file():
        raise ValueError("项目尚未关联代码仓，请先在项目配置中绑定基线代码")
    raw = yaml.safe_load(link_path.read_text(encoding="utf-8"))
    raw_path = str((raw or {}).get("repo_path", "") or "")
    if not raw_path:
        raise ValueError("repo_link.yaml 未配置 repo_path")
    repo = Path(raw_path).expanduser()
    if not repo.is_absolute():
        repo = (project_root(project) / repo).resolve()
    if not repo.is_dir():
        raise ValueError(f"关联代码仓不存在：{repo}")
    return repo


def _iter_repo_files(repo: Path) -> list[Path]:
    out: list[Path] = []
    for path in sorted(repo.rglob("*")):
        if len(out) >= _MAX_SCAN_FILES:
            break
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(repo)
        if any(part in _IGNORE_DIRS for part in relative.parts):
            continue
        if path.suffix.lower() in _FORBIDDEN_SUFFIXES:
            continue
        if path.suffix.lower() not in _SCAN_EXTS:
            continue
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        out.append(path)
    return out


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return path.read_text(encoding="utf-8", errors="replace")


def detect_ainative(repo: Path) -> dict[str, Any]:
    """Deterministic five-point AI Native standard check; no model calls."""
    files = _iter_repo_files(repo)
    py_files = [p for p in files if p.suffix == ".py"]
    py_texts = {p: _read_text(p) for p in py_files[:400]}
    requirements = "\n".join(_read_text(p) for p in files if p.name.startswith("requirements") or p.name == "pyproject.toml")

    def has_torch() -> bool:
        return "torch" in requirements or any("import torch" in t or "from torch" in t for t in py_texts.values())

    def has_tensorboard() -> bool:
        return ("tensorboard" in requirements
                or any("tensorboard" in t or "SummaryWriter" in t or "EventFileWriter" in t
                       for t in py_texts.values()))

    def has_checkpoint() -> bool:
        return any("torch.save" in t and ("state_dict" in t or ".pt" in t) for t in py_texts.values())

    def has_entry_and_configs() -> bool:
        entries = [p for p in py_files if p.parent == repo
                   and re.match(r"(train|main|run)", p.name.lower())]
        entries += [p for p in py_files if p.parent.name == "tools"]
        configs = repo / "configs"
        has_configs = configs.is_dir() and any(configs.glob("*.yaml")) or any(configs.glob("*.yml")) if configs.is_dir() else False
        return bool(entries) and bool(has_configs)

    agents_md = (repo / "AGENTS.md").is_file() or (repo / "Agent.md").is_file()
    checks = [
        {"name": "torch_dependency", "label": "torch 架构依赖", "passed": has_torch()},
        {"name": "agents_md", "label": "仓根 AGENTS.md", "passed": agents_md},
        {"name": "tensorboard_integration", "label": "TensorBoard 集成", "passed": has_tensorboard()},
        {"name": "checkpoint_mechanism", "label": "checkpoint 保存机制", "passed": has_checkpoint()},
        {"name": "entrypoint_configs", "label": "训练入口与 configs/", "passed": has_entry_and_configs()},
    ]
    return {"repo_path": str(repo), "is_native": all(c["passed"] for c in checks), "checks": checks}


# ------------------------------------------------------------------- jobs

def _job_dir(project: str, job_id: str, *, create: bool = False) -> Path:
    if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
        raise ValueError("无效的生成任务记录")
    directory = _jobs_dir(project) / job_id
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    return directory


def read_job(project: str, job_id: str) -> dict[str, Any]:
    path = _job_dir(project, job_id) / "record.json"
    if not path.is_file():
        raise ValueError("生成任务记录不存在")
    record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if record.get("status") == "generating" and job_id not in _TASKS:
        record["status"] = "interrupted"
    return record


def latest_job(project: str) -> dict[str, Any] | None:
    directory = _jobs_dir(project)
    if not directory.is_dir():
        return None
    records: list[tuple[str, dict[str, Any]]] = []
    for path in directory.glob("*/record.json"):
        try:
            records.append((str(json.loads(path.read_text(encoding="utf-8")).get("created_at", "")),
                            read_job(project, path.parent.name)))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
    if not records:
        return None
    return max(records, key=lambda item: item[0])[1]


def generation_status(project: str) -> dict[str, Any]:
    record = latest_job(project)
    if record is None:
        repo = _bound_repo(project)
        return {"project": project, "has_job": False, "detection": detect_ainative(repo)}
    return {"project": project, "has_job": True, "job": record,
            "detection": record.get("detection", {})}


def start_generation(project: str) -> dict[str, Any]:
    if any(task.done() is False for task in _TASKS.values()):
        latest = latest_job(project)
        if latest is not None and latest.get("status") == "generating":
            raise ValueError("当前项目已有 AI Native 生成任务在运行")
    repo = _bound_repo(project)
    detection = detect_ainative(repo)
    if detection["is_native"]:
        return {"skipped": True, "reason": "当前代码仓已符合 AI Native 标准，无需生成",
                "detection": detection}
    target = _project_base(project) / "ainative"
    if target.exists() and any(target.iterdir()):
        raise ValueError(f"生成目标 {target} 已存在且非空；请先删除或重命名，避免覆盖研究人员的修改")
    job_id = uuid.uuid4().hex
    out = _job_dir(project, job_id, create=True)
    record: dict[str, Any] = {"id": job_id, "project": project, "status": "generating",
                              "created_at": _now(), "step": "survey", "error": "",
                              "baseline_repo": str(repo), "repo_path": str(target),
                              "detection": detection, "steps": [], "model_calls": []}
    atomic_json(out / "record.json", record)
    _TASKS[job_id] = asyncio.create_task(_generate(project, job_id, repo, target, record))
    _TASKS[job_id].add_done_callback(lambda _: _TASKS.pop(job_id, None))
    return {"skipped": False, "job": record}


def _finish_step(record: dict[str, Any], out: Path, name: str, detail: str) -> None:
    record["steps"].append({"name": name, "status": "completed", "detail": detail})
    record["step"] = name
    atomic_json(out / "record.json", record)


def _fail(record: dict[str, Any], out: Path, exc: BaseException) -> None:
    record["status"] = "failed"
    record["error"] = str(exc)[:4000]
    record["steps"].append({"name": record.get("step", ""), "status": "failed", "detail": str(exc)[:2000]})
    atomic_json(out / "record.json", record)
    logger.warning("ainative generation failed: project={} error={}", record.get("project"), str(exc)[:500])


async def _complete(provider: LLMProvider, config: LLMConfig, record: dict[str, Any],
                    system: str, user: str) -> str:
    completion: Completion = await provider.complete(
        [Message(role="system", content=system), Message(role="user", content=user)], config)
    record["model_calls"].append({"model": completion.model, "usage": completion.raw.get("usage", {})})
    return completion.text


def _code_text(text: str) -> str:
    fenced = re.search(r"```[a-zA-Z0-9_+-]*\n(.*?)```", text, re.DOTALL)
    body = fenced.group(1) if fenced else text
    return body.strip() + "\n"


def _survey(repo: Path) -> tuple[str, str]:
    """Return (file listing, key file excerpts) within conservative caps."""
    files = _iter_repo_files(repo)
    listing = "\n".join(str(p.relative_to(repo)) for p in files[:600])
    ranked = sorted(
        (p for p in files if p.suffix == ".py"),
        key=lambda p: (not any(h in p.name.lower() for h in _KEY_FILE_HINTS), str(p)))
    excerpts: list[str] = []
    total = 0
    for path in ranked[:12]:
        text = _read_text(path)[:8000]
        header = f"----- {path.relative_to(repo)} -----\n"
        if total + len(header) + len(text) > _SURVEY_TOTAL_CHARS:
            break
        excerpts.append(header + text)
        total += len(header) + len(text)
    return listing, "\n".join(excerpts)


def _parse_plan(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```[a-zA-Z0-9_+-]*\n|\n```$", "", text.strip(), flags=re.MULTILINE)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("迁移计划不是可解析的 JSON")
    parsed: Any = json.loads(cleaned[start:end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("迁移计划必须是 JSON 对象")
    for key in ("model_class", "io_contract", "data_format"):
        if not str(parsed.get(key, "")).strip():
            raise ValueError(f"迁移计划缺少 {key}")
    return parsed


def _write_genererated_file(repo: Path, relative: str, content: str) -> None:
    target = safe_scope_path(repo, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, content)


def _verify(repo: Path) -> list[dict[str, Any]]:
    """Real execution: compile everything, then a real CPU dry-run of the entry."""
    results = []
    checks = (
        ("compileall", ["-m", "compileall", "-q", "."], 120),
        ("import", ["-c", "import libs.model, libs.data, libs.engine; print('ainative-import-ok')"], 120),
        ("dry_run", ["train.py", "--dry-run"], _VERIFY_TIMEOUT_SECONDS),
    )
    for name, args, timeout in checks:
        proc = subprocess.run([sys.executable, *args], cwd=repo, capture_output=True, text=True,
                              timeout=timeout, check=False)  # noqa: S603
        ok = proc.returncode == 0
        results.append({"name": name, "passed": ok,
                        "output": (proc.stdout + proc.stderr)[-4000:]})
        if not ok:
            raise ValueError(f"验证步骤 {name} 失败：{(proc.stdout + proc.stderr)[-1500:]}")
    return results


def _requirements_text() -> str:
    return ("torch>=2.2,<3\nnumpy>=1.26\nPyYAML>=6.0\ntensorboard>=2.16,<3\nprotobuf<7\npytest>=8.0\n")


async def _generate(project: str, job_id: str, baseline: Path, target: Path, record: dict[str, Any]) -> None:
    out = _job_dir(project, job_id)
    provider: LLMProvider | None = None
    md_provider: LLMProvider | None = None
    try:
        provider, llm_config = select_provider(get_agent_config("coding"))
        target.mkdir(parents=True)

        # 1. survey the read-only baseline
        listing, excerpts = await asyncio.to_thread(_survey, baseline)
        _finish_step(record, out, "survey", f"{len(listing.splitlines())} files scanned")

        # 2. plan with the coding model
        plan_text = await _complete(provider, llm_config, record,
            "你是仿真代码仓迁移规划器。只输出 JSON：{\"model_class\": str, \"io_contract\": str, "
            "\"data_format\": str, \"entry_style\": str, \"files\": [str], \"notes\": str}。"
            "model_class 是基线中应迁移为 torch nn.Module 的主模型类名；io_contract 描述输入输出张量形状与 dtype；"
            "data_format 描述数据文件格式与键名；不得编造不存在的内容。",
            f"基线文件清单：\n{listing}\n\n关键文件摘录：\n{excerpts}")
        plan = _parse_plan(plan_text)
        atomic_json(out / "plan.json", plan)
        _finish_step(record, out, "plan", str(plan.get("model_class", "")))

        # 3. generate the torch repository (one model call per file)
        for relative, purpose in _CODE_FILES:
            content = await _complete(provider, llm_config, record,
                "你是 AI Native 仿真代码生成器，参照 pimc 仓库结构（libs/ 分层 + configs/ YAML + tests/ + train.py 入口）。"
                "要求：1) 统一 torch 架构，模型 forward 前后写张量形状注释，支持 cuda/cpu（torch.device，cuda 不可用回退 cpu）；"
                "2) train.py 必须支持 --dry-run：构建小规模随机数据、单次 forward/backward、打印摘要后退出；"
                "3) 真实训练用 tensorboard SummaryWriter 写 train/loss 等标量，并按验证指标原子保存 best.pt"
                "（先写 tmp 再 os.replace，记录 sha256）；4) 超参数只放 configs/static.yaml；5) 只输出一个文件的完整代码，不要解释。",
                f"迁移计划：\n{json.dumps(plan, ensure_ascii=False)}\n\n"
                f"现在生成文件 {relative}（用途：{purpose}）。基线关键摘录供参考：\n{excerpts[:60000]}")
            _write_genererated_file(target, relative, _code_text(content))
        _write_genererated_file(target, "requirements.txt", _requirements_text())
        _write_genererated_file(target, ".gitignore", "__pycache__/\n*.pyc\nruns/\n*.pt\n*.pth\n")
        _finish_step(record, out, "generate", f"{len(_CODE_FILES) + 2} files written")

        # 4. separate model session reads the generated repo and writes AGENTS.md
        md_provider, md_config = select_provider(get_agent_config("coding"))
        gen_listing, gen_excerpts = await asyncio.to_thread(_survey, target)
        agents_md = await _complete(md_provider, md_config, record,
            "你是代码仓规约撰写者。读完整个仿真代码仓后，产出仓根 AGENTS.md，格式参照 MARS pimc 项目："
            "编号章节 + 每条规则附 `Pattern: 路径:符号` 行；必须包含：1) 冻结基线类与 forward 接口签名；"
            "2) 只读目录（如 baseline/）；3) 张量形状注释要求；4) TensorBoard 是视图不是证据库、"
            "checkpoint 与事件文件不得提交 Git；5) 真实仿真启动命令。规则必须与仓内真实代码一致，不得虚构类名。",
            f"生成的代码仓文件清单：\n{gen_listing}\n\n关键文件摘录：\n{gen_excerpts}")
        _write_genererated_file(target, "AGENTS.md", agents_md.strip() + "\n")
        _finish_step(record, out, "agent_md", "AGENTS.md written")

        # 5. real verification (compile + import + dry-run)
        results = await asyncio.to_thread(_verify, target)
        record["verification"] = results
        _finish_step(record, out, "verify", "compileall + import + dry-run passed")

        # 6. bind as the project's writable working repo
        await asyncio.to_thread(_bind_ainative, project, target)
        record["status"] = "completed"
        record["steps"].append({"name": "bind", "status": "completed", "detail": str(target)})
        atomic_json(out / "record.json", record)
        logger.info("ainative generation completed: project={} repo={}", project, target)
    except Exception as exc:
        _fail(record, out, exc)
    finally:
        for session in (provider, md_provider):
            if session is not None:
                try:
                    await session.close()
                except Exception:  # noqa: BLE001 - close is best-effort cleanup
                    pass


def _bind_ainative(project: str, repo: Path) -> None:
    from app.bridge.project_onboarding import bind_code_folder
    folder = folder_project(project)
    if folder is None:
        raise ValueError("AI Native 绑定需要文件夹项目；请先在项目配置中打开项目文件夹")
    bind_code_folder(folder, str(repo), role="ainative")
