"""Per-run context: artifacts, journal, event log and shared services."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from mars.knowledge import KnowledgeBase, load_history
from mars.llm import LLM
from mars.project import ExecutionContext, Scope, digest, prepare_run
from mars.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "prompts"


def render(name: str, **values: Any) -> str:
    text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    for key, value in values.items():
        rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=1, default=str)
        text = text.replace("{{" + key + "}}", rendered)
    return text


@dataclass
class Run:
    run_dir: Path
    contract: dict[str, Any]
    llm: LLM
    kernel: Workspace
    kb: KnowledgeBase = field(init=False)

    def __post_init__(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.kernel.ensure_base()
        self.refresh_kb()

    @classmethod
    def open(cls, run_dir: Path, task_path: Path | None = None, *, project_root: str | Path | None = None,
             environment: str | None = None) -> "Run":
        run_dir = run_dir.resolve()  # child processes (CodingAgent) run in other directories
        scope = Scope(Path(project_root) if project_root is not None else None)
        if run_dir != scope.run(run_dir.name).resolve():
            raise ValueError("运行目录与项目归属不一致")
        if not (run_dir / "contract.yaml").exists():
            if task_path is None:
                raise ValueError(f"项目运行 {run_dir.name} 不存在")
            prepare_run(run_dir, task_path, scope.root, environment)
        snapshot = run_dir / "execution.json"
        if not snapshot.is_file():
            raise ValueError("项目运行缺少 execution.json，无法安全恢复原始绑定")
        context = ExecutionContext.restore(scope, snapshot, validate=True)
        if environment and environment != context.environment:
            raise ValueError("此运行已固定执行环境；更换环境请使用新的 run-id")
        contract = yaml.safe_load((run_dir / "contract.yaml").read_text(encoding="utf-8"))
        if digest(contract) != json.loads(snapshot.read_text(encoding="utf-8")).get("contract_hash"):
            raise ValueError("运行的研究约定与启动快照不一致，请使用新的 run_id")
        if contract.get("execution", {}).get("binding_id") != context.binding["id"]:
            raise ValueError("研究约定与执行快照的 binding_id 不一致")
        return cls(run_dir, contract, LLM(config_path=Path(context.binding["models_config"]), log_path=run_dir / "llm_calls.jsonl"),
                   Workspace(context))

    def refresh_kb(self) -> None:
        self.kb = KnowledgeBase(self.kernel.knowledge_files(), load_history(self.kernel.scope.runs / "history.jsonl"))

    def reference(self) -> str:
        """Project background and MARS experiment history, for the models' analysis and decisions."""
        return "\n".join(c["text"] for c in self.kb.chunks if c["kind"] in ("project_context", "mars_history"))[:24000] or "本项目暂无背景资料与实验历史；不要套用其他项目的结果。"

    # ----- artifacts -----
    def path(self, relative: str) -> Path:
        target = self.run_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def has(self, relative: str) -> bool:
        return (self.run_dir / relative).exists()

    def save(self, relative: str, value: Any) -> Path:
        target = self.path(relative)
        if isinstance(value, BaseModel):
            value = value.model_dump()
        if isinstance(value, list):
            value = [v.model_dump() if isinstance(v, BaseModel) else v for v in value]
        if relative.endswith(".md"):
            target.write_text(str(value), encoding="utf-8")
        else:
            target.write_text(json.dumps(value, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        return target

    def load(self, relative: str) -> Any:
        text = (self.run_dir / relative).read_text(encoding="utf-8")
        return text if relative.endswith(".md") else json.loads(text)

    def event(self, stage: str, message: str, **data: Any) -> None:
        record = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "stage": stage, "message": message, **data}
        with self.path("events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        print(f"[{record['ts']}] {stage}: {message}", flush=True)

    def remember(self, record: dict[str, Any]) -> None:
        """Append to the cross-run MARS experiment history (part of the knowledge base for later runs)."""
        history = self.kernel.scope.runs / "history.jsonl"
        history.parent.mkdir(parents=True, exist_ok=True)
        with history.open("a", encoding="utf-8") as handle:
            from mars.osutil import lock
            lock(handle, blocking=True)
            binding = {"project_root": str(self.kernel.scope.root), "binding_id": self.kernel.context.binding["id"],
                       "source_commit": self.kernel.cfg["repo"]["commit"], "environment": self.kernel.environment}
            handle.write(json.dumps({"run": self.run_dir.name, **binding, **record}, ensure_ascii=False, default=str) + "\n")
