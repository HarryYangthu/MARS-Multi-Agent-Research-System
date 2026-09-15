"""CLI research orchestration over real agents, frozen source and bounded workers."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import platform
import subprocess
from typing import Any, Literal, Protocol

from filelock import FileLock
from loguru import logger
import yaml

from app.execution.research_process import run_worker
from app.execution.subprocess_env import sanitized_subprocess_environment
from app.harness.agent_loop.trace import atomic_json
from app.harness.discovery.code_candidate import CodeCandidateSpec, TensorInterfaceSpec, code_candidate_spec_sha256
from app.harness.discovery.code_materialization import (
    CodeBlobOperation, CodeMaterializationBundle, content_blob_path, content_sha256,
    materialize_code_workspace,
)
from app.harness.discovery.snapshots import SnapshotPolicy, create_snapshot, verify_snapshot
from app.harness.discovery.source_commit import archive_source_commit
from app.harness.project_workspace import open_folder
from app.harness.research_trial import ResearchBudget, compare, file_sha256, read_record, select_candidate
from app.harness.schema.frontmatter_parser import dumps, parse
from app.harness.schema.validator import validate_document
from app.harness.tools.registry import ToolContext, get_registry
from app.settings import repo_root


class ResearchAgents(Protocol):
    async def invoke(self, stage: str, project: str, task: str, upstream: dict[str, str], root: Path) -> str: ...


def failed_stage_context(stage_root: Path) -> dict[str, str]:
    checkpoints = sorted(stage_root.glob("agent_traces/*/*/checkpoint.json"), key=lambda p: p.stat().st_mtime)
    if not checkpoints:
        return {}
    path = checkpoints[-1]
    previous = read_record(path)
    if previous.get("status") == "passed":
        return {}
    candidate = previous.get("candidate", "")
    issues = {key: previous.get(key) for key in ("status", "review_issues", "validation_issues", "feedback")}
    if not candidate and not any(issues.get(key) for key in ("review_issues", "validation_issues", "feedback")):
        return {}
    return {"rejected_stage_candidate": str(candidate),
            "actual_stage_feedback": json.dumps(issues, ensure_ascii=False),
            "stage_feedback_origin": str(path)}


def configuration() -> dict[str, Any]:
    raw: dict[str, Any] = yaml.safe_load((repo_root() / "configs/cli_research.yaml").read_text(encoding="utf-8"))
    return raw


def runtime_hashes() -> dict[str, str]:
    paths = [*sorted((repo_root() / "backend/app").rglob("*.py")),
             *sorted((repo_root() / "configs").rglob("*.yaml")),
             *sorted((repo_root() / "backend/app/harness/schema/schemas").glob("*.json"))]
    return {p.relative_to(repo_root()).as_posix(): file_sha256(p) for p in paths}


def runtime_environment() -> dict[str, str]:
    result = {"python": platform.python_version(), "platform": platform.platform()}
    for package in ("torch", "numpy", "scipy", "openai", "httpx", "pydantic", "PyYAML", "jsonschema", "filelock"):
        try:
            result[package] = version(package)
        except PackageNotFoundError:
            result[package] = "not-installed"
    return result


def prepare_protocol(repo: Path, data: Path, budget: ResearchBudget) -> dict[str, Any]:
    config_path = repo / configuration()["source_config"]
    cfg: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    baseline_path = (repo / cfg["baseline_config"]).resolve()
    if not baseline_path.is_relative_to(repo.resolve()):
        raise ValueError("Baseline config must belong to the static repository")
    if cfg.get("optimizer", "adam") != "adam":
        raise ValueError("This matched CLI protocol supports Adam only")
    cfg["baseline"] = yaml.safe_load(baseline_path.read_text(encoding="utf-8"))
    cfg.update({"data_path": str(data), "data_sha256": file_sha256(data) if data.is_file() else None,
                "max_steps": budget.max_steps, "reduction": budget.reduction,
                "selection": "validation only; final test evaluated only after candidate selection",
                "initialization": "from scratch with the fixed seed; no trained baseline checkpoint or extra fitting budget",
                "metric": "10log10(mean_channel(Perr/Pnf)); lower is better",
                "adapter_version": "pimc_static.cli.v1"})
    return cfg


def initialize(repo: Path, data: Path, output: Path, task: str, model: str, budget: ResearchBudget) -> dict[str, Any]:
    if not repo.is_dir():
        raise ValueError(f"Research repository does not exist: {repo}")
    cfg = configuration()
    for relative in cfg["context_files"]:
        if not (repo / relative).is_file():
            raise ValueError(f"Missing static repository source: {relative}")
    if output.exists():
        raise ValueError("Run directory already exists; use mars resume")
    if (repo / cfg["candidate_path"]).exists():
        raise ValueError("Reserved candidate path already exists in source; choose a clean research checkout")
    protocol = prepare_protocol(repo, data, budget)
    output.mkdir(parents=True)
    for name in ("input", "context", "idea", "experiment", "coding", "execution", "writing", "hitl", "events"):
        (output / name).mkdir()
    project = open_folder(str(repo))
    source_files: list[str] = []
    for prefix in cfg["source_paths"]:
        path = repo / prefix
        for item in ([path] if path.is_file() else sorted(path.rglob("*"))):
            if item.is_file() and item.suffix in {".py", ".yaml", ".yml", ".md", ".json", ".toml", ".txt"}:
                relative = item.relative_to(repo)
                if not any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
                    source_files.append(relative.as_posix())
    snapshot = create_snapshot(source_root=repo, cache_root=output / "source_snapshots", project=project.name,
        source_ref=str(repo), policy=SnapshotPolicy(allowed_paths=tuple(sorted(set(source_files))),
        ignore_patterns=("__pycache__/", "*.pyc", ".env*", "**/.env*")))
    context = {name: (snapshot.root / name).read_text(encoding="utf-8") for name in cfg["context_files"]}
    frozen_commit = archive_source_commit(source_root=snapshot.root, git_dir=output / "source_commits/baseline.git",
        paths=[item.path for item in snapshot.manifest.files], environment=sanitized_subprocess_environment())
    atomic_json(output / "context/source.json", context)
    atomic_json(output / "experiment/protocol.json", protocol)
    revision = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True)
    source_status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True)
    manifest = {"schema": "cli_research.run.v1", "created_at": datetime.now(timezone.utc).isoformat(),
        "project": project.name, "repo": str(repo), "data": str(data), "task": task, "model": model,
        "budget": budget.model_dump(), "source_snapshot": str(snapshot.root), "runtime_hashes": runtime_hashes(),
        "environment": runtime_environment(),
        "source_commit": revision.stdout.strip() if revision.returncode == 0 else None,
        "frozen_source_commit": frozen_commit,
        "source_tracked_dirty": bool(source_status.stdout.strip()) if source_status.returncode == 0 else None,
        "protocol_sha256": file_sha256(output / "experiment/protocol.json"),
        "context_sha256": file_sha256(output / "context/source.json"), "configuration": cfg}
    atomic_json(output / "input/manifest.json", manifest)
    state: dict[str, Any] = {"status": "ready" if data.is_file() else "blocked_data", "artifacts": {}, "trials": {},
                             "attempts": {}, "manifest_sha256": file_sha256(output / "input/manifest.json")}
    atomic_json(output / "state.json", state)
    write_report(output, manifest, state)
    return state


def verify_run(root: Path, manifest: dict[str, Any], state: dict[str, Any]) -> None:
    if file_sha256(root / "input/manifest.json") != state["manifest_sha256"]:
        raise ValueError("Run manifest changed")
    if runtime_hashes() != manifest["runtime_hashes"]:
        raise ValueError("Harness/configuration changed; start a new run to preserve reproducibility")
    if runtime_environment() != manifest["environment"]:
        raise ValueError("Dependency environment changed; start a new run")
    if file_sha256(root / "experiment/protocol.json") != manifest["protocol_sha256"]:
        raise ValueError("Frozen evaluation protocol changed")
    if file_sha256(root / "context/source.json") != manifest["context_sha256"]:
        raise ValueError("Frozen source context changed")
    protocol = read_record(root / "experiment/protocol.json")
    if protocol["data_sha256"] is not None and file_sha256(Path(manifest["data"])) != protocol["data_sha256"]:
        raise ValueError("Dataset changed since the protocol was frozen")
    snapshot = verify_snapshot(Path(manifest["source_snapshot"]))
    # Research read tools use the connected checkout; it must still match the frozen source.
    for item in snapshot.manifest.files:
        if "sha256:" + file_sha256(Path(manifest["repo"]) / item.path) != item.sha256:
            raise ValueError(f"Research checkout changed: {item.path}")
    for relative, expected in state["artifacts"].items():
        if file_sha256(root / relative) != expected:
            raise ValueError(f"Archived evidence changed: {relative}")


async def materialize(root: Path, manifest: dict[str, Any], candidate_id: str, document: str) -> Path:
    source = parse(document).metadata["source_code"]
    relative = str(manifest["configuration"]["candidate_path"])
    audit = await get_registry().dispatch("code.patch_generator", {"path": relative, "content": source},
        ToolContext(run_id=root.name, project=manifest["project"], agent="coding", dry_run=True,
                    project_repo_root=manifest["repo"], extra={"run_root": str(root)}))
    if not audit.ok or audit.requires_approval or audit.blocked_by_gate:
        raise ValueError("Candidate rejected by tool/Gate 5 policy: " + str(audit.error or audit.blocked_by_gate))
    snapshot = verify_snapshot(Path(manifest["source_snapshot"]))
    blob_hash = content_sha256(source.encode("utf-8"))
    spec = CodeCandidateSpec(base_snapshot_id=snapshot.manifest.snapshot_id, entrypoint=relative,
        patch_ref=f"coding/{candidate_id}.md", patch_sha256=blob_hash, touched_paths=(relative,),
        interface=TensorInterfaceSpec(input_rank=2, output_rank=2, input_dtype="complex64", output_dtype="complex64"))
    bundle = CodeMaterializationBundle(base_snapshot_id=snapshot.manifest.snapshot_id,
        code_spec_sha256=code_candidate_spec_sha256(spec),
        operations=(CodeBlobOperation(path=relative, action="add", content_sha256=blob_hash),))
    blob_root = root / "code_blobs"
    blob = content_blob_path(blob_root, blob_hash)
    blob.parent.mkdir(parents=True, exist_ok=True)
    if blob.exists() and content_sha256(blob.read_bytes()) != blob_hash:
        raise ValueError("Candidate content store changed")
    blob.write_text(source, encoding="utf-8")
    workspace = materialize_code_workspace(snapshot_root=snapshot.root, blob_root=blob_root,
        workspaces_root=root / "candidates", candidate_id=candidate_id, bundle=bundle,
        allowed_paths=tuple(manifest["configuration"]["source_paths"]), expected_touched_paths=(relative,),
        expected_entrypoint=relative, protected_paths=("tools", "configs", "libs/model.py"))
    candidate_commit = archive_source_commit(source_root=workspace.root, git_dir=root / "source_commits" / f"{candidate_id}.git",
        paths=[item.path for item in workspace.manifest.files], environment=sanitized_subprocess_environment())
    atomic_json(root / "coding" / f"{candidate_id}.receipt.json", {
        "spec": spec.model_dump(mode="json"), "bundle": bundle.model_dump(mode="json"),
        "workspace_manifest_sha256": workspace.manifest_sha256, "source_commit": candidate_commit, "gate5": "passed",
        "scope": "add isolated candidate only; baseline/evaluator/config files protected"})
    return workspace.root / relative


class CliResearchService:
    def __init__(self, agents: ResearchAgents) -> None:
        self.agents = agents

    async def run(self, root: Path, *, prepare_only: bool = False) -> dict[str, Any]:
        with FileLock(str(root / "run.lock"), timeout=0):
            manifest, state = read_record(root / "input/manifest.json"), read_record(root / "state.json")
            verify_run(root, manifest, state)
            if state["status"] in {"goal_met_within_budget", "goal_not_met"}:
                return state
            budget = ResearchBudget.model_validate(manifest["budget"])
            protocol = read_record(root / "experiment/protocol.json")
            if not Path(manifest["data"]).is_file() or protocol["data_sha256"] is None:
                state["status"] = "blocked_data"
                state["error"] = "Real dataset unavailable when the protocol was frozen; start a new run after supplying it"
                if not prepare_only:
                    atomic_json(root / "state.json", state)
                    write_report(root, manifest, state)
                    return state
            source = read_record(root / "context/source.json")
            base_context = {"source_code": json.dumps(source, ensure_ascii=False),
                "frozen_protocol": json.dumps(protocol, ensure_ascii=False),
                "goal": json.dumps(budget.model_dump(), ensure_ascii=False)}
            try:
                preflight = await self.trial(root, manifest, state, "baseline_preflight", None, operation="preflight")
                if preflight["status"] != "completed":
                    raise RuntimeError("Baseline/data preflight failed before model calls; see execution/baseline_preflight")
                base_context["baseline_preflight"] = json.dumps(preflight, ensure_ascii=False)
                state["status"] = "researching"
                atomic_json(root / "state.json", state)
                proposal = await self.artifact(root, manifest, state, "research", "idea/proposal.md", base_context)
                if prepare_only:
                    code = await self.artifact(root, manifest, state, "coding", "coding/round_01.md", {**base_context, "proposal": proposal})
                    candidate_path = await materialize(root, manifest, "round_01", code)
                    candidate_preflight = await self.trial(root, manifest, state, "candidate_preflight", candidate_path, operation="preflight")
                    if candidate_preflight["status"] != "completed":
                        raise RuntimeError("Generated candidate failed actual preflight; see execution/candidate_preflight")
                    state["status"] = "prepared_only"
                    state["error"] = "No PIMC experiment was performed in prepare-only mode"
                else:
                    baseline = await self.trial(root, manifest, state, "baseline", None)
                    if baseline["status"] != "completed":
                        raise RuntimeError("Baseline execution failed; see execution/baseline logs")
                    for index in range(1, budget.rounds + 1):
                        candidate_id = f"round_{index:02d}"
                        previous = {name: value for name, value in state["trials"].items() if not name.startswith("final_")}
                        feedback_path = root / "writing" / f"round_{index-1:02d}.md"
                        feedback = feedback_path.read_text(encoding="utf-8") if feedback_path.exists() else "No previous candidate experiment."
                        previous_code_path = root / "coding" / f"round_{index-1:02d}.md"
                        previous_code = previous_code_path.read_text(encoding="utf-8") if previous_code_path.exists() else "No previous candidate code."
                        code = await self.artifact(root, manifest, state, "coding", f"coding/{candidate_id}.md",
                            {**base_context, "proposal": proposal, "experiments": json.dumps(previous, ensure_ascii=False),
                             "previous_analysis": feedback, "previous_code": previous_code})
                        if candidate_id not in state["trials"]:
                            try:
                                candidate_path = await materialize(root, manifest, candidate_id, code)
                                result = await self.trial(root, manifest, state, candidate_id, candidate_path)
                            except ValueError as exc:
                                result = {"status": "failed", "candidate_id": candidate_id, "error": str(exc)}
                                state["trials"][candidate_id] = result
                                atomic_json(root / "state.json", state)
                        result = state["trials"][candidate_id]
                        observed = {"baseline": baseline, "candidate": result}
                        if result["status"] == "completed":
                            observed["comparison"] = compare(baseline, result, budget)
                        await self.artifact(root, manifest, state, "analysis", f"writing/{candidate_id}.md",
                            {"proposal": proposal, "code_spec": code, "actual_experiment": json.dumps(observed, ensure_ascii=False),
                             "goal": base_context["goal"], "remaining_rounds": str(budget.rounds-index)})
                    candidates = [c for name, c in state["trials"].items() if name.startswith("round_")]
                    selected = select_candidate(baseline, candidates, budget)
                    # Freeze selection before any final-test worker is launched. Never loop back after test.
                    state["selected"] = selected["candidate_id"] if selected else None
                    state["status"] = "finalizing"
                    atomic_json(root / "state.json", state)
                    if selected:
                        final_baseline = await self.trial(root, manifest, state, "final_baseline", None, checkpoint=baseline)
                        candidate_path = Path(selected["candidate_path"])
                        final_candidate = await self.trial(root, manifest, state, "final_candidate", candidate_path, checkpoint=selected)
                        if final_baseline["status"] != "completed" or final_candidate["status"] != "completed":
                            raise RuntimeError("Final evaluation failed; no success claim is permitted")
                        state["final_comparison"] = compare(final_baseline, final_candidate, budget, split="test")
                        state["status"] = "goal_met_within_budget" if state["final_comparison"]["passed"] else "goal_not_met"
                    else:
                        state["status"] = "goal_not_met"
                if state["status"] != "prepared_only":
                    state.pop("error", None)
            except (Exception, KeyboardInterrupt, asyncio.CancelledError) as exc:
                state["status"] = "interrupted" if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)) else "failed"
                state["error"] = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                atomic_json(root / "state.json", state)
                write_report(root, manifest, state)
            return state

    async def artifact(self, root: Path, manifest: dict[str, Any], state: dict[str, Any], stage: str,
                       relative: str, upstream: dict[str, str]) -> str:
        verify_run(root, manifest, state)
        path = root / relative
        schema = {"research": "proposal.v1", "coding": "code_spec.v1", "analysis": "report.v1"}[stage]
        if relative in state["artifacts"]:
            return path.read_text(encoding="utf-8")
        attempts = state.setdefault("agent_attempts", {})
        stage_root = root / "stages" / path.parent.name / path.stem
        task = manifest["task"] + "\n宿主固定的验收目标和计算预算（不得修改）：" + json.dumps(manifest["budget"], ensure_ascii=False)
        while int(attempts.get(relative, 0)) < 2:
            repair = failed_stage_context(stage_root) if attempts.get(relative) else {}
            attempts[relative] = int(attempts.get(relative, 0)) + 1
            atomic_json(root / "state.json", state)
            logger.info("Stage {} attempt {} -> {}", stage, attempts[relative], relative)
            try:
                text = await self.agents.invoke(stage, manifest["project"], task, {**upstream, **repair}, stage_root)
                break
            except RuntimeError as exc:
                state.setdefault("stage_failures", {}).setdefault(relative, []).append(str(exc))
                atomic_json(root / "state.json", state)
                if attempts[relative] >= 2 or not failed_stage_context(stage_root):
                    raise
                logger.warning("{} rejected; retrying with the recorded candidate and review feedback", stage)
        else:
            raise ValueError("Agent-stage restart budget exhausted; inspect archived traces")
        validation = validate_document(text, expected_schema=schema)
        if not validation.valid:
            raise ValueError("Invalid downstream artifact: " + str(validation.first_error()))
        path.write_text(text, encoding="utf-8")
        state["artifacts"][relative] = file_sha256(path)
        atomic_json(root / "state.json", state)
        return text

    async def trial(self, root: Path, manifest: dict[str, Any], state: dict[str, Any], name: str,
                    candidate: Path | None, *, checkpoint: dict[str, Any] | None = None,
                    operation: Literal["train", "preflight"] = "train") -> dict[str, Any]:
        verify_run(root, manifest, state)
        if name in state["trials"]:
            return dict(state["trials"][name])
        budget = ResearchBudget.model_validate(manifest["budget"])
        attempt = int(state["attempts"].get(name, 0)) + 1
        if attempt > 2:
            raise ValueError("Trial restart budget exhausted; inspect previous worker evidence")
        state["attempts"][name] = attempt
        state["status"] = "finalizing" if checkpoint else "experimenting"
        atomic_json(root / "state.json", state)
        job = {"operation": "finalize" if checkpoint else operation, "repo": manifest["source_snapshot"],
               "protocol": str(root / "experiment/protocol.json"), "candidate": str(candidate) if candidate else None}
        candidate_hash = file_sha256(candidate) if candidate else None
        if checkpoint:
            job.update({"checkpoint": str(Path(checkpoint["output"]) / "best.pt"),
                        "checkpoint_sha256": checkpoint["checkpoint_sha256"]})
            if candidate and file_sha256(candidate) != checkpoint["candidate_sha256"]:
                raise ValueError("Selected candidate code changed")
        logger.info("Experiment {} (CPU, {} updates maximum)", name, budget.max_steps)
        result = await run_worker(job, root / "execution" / name / f"attempt_{attempt:02d}", budget.timeout_seconds)
        verify_run(root, manifest, state)
        if candidate and file_sha256(candidate) != candidate_hash:
            raise ValueError("Candidate source changed during execution")
        result.update({"candidate_id": name, "candidate_path": str(candidate) if candidate else None})
        state["trials"][name] = result
        relative = Path(result["output"]).relative_to(root) / "result.json"
        state["artifacts"][relative.as_posix()] = file_sha256(root / relative)
        if candidate:
            state["artifacts"][candidate.relative_to(root).as_posix()] = file_sha256(candidate)
        atomic_json(root / "state.json", state)
        return result


def write_report(root: Path, manifest: dict[str, Any], state: dict[str, Any]) -> None:
    budget = ResearchBudget.model_validate(manifest["budget"])
    body = [f"# CLI 研究报告\n\n状态：{state['status']}\n\n任务：{manifest['task']}",
        f"\n目标：参数减少至少 {budget.reduction:.0%}；RES 退化不超过 {budget.max_degradation_db:g} dB。",
        f"\n预算：每个方法最多 {budget.max_steps} 个 Adam 更新、{budget.timeout_seconds} 秒，最多 {budget.rounds} 轮候选。",
        "\n基线与候选使用同一固定种子、划分、损失和预算。候选只依据验证集选择；选定后分别测试基线和候选。",
        "\n此结果只代表固定数据划分与计算预算；少量更新不能证明模型收敛、多种子稳定性或普遍性能不变。",
        "\n| 实验 | 状态 | 实参数量 | 验证 RES(dB) | 测试 RES(dB) | 更新数 |\n|---|---|---:|---:|---:|---:|"]
    for name, trial in state["trials"].items():
        body.append(f"| {name} | {trial['status']} | {trial.get('real_parameters', '—')} | "
                    f"{trial.get('validation', {}).get('RES_db', '—')} | {trial.get('test', {}).get('RES_db', '—')} | {trial.get('optimizer_steps', '—')} |")
    if state.get("error"):
        body.append("\n阻塞或错误：" + state["error"])
    if state.get("final_comparison"):
        body.append("\n最终判定：\n```json\n" + json.dumps(state["final_comparison"], ensure_ascii=False, indent=2) + "\n```")
    body.append("\n## 可复查证据\n\n固定输入与源码哈希：input/manifest.json；评测协议：experiment/protocol.json。")
    for relative in state["artifacts"]:
        if relative.endswith(".md"):
            body.append(f"\n- [{relative}]({relative})")
    body.append("\n模型调用与工具收据：stages/；候选代码与 Gate 5 收据：coding/、candidates/；实验日志、逐轮验证曲线和最优权重：execution/。")
    metadata = {"schema": "report.v1", "project": manifest["project"], "agent": "writing",
                "deliverable_type": "research_report", "target_audience": "研究者",
                "chain_refs": {"proposal": "idea/proposal.md", "plan": "experiment/protocol.json", "runs": list(state["trials"])}}
    text = dumps(metadata, "\n".join(body))
    if not validate_document(text, expected_schema="report.v1").valid:
        raise ValueError("Host report schema validation failed")
    (root / "report.md").write_text(text, encoding="utf-8")
