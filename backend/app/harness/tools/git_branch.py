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


def changes_path(run_root: Path) -> Path:
    return run_root / "input" / "research_changes.json"


def _owner_path(root: Path, name: str) -> Path:
    return _git_root(root) / "mars-research-branches" / (hashlib.sha256(name.encode()).hexdigest() + ".json")


def branch_owner(root: Path) -> tuple[GitBranch, Path] | None:
    """A branch name alone never grants ownership of another task's files."""
    path = _owner_path(root, _branch(root))
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        run_root = Path(raw["run_root"])
        if not run_root.is_absolute() or run_root.is_symlink():
            raise ValueError("root")
        frozen = raw.get("binding")
        saved = GitBranch(**frozen) if isinstance(frozen, dict) else _load(root, run_root, raw["project"], raw["run_id"])
        if saved is None or saved.branch != _branch(root):
            raise ValueError("receipt")
        if (saved.repo_path != str(root.resolve()) or saved.branch != branch_name(saved.project, saved.run_id)
                or saved.project != raw["project"] or saved.run_id != raw["run_id"]):
            raise ValueError("identity")
        # A deleted/trashed run must not orphan its clean Git branch or turn
        # that candidate into the baseline for the next task.
        if receipt_path(run_root).exists() and _load(root, run_root, saved.project, saved.run_id) != saved:
            raise ValueError("receipt changed")
        saved.validate(saved.project, saved.run_id)
        return saved, run_root
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise GitWorkspaceError("现有编码分支的任务记录无法校验，请核对关联。",
                                "git_workspace_identity_changed") from exc


def changed_files(root: Path, base: str = "HEAD") -> set[str]:
    changed = set(filter(None, git(root, "diff", "--name-only", "--no-renames", "-z", base).split("\0")))
    changed.update(filter(None, git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0")))
    return changed


def _file_hash(root: Path, name: str) -> str | None:
    from app.harness.runtime.project_scope import forbidden_source_path, safe_scope_path
    if forbidden_source_path(name):
        raise GitWorkspaceError("编码提交不能包含 Git 控制文件、凭据或运行配置。")
    target = safe_scope_path(root, name)
    if not target.exists():
        return None
    if not target.is_file():
        raise GitWorkspaceError("编码提交只允许普通文件。")
    return hashlib.sha256(target.read_bytes()).hexdigest()


def _changes(branch: GitBranch, run_root: Path) -> dict[str, str | None]:
    path = changes_path(run_root)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if (raw.get("schema_id") != "research.git_changes.v1" or raw.get("project") != branch.project
                or raw.get("run_id") != branch.run_id or raw.get("repo_path") != branch.repo_path
                or raw.get("branch") != branch.branch or not isinstance(raw.get("files"), dict)):
            raise ValueError("identity")
        files = raw["files"]
        if any(not isinstance(name, str) or value is not None and
               (not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None)
               for name, value in files.items()):
            raise ValueError("hash")
        return dict(files)
    except (OSError, TypeError, ValueError, AttributeError) as exc:
        raise GitWorkspaceError("编码写入记录无法校验，未自动提交。", "git_workspace_identity_changed") from exc


def record_changed_files(project: str, run_id: str, names: list[str]) -> None:
    """Called by governed file mutations, while the host holds the repository lock."""
    branch = current_git_branch(project, run_id)
    if branch is None:
        return
    owner = branch_owner(branch.root)
    if owner is None or owner[0] != branch:
        raise GitWorkspaceError("编码分支缺少任务归属记录。", "git_workspace_identity_changed")
    files = _changes(branch, owner[1])
    files.update({name: _file_hash(branch.root, name) for name in names})
    atomic_json(changes_path(owner[1]), {"schema_id": "research.git_changes.v1",
        "project": project, "run_id": run_id, "repo_path": branch.repo_path, "branch": branch.branch, "files": files})


def checkpoint_changes(branch: GitBranch, run_root: Path, *, strict: bool = False) -> tuple[str | None, list[str], list[str]]:
    """Save verified writes locally. A commit is not approval to execute them."""
    branch.validate(branch.project, branch.run_id)
    pending = changed_files(branch.root)
    records = _changes(branch, run_root)
    verified = sorted(name for name in pending if name in records and _file_hash(branch.root, name) == records[name])
    remaining = sorted(pending - set(verified))
    if strict and remaining:
        raise GitWorkspaceError("代码与本任务写入记录不一致，请核对后再执行；未提交其他改动。",
                                "git_workspace_unreviewed_changes")
    if not verified:
        return None, [], remaining
    paths = [":(literal)" + name for name in verified]
    git(branch.root, "add", "--", *paths)
    git(branch.root, "-c", "user.name=MARS", "-c", "user.email=mars@localhost", "-c", "commit.gpgsign=false",
        "commit", "-m", f"Coding checkpoint: {branch.run_id}", "--", *paths)
    commit = git(branch.root, "rev-parse", "HEAD")
    atomic_json(run_root / "coding" / "git_checkpoints" / (commit + ".json"), {
        "schema_id": "research.git_checkpoint.v1", "project": branch.project, "run_id": branch.run_id,
        "repo_path": branch.repo_path, "branch": branch.branch, "commit": commit, "files": verified,
        "approved": False, "remaining": remaining,
    })
    return commit, verified, remaining


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
    owner = branch_owner(root)
    pending = changed_files(root)
    records = _changes(*owner) if owner is not None else {}
    if any(name not in records or _file_hash(root, name) != records[name] for name in pending):
        raise GitWorkspaceError("代码仓有非本任务记录的未提交改动，请先核对；已记录的编码改动会自动提交，不会混入其他文件。",
                                "git_workspace_dirty")
    if saved is not None:
        return saved
    # Never claim a pre-existing, unowned branch (including interrupted creation).
    branches = git(root, "for-each-ref", "--format=%(refname:short)", f"refs/heads/{expected}").splitlines()
    if expected in branches:
        raise GitWorkspaceError("同名实验分支已存在但缺少任务记录，请先核对。", "git_workspace_identity_changed")
    baseline_branch, baseline_commit = ((owner[0].baseline_branch, owner[0].baseline_commit) if owner is not None
                                       else (current, git(root, "rev-parse", "HEAD")))
    return GitBranch(project, run_id, str(root.resolve()), expected, baseline_branch, baseline_commit)


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
        previous = branch_owner(root)
        if previous is not None:
            checkpoint_changes(*previous)
        exists = branch.branch in git(root, "for-each-ref", "--format=%(refname:short)",
                                       f"refs/heads/{branch.branch}").splitlines()
        if exists:
            git(root, "switch", branch.branch)
        else:
            git(root, "switch", "-c", branch.branch, branch.baseline_commit)
    branch.validate(project, run_id)
    atomic_json(_owner_path(root, branch.branch), {"run_root": str(run_root.resolve()),
        "project": project, "run_id": run_id, "binding": asdict(branch)})
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
