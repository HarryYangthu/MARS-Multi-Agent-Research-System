"""Prepare a run's Git branch before constructing model or command contexts."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict

from app.bridge.node_key import parse_node_key
from app.harness.tools.git_branch import GitWorkspaceError, bind_git_branch, git, preflight, prepare, receipt_path, repository_lock
from app.harness.tools.project_repo import load_project_repo, resolve_allowed_path
from app.harness.schema.frontmatter_parser import parse
from app.storage.run_store import RunHandle


def coding_workspace_blocker(run: RunHandle) -> str:
    repo = load_project_repo(run.project)
    if repo.scope is not None:
        return ""
    if not repo.read_only and not (repo.root / ".git").exists():
        return ""  # Explicitly writable non-Git integrations retain their policy.
    return preflight(repo.root, run.root, run.project, run.run_id)


def stopped_coding_retry_blocker(run: RunHandle, node_key: str) -> str:
    """Explicit new invocation only; never replay an unknown write or job."""
    import json
    from app.bridge.task_runtime import task_contract_path
    from app.harness.runtime.task_contract import TaskEnvelope
    from app.harness.llm.accounting import RunModelBudget
    if not receipt_path(run.root).is_file():
        return "此任务没有已绑定的 Git 实验分支。"
    blocker = coding_workspace_blocker(run)
    if blocker:
        return blocker
    task = TaskEnvelope.model_validate_json(task_contract_path(run, node_key).read_text())
    if (task.run_id, task.project, task.node_id, task.agent) != (run.run_id, run.project, node_key, "coding"):
        return "编码检查点身份不匹配。"
    checkpoint = run.root / "agent_traces/coding" / task.invocation_id / "checkpoint.json"
    if not checkpoint.resolve().is_relative_to(run.root.resolve()):
        return "编码检查点路径异常。"
    state = json.loads(checkpoint.read_text())
    if state.get("pending") == "tool" or state.get("pending_batch"):
        return "有结果未确认的工具操作，不能重新执行编码阶段。"
    if any(row.get("status") in {"in_flight", "reconciliation_required"}
           for row in RunModelBudget(run.root).recovery_snapshot()["requests"].values()):
        return "有结果未确认的模型请求，请先核对调用记录。"
    return ""


@contextmanager
def research_branch_scope(run: RunHandle, node_key: str) -> Iterator[None]:
    stage = parse_node_key(node_key).stage
    if stage != "coding" and not receipt_path(run.root).exists():
        yield
        return
    repo = load_project_repo(run.project)
    if repo.scope is not None or not repo.read_only and not (repo.root / ".git").exists():
        yield
        return
    with repository_lock(repo.root):
        branch = prepare(repo.root, run.root, run.project, run.run_id)
        run.write_event("agent_events", {"event": "coding.git_branch_ready", "agent": stage,
            "node": node_key, **asdict(branch), "message": f"已就绪实验分支 {branch.branch}，复用原代码目录。"})
        with bind_git_branch(branch):
            if stage == "execution":
                checkpoint_approved_code(run)
            yield


def checkpoint_approved_code(run: RunHandle) -> None:
    """Commit only approved paths, before any performance process starts."""
    repo = load_project_repo(run.project)
    if repo.git_branch is None:
        return
    changed = set(filter(None, git(repo.root, "diff", "--name-only", "--no-renames", "-z", "HEAD").split("\0")))
    changed.update(filter(None, git(repo.root, "ls-files", "--others", "--exclude-standard", "-z").split("\0")))
    if not changed:
        return
    approved = run.root / "coding" / "code_spec.approved.md"
    if not approved.is_file():
        raise GitWorkspaceError("编码改动尚未审核，不能提交或开始实验。", "git_workspace_code_unapproved")
    metadata = parse(approved.read_text(encoding="utf-8")).metadata
    rows = metadata.get("files_changed", [])
    declared = {str(row.get("path", "")) for row in rows if isinstance(row, dict)} if isinstance(rows, list) else set()
    if not changed.issubset(declared):
        raise GitWorkspaceError("实验分支有未纳入已审核代码方案的改动，请先核对；不会自动提交其他文件。",
                                "git_workspace_unreviewed_changes")
    for name in changed:
        resolve_allowed_path(repo, name, for_write=True)
    # A path-limited commit neither publishes nor merges; repository/global
    # author preferences remain unchanged. Disable signing for local checkpoints.
    paths = [":(literal)" + name for name in sorted(changed)]
    git(repo.root, "add", "--", *paths)
    git(repo.root, "-c", "user.name=MARS", "-c", "user.email=mars@localhost",
        "-c", "commit.gpgsign=false", "commit", "-m", f"Research checkpoint: {run.run_id}", "--", *paths)
    run.write_event("agent_events", {"event": "coding.git_checkpoint", "agent": "coding",
        "branch": repo.git_branch.branch, "commit": git(repo.root, "rev-parse", "HEAD"), "files": sorted(changed)})
