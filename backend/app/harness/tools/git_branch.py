"""Host-owned, persistent Git branches in the user's existing checkout.

No clone, worktree, stash, reset, pull or merge is performed. A branch grant is
local to an executing run; the project's baseline binding stays read-only.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re

from filelock import FileLock, Timeout

from app.harness.agent_loop.trace import atomic_json
from app.harness.runtime.git_runtime import GitRuntimeError, run_git


class GitWorkspaceError(ValueError):
    def __init__(self, message: str, code: str = "git_workspace_not_ready") -> None:
        super().__init__(message)
        self.reason = {"code": code}


def git(root: Path, *arguments: str) -> str:
    try:
        result = run_git(("-C", str(root), *arguments))
    except GitRuntimeError as exc:
        raise GitWorkspaceError(str(exc), exc.reason["code"]) from exc
    return result.stdout.rstrip("\r\n")


def branch_name(project: str, run_id: str) -> str:
    suffix = hashlib.sha256(f"{project}\0{run_id}".encode()).hexdigest()[:12]
    slug = re.sub(r"[^a-zA-Z0-9_-]", "-", run_id).strip("-")[:48] or "research"
    return f"mars/{slug}-{suffix}"


def _branch(root: Path) -> str:
    value = git(root, "branch", "--show-current")
    if not value:
        raise GitWorkspaceError("代码仓处于游离提交状态，请先选择基线分支。")
    return value


def _git_root(root: Path) -> Path:
    if not root.is_dir() or root.is_symlink():
        raise GitWorkspaceError("代码目录不存在或是符号链接，请重新关联代码仓。")
    if not (root / ".git").exists():
        raise GitWorkspaceError("此代码目录尚未建立 Git 仓库，请先初始化并提交基线代码；不会创建实验副本。")
    if Path(git(root, "rev-parse", "--show-toplevel")).resolve() != root.resolve():
        raise GitWorkspaceError("请关联 Git 仓库根目录，不能将仓库子目录作为实验分支工程。")
    return Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))


@dataclass(frozen=True)
class GitBranch:
    project: str
    run_id: str
    repo_path: str
    branch: str
    baseline_branch: str
    baseline_commit: str

    @property
    def root(self) -> Path:
        return Path(self.repo_path)

    def validate(self, project: str, run_id: str | None = None) -> None:
        if self.project != project or run_id is not None and self.run_id != run_id:
            raise GitWorkspaceError("实验分支不属于当前任务。", "git_workspace_identity_changed")
        if _branch(self.root) != self.branch:
            raise GitWorkspaceError("代码仓已切换到其他分支，当前操作已停止；请恢复本任务分支。",
                                    "git_workspace_branch_changed")
        if git(self.root, "rev-parse", "--verify", f"refs/heads/{self.baseline_branch}") != self.baseline_commit:
            raise GitWorkspaceError("原基线分支已有新提交，请核对后重新开展研究；不会覆盖基线。",
                                    "git_workspace_baseline_changed")
        git(self.root, "merge-base", "--is-ancestor", self.baseline_commit, "HEAD")


def receipt_path(run_root: Path) -> Path:
    return run_root / "input" / "research_branch.json"


def _load(root: Path, run_root: Path, project: str, run_id: str) -> GitBranch | None:
    path = receipt_path(run_root)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.pop("schema_id", None) != "research.git_branch.v1":
            raise ValueError("schema")
        branch = GitBranch(**raw)
        if (branch.project != project or branch.run_id != run_id
                or branch.repo_path != str(root.resolve())
                or branch.branch != branch_name(project, run_id)
                or not re.fullmatch(r"[0-9a-f]{40,64}", branch.baseline_commit)):
            raise ValueError("identity")
        git(root, "check-ref-format", f"refs/heads/{branch.baseline_branch}")
        return branch
    except (OSError, TypeError, ValueError) as exc:
        raise GitWorkspaceError("实验分支记录无法校验，请核对任务与代码仓关联。",
                                "git_workspace_identity_changed") from exc


def _inspect(root: Path, run_root: Path, project: str, run_id: str) -> GitBranch:
    _git_root(root)
    saved = _load(root, run_root, project, run_id)
    current = _branch(root)
    expected = branch_name(project, run_id)
    if saved is not None:
        if git(root, "rev-parse", "--verify", f"refs/heads/{saved.baseline_branch}") != saved.baseline_commit:
            raise GitWorkspaceError("原基线分支已有新提交，请核对研究基线。", "git_workspace_baseline_changed")
        if current == expected:
            saved.validate(project, run_id)
            return saved  # Interrupted work on this very branch is retained.
    if git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise GitWorkspaceError("代码仓有未提交改动，请先提交或自行保存；系统不会自动暂存、丢弃或携带到其他任务。",
                                "git_workspace_dirty")
    if saved is not None:
        return saved
    # Never claim a pre-existing, unowned branch (including interrupted creation).
    branches = git(root, "for-each-ref", "--format=%(refname:short)", f"refs/heads/{expected}").splitlines()
    if expected in branches:
        raise GitWorkspaceError("同名实验分支已存在但缺少任务记录，请先核对。", "git_workspace_identity_changed")
    return GitBranch(project, run_id, str(root.resolve()), expected, current, git(root, "rev-parse", "HEAD"))


@contextmanager
def repository_lock(root: Path) -> Iterator[None]:
    lock = FileLock(_git_root(root) / "mars-research.lock", timeout=0)
    try:
        lock.acquire()
    except Timeout as exc:
        raise GitWorkspaceError("同一代码仓已有研究阶段在运行，请等待结束；不会并行切换分支。",
                                "git_workspace_busy") from exc
    try:
        yield
    finally:
        lock.release()


def preflight(root: Path, run_root: Path, project: str, run_id: str) -> str:
    """Inspect only; recovery polling never creates or switches branches."""
    try:
        with repository_lock(root):
            _inspect(root, run_root, project, run_id)
    except (OSError, ValueError) as exc:
        return str(exc)
    return ""


def prepare(root: Path, run_root: Path, project: str, run_id: str) -> GitBranch:
    """Caller must hold repository_lock for the entire code-consuming stage."""
    branch = _inspect(root, run_root, project, run_id)
    path = receipt_path(run_root)
    if not path.exists():
        # Persist intent before switch, so a crash can resume without adoption.
        atomic_json(path, {"schema_id": "research.git_branch.v1", **asdict(branch)})
    if _branch(root) != branch.branch:
        exists = branch.branch in git(root, "for-each-ref", "--format=%(refname:short)",
                                       f"refs/heads/{branch.branch}").splitlines()
        if exists:
            git(root, "switch", branch.branch)
        else:
            git(root, "switch", "-c", branch.branch, branch.baseline_commit)
    branch.validate(project, run_id)
    return branch


_BRANCH: ContextVar[GitBranch | None] = ContextVar("mars_research_git_branch", default=None)


def current_git_branch(project: str, run_id: str | None = None) -> GitBranch | None:
    branch = _BRANCH.get()
    if branch is not None:
        branch.validate(project, run_id)
    return branch


@contextmanager
def bind_git_branch(branch: GitBranch) -> Iterator[GitBranch]:
    branch.validate(branch.project, branch.run_id)
    if _BRANCH.get() is not None:
        raise GitWorkspaceError("不能在执行中重新绑定实验分支。")
    token = _BRANCH.set(branch)
    try:
        yield branch
    finally:
        _BRANCH.reset(token)
