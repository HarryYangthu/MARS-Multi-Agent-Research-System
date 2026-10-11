"""Prepare a run's Git branch before constructing model or command contexts."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict

from app.bridge.node_key import parse_node_key
from app.harness.tools.git_branch import (
    GitWorkspaceError, bind_git_branch, changed_files, changes_path, checkpoint_changes, git, preflight, prepare,
    receipt_path, repository_lock,
)
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
    branch = prepare(repo.root, run.root, run.project, run.run_id)
    with repository_lock(branch.root):
        run.write_event("agent_events", {"event": "coding.git_branch_ready", "agent": stage,
            "node": node_key, **asdict(branch), "message": f"已就绪独立研究代码目录 {branch.repo_path}，分支 {branch.branch}。"})
        with bind_git_branch(branch):
            if stage == "execution":
                checkpoint_approved_code(run)
            try:
                yield
            finally:
                # Persist partial work on failures/stops as well as on success.
                # Saving a local commit never approves a scientific handoff.
                if stage == "coding":
                    import sys
                    interrupted = sys.exc_info()[0] is not None
                    try:
                        # An external branch switch revokes the write grant.
                        if git(branch.root, "branch", "--show-current") == branch.branch:
                            commit, files, remaining = checkpoint_changes(branch, run.root)
                            if commit is not None:
                                run.write_event("agent_events", {"event": "coding.git_checkpoint", "agent": "coding",
                                    "branch": branch.branch, "commit": commit, "files": files,
                                    "interrupted": interrupted, "approved": False,
                                    "message": "编码改动已自动提交到本任务分支。"})
                            if remaining:
                                run.write_event("agent_events", {"event": "coding.git_unrecorded_changes", "agent": "coding",
                                    "branch": branch.branch, "files": remaining,
                                    "message": "这些文件没有匹配的编码写入记录，已保留且未混入自动提交。"})
                    except (OSError, ValueError) as exc:
                        run.write_event("agent_events", {"event": "coding.git_checkpoint_failed", "agent": "coding",
                            "branch": branch.branch, "message": str(exc)})
                        if not interrupted:
                            raise


def checkpoint_approved_code(run: RunHandle) -> None:
    """Commit only approved paths, before any performance process starts."""
    repo = load_project_repo(run.project)
    if repo.git_branch is None:
        return
    # Check the entire candidate against its baseline, including auto-saved
    # commits. A clean checkout alone does not imply human/Commander approval.
    changed = changed_files(repo.root, repo.git_branch.baseline_commit)
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
    pending = changed_files(repo.root)
    if not pending:
        return
    if changes_path(run.root).exists():
        commit, files, _ = checkpoint_changes(repo.git_branch, run.root, strict=True)
    else:
        # Existing human-authored deliveries may predate governed write records.
        # Preserve their explicitly approved, path-limited checkpoint behavior.
        files = sorted(pending)
        paths = [":(literal)" + name for name in files]
        git(repo.root, "add", "--", *paths)
        git(repo.root, "-c", "user.name=MARS", "-c", "user.email=mars@localhost",
            "-c", "commit.gpgsign=false", "commit", "-m", f"Research checkpoint: {run.run_id}", "--", *paths)
        commit = git(repo.root, "rev-parse", "HEAD")
    if commit is not None:
        run.write_event("agent_events", {"event": "coding.git_checkpoint", "agent": "coding",
            "branch": repo.git_branch.branch, "commit": commit, "files": files, "approved": True})
