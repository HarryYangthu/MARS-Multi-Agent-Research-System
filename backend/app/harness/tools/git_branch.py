"""Persistent per-run Git checkouts; the configured baseline never switches.

Local clones contain committed code and independent Git objects/indexes. No
stash, reset, network pull, automatic merge, or data directory copy is used.
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
import uuid

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
    source_repo_path: str = ""
    initial_commit: str = ""

    @property
    def root(self) -> Path:
        return Path(self.repo_path)

    @property
    def source_root(self) -> Path:
        return Path(self.source_repo_path) if self.source_repo_path else self.root

    def validate(self, project: str, run_id: str | None = None) -> None:
        if self.project != project or run_id is not None and self.run_id != run_id:
            raise GitWorkspaceError("实验分支不属于当前任务。", "git_workspace_identity_changed")
        if _branch(self.root) != self.branch:
            raise GitWorkspaceError("代码仓已切换到其他分支，当前操作已停止；请恢复本任务分支。",
                                    "git_workspace_branch_changed")
        if not self.source_repo_path:
            # Legacy tasks still share their source checkout. Advancing its
            # mutable baseline ref must not broaden their original write grant.
            if git(self.source_root, "rev-parse", "--verify", f"refs/heads/{self.baseline_branch}") != self.baseline_commit:
                raise GitWorkspaceError("原基线分支已有新提交，请核对后重新开展研究；不会覆盖基线。",
                                        "git_workspace_baseline_changed")
            git(self.root, "merge-base", "--is-ancestor", self.baseline_commit, "HEAD")
            return
        # An isolated clone owns an immutable starting commit, not today's tip
        # of the source branch. Other studies may legitimately advance main.
        for commit in {self.baseline_commit, self.initial_commit}:
            if re.fullmatch(r"[0-9a-f]{40,64}", commit) is None:
                raise GitWorkspaceError("保存的研究起点提交无法校验，未绑定代码。", "git_workspace_history_changed")
            try:
                git(self.root, "cat-file", "-e", commit + "^{commit}")
                git(self.root, "merge-base", "--is-ancestor", commit, "HEAD")
            except GitWorkspaceError as exc:
                raise GitWorkspaceError("保存的基线或研究起点不在本任务代码历史中，未绑定代码。",
                                        "git_workspace_history_changed") from exc


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
        if receipt_path(run_root).exists():
            receipt = _load(saved.source_root, run_root, saved.project, saved.run_id)
            migrated = (receipt is not None and receipt.source_repo_path and receipt.source_root == root
                        and raw.get("migrated_to") == receipt.repo_path
                        and receipt.branch == saved.branch
                        and receipt.baseline_commit == saved.baseline_commit)
            intent_path = run_root / "input/research_workspace_intent.json"
            initializing = (receipt is not None and not receipt.source_repo_path and saved.source_repo_path
                            and receipt.branch == saved.branch and receipt.baseline_commit == saved.baseline_commit
                            and intent_path.is_file()
                            and json.loads(intent_path.read_text()) == {"schema_id": "research.git_workspace_intent.v1", **asdict(saved)})
            if receipt != saved and not migrated and not initializing:
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


def _exclude_python_caches(root: Path) -> None:
    """Keep generated bytecode out of isolated research delivery manifests.

    Use Git's local excludes so every clean-worktree check agrees. This never
    changes the source repository or its tracked ignore rules, and Git still
    reports tracked/staged bytecode and source files inside cache directories.
    """
    control = _git_root(root).resolve()
    path = control / "info" / "exclude"
    if (path.is_symlink() or path.parent.is_symlink()
            or not path.resolve().is_relative_to(control)):
        raise GitWorkspaceError("实验代码目录的 Git 排除配置路径异常，未修改。")
    path.parent.mkdir(parents=True, exist_ok=True)
    content = path.read_bytes() if path.exists() else b""
    pattern = b"**/__pycache__/*.pyc"
    if pattern not in content.splitlines():
        with path.open("ab") as stream:
            if content and not content.endswith(b"\n"):
                stream.write(b"\n")
            stream.write(b"# MARS: generated Python bytecode (untracked files only)\n" + pattern + b"\n")


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
        if not isinstance(raw, dict) or raw.pop("schema_id", None) not in {"research.git_branch.v1", "research.git_branch.v2"}:
            raise ValueError("schema")
        branch = GitBranch(**raw)
        if (branch.project != project or branch.run_id != run_id
                or branch.source_root != root.resolve()
                or branch.branch != branch_name(project, run_id)
                or not re.fullmatch(r"[0-9a-f]{40,64}", branch.baseline_commit)):
            raise ValueError("identity")
        if branch.source_repo_path and (branch.root != workspace_path(root, project, run_id)
                or not re.fullmatch(r"[0-9a-f]{40,64}", branch.initial_commit)):
            raise ValueError("workspace")
        git(root, "check-ref-format", f"refs/heads/{branch.baseline_branch}")
        return branch
    except (OSError, TypeError, ValueError) as exc:
        raise GitWorkspaceError("实验分支记录无法校验，请核对任务与代码仓关联。",
                                "git_workspace_identity_changed") from exc


def _inspect(root: Path, run_root: Path, project: str, run_id: str) -> GitBranch:
    _git_root(root)
    saved = _load(root, run_root, project, run_id)
    if saved is not None:
        if not saved.source_repo_path and git(root, "rev-parse", "--verify", f"refs/heads/{saved.baseline_branch}") != saved.baseline_commit:
            raise GitWorkspaceError("原基线分支已有新提交，请核对研究基线。", "git_workspace_baseline_changed")
        if saved.source_repo_path:
            if not saved.root.exists():
                raise GitWorkspaceError("本研究的代码目录缺失，请恢复保存的副本；不会用基线覆盖研究结果。", "git_workspace_missing")
            saved.validate(project, run_id)
        return saved
    current = _branch(root)
    expected = branch_name(project, run_id)
    owner = branch_owner(root)
    pending = changed_files(root)
    if owner is None and pending:
        raise GitWorkspaceError("代码仓有非本任务记录的未提交改动，请先核对；已记录的编码改动会自动提交，不会混入其他文件。",
                                "git_workspace_dirty")
    # Never claim a pre-existing, unowned branch (including interrupted creation).
    branches = git(root, "for-each-ref", "--format=%(refname:short)", f"refs/heads/{expected}").splitlines()
    if expected in branches:
        raise GitWorkspaceError("同名实验分支已存在但缺少任务记录，请先核对。", "git_workspace_identity_changed")
    baseline_branch, baseline_commit = ((owner[0].baseline_branch, owner[0].baseline_commit) if owner is not None
                                       else (current, git(root, "rev-parse", "HEAD")))
    return GitBranch(project, run_id, str(workspace_path(root, project, run_id)), expected,
                     baseline_branch, baseline_commit, str(root.resolve()), baseline_commit)


def workspace_path(root: Path, project: str, run_id: str) -> Path:
    from app.harness.project_storage import project_storage_root
    directory = project_storage_root(project, "code", root.parent)
    suffix = hashlib.sha256(f"{project}\0{run_id}".encode()).hexdigest()[:12]
    return directory.resolve() / (root.name[:48] + "-research-" + suffix)


@contextmanager
def repository_lock(root: Path) -> Iterator[None]:
    lock = FileLock(_git_root(root) / "mars-research.lock", timeout=0)
    try:
        lock.acquire()
    except Timeout as exc:
        raise GitWorkspaceError("此研究的代码工作目录正在使用，请等待当前阶段结束；其他研究可独立运行。",
                                "git_workspace_busy") from exc
    try:
        yield
    finally:
        lock.release()


def preflight(root: Path, run_root: Path, project: str, run_id: str) -> str:
    """Inspect only; recovery polling never creates or switches branches."""
    try:
        branch = _inspect(root, run_root, project, run_id)
        if branch.source_repo_path and branch.root.exists():
            with repository_lock(branch.root):
                branch.validate(project, run_id)
    except (OSError, ValueError) as exc:
        return str(exc)
    return ""


def prepare(root: Path, run_root: Path, project: str, run_id: str) -> GitBranch:
    """Brief per-run preparation lock, followed by a lock on its own checkout."""
    run_root.joinpath("input").mkdir(parents=True, exist_ok=True)
    lock = FileLock(run_root / "input/.research-workspace.lock", timeout=0)
    try:
        with lock:
            return _prepare(root, run_root, project, run_id)
    except Timeout as exc:
        raise GitWorkspaceError("此研究的代码目录正在初始化，请稍后重试。", "git_workspace_busy") from exc


def _prepare(root: Path, run_root: Path, project: str, run_id: str) -> GitBranch:
    branch = _inspect(root, run_root, project, run_id)
    path = receipt_path(run_root)
    legacy = not branch.source_repo_path
    if legacy:
        # Preserve old tasks' exact branch/head. Never move another task's
        # checkout, nor lose governed partial writes on this legacy branch.
        with repository_lock(root):
            ledger = changes_path(run_root)
            migrated_ledger = (ledger.exists() and json.loads(ledger.read_text()).get("repo_path")
                               == str(workspace_path(root, project, run_id)))
            if _branch(root) == branch.branch and not migrated_ledger:
                checkpoint_changes(branch, run_root, strict=True)
            seed = git(root, "rev-parse", "--verify", f"refs/heads/{branch.branch}")
        branch = GitBranch(project, run_id, str(workspace_path(root, project, run_id)),
            branch.branch, branch.baseline_branch, branch.baseline_commit, str(root.resolve()), seed)
    intent_path = run_root / "input/research_workspace_intent.json"
    intent = {"schema_id": "research.git_workspace_intent.v1", **asdict(branch)}
    if intent_path.exists() and json.loads(intent_path.read_text()) != intent:
        # A completed workspace may have advanced beyond its initial commit;
        # its persisted branch still keeps the original initialization intent.
        raise GitWorkspaceError("代码目录初始化记录不匹配，未覆盖任何目录。", "git_workspace_identity_changed")
    atomic_json(intent_path, intent)
    if branch.root.exists():
        owner = branch_owner(branch.root)
        if owner is None or owner[0] != branch:
            raise GitWorkspaceError("研究代码目录已存在但归属不匹配，未覆盖。", "git_workspace_identity_changed")
    else:
        branch.root.parent.mkdir(parents=True, exist_ok=True)
        staging = branch.root.with_name(branch.root.name + ".creating-" + uuid.uuid4().hex)
        # --no-hardlinks makes the clone independently recoverable and avoids
        # cross-checkout object writes. Only Git-tracked code is checked out.
        # Governed write receipts hash exact bytes. Do not let the host's
        # autocrlf setting rewrite those bytes when isolating a saved task.
        git(root, "clone", "--local", "--no-hardlinks", "--no-checkout",
            "--config", "core.autocrlf=false", "--", str(root.resolve()), str(staging))
        exists = branch.branch in git(staging, "for-each-ref", "--format=%(refname:short)",
                                      f"refs/heads/{branch.branch}").splitlines()
        if exists:
            if git(staging, "rev-parse", "HEAD") != branch.initial_commit:
                raise GitWorkspaceError("初始化分支版本已变化，副本保留待核对。", "git_workspace_identity_changed")
            git(staging, "switch", branch.branch)
        else:
            git(staging, "switch", "-c", branch.branch, branch.initial_commit)
        atomic_json(_owner_path(staging, branch.branch), {"run_root": str(run_root.resolve()),
            "project": project, "run_id": run_id, "binding": asdict(branch)})
        staging.rename(branch.root)
    _exclude_python_caches(branch.root)
    branch.validate(project, run_id)
    if legacy:
        backup = path.with_name("research_branch.pre_isolation.json")
        if not backup.exists():
            backup.write_bytes(path.read_bytes())
        ledger = changes_path(run_root)
        if ledger.exists():
            raw = json.loads(ledger.read_text())
            if (raw.get("repo_path") not in {str(root.resolve()), branch.repo_path}
                    or raw.get("branch") != branch.branch or raw.get("project") != project or raw.get("run_id") != run_id):
                raise GitWorkspaceError("旧编码写入记录身份不匹配，未迁移。", "git_workspace_identity_changed")
            for name, value in raw["files"].items():
                if _file_hash(branch.root, name) != value:
                    raise GitWorkspaceError("旧编码与保存的分支不同，未迁移写入记录。", "git_workspace_identity_changed")
            if not ledger.with_name("research_changes.pre_isolation.json").exists():
                ledger.with_name("research_changes.pre_isolation.json").write_bytes(ledger.read_bytes())
            atomic_json(ledger, {**raw, "repo_path": branch.repo_path})
        old_owner = _owner_path(root, branch.branch)
        if old_owner.exists():
            raw_owner = json.loads(old_owner.read_text())
            atomic_json(old_owner, {**raw_owner, "migrated_to": branch.repo_path})
    atomic_json(path, {"schema_id": "research.git_branch.v2", **asdict(branch)})
    return branch


def saved_branch(root: Path, run_root: Path, project: str, run_id: str) -> GitBranch | None:
    """Read-only binding for artifact browsing; never create or switch code."""
    branch = _load(root, run_root, project, run_id)
    if branch is not None and branch.source_repo_path:
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
