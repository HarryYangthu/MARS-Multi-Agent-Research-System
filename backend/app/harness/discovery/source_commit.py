"""Local source commits stored outside read-only workspaces; never contacts a remote."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import os
from pathlib import Path, PurePosixPath
import re
import subprocess


def _git_environment(environment: Mapping[str, str]) -> dict[str, str]:
    # Repository snapshots must not inherit config injection, signing, filters or hooks.
    result = {key: value for key, value in environment.items() if not key.upper().startswith("GIT_")}
    result.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                   "GIT_ATTR_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"})
    return result


def _git(git_dir: Path, environment: Mapping[str, str], *args: str,
         check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["git", "--git-dir", str(git_dir),
        "-c", f"core.hooksPath={os.devnull}", "-c", "commit.gpgSign=false",
        "-c", "gc.auto=0", "-c", "protocol.allow=never", "-c", "protocol.file.allow=always", *args],
        cwd=git_dir.parent, env=_git_environment(environment), capture_output=True, text=True, timeout=30)
    if check and result.returncode:
        raise ValueError("Local source history failed: " + result.stderr.strip())
    return result


def _commit_id(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value):
        raise ValueError("Source commit must be a full hexadecimal object ID")
    return value


def archive_source_commit(*, source_root: Path, git_dir: Path, paths: Sequence[str],
                          environment: Mapping[str, str], parent_git_dir: Path | None = None,
                          parent_commit: str | None = None) -> str:
    """Commit exact bytes, optionally descended from a separately archived baseline.

    Omitting both parent arguments preserves standalone root-commit behavior.
    A parent is imported only from the supplied local Git archive, never a remote.
    """
    if git_dir.is_symlink():
        raise ValueError("Source history must not be a symlink")
    if source_root.is_symlink() or not source_root.is_dir():
        raise ValueError("Source root must be a real directory")
    if (parent_git_dir is None) != (parent_commit is None):
        raise ValueError("Both parent_git_dir and parent_commit are required for source lineage")
    if parent_git_dir is not None:
        if parent_git_dir.is_symlink() or not parent_git_dir.is_dir():
            raise ValueError("Parent source history must be a local non-symlink directory")
        parent_git_dir = parent_git_dir.resolve()
    if parent_commit is not None:
        _commit_id(parent_commit)
    source_root, git_dir = source_root.resolve(), git_dir.resolve()
    files: list[tuple[str, Path]] = []
    for relative in sorted(set(paths)):
        pure = PurePosixPath(relative)
        if (pure.is_absolute() or not relative or "\x00" in relative or "\\" in relative
                or any(part in {".", "..", ".git"} for part in pure.parts)
                or pure.as_posix() != relative):
            raise ValueError("Source history paths must be safe relative file paths")
        path = source_root / relative
        if (not path.is_file() or any((source_root / parent).is_symlink()
                                    for parent in (pure, *pure.parents))):
            raise ValueError("Source history accepts only regular files without symlinks")
        files.append((relative, path))
    if not files:
        raise ValueError("Source history requires at least one file")
    git_dir.parent.mkdir(parents=True, exist_ok=True)

    def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return _git(git_dir, environment, *args, check=check)

    if not git_dir.exists():
        initialized = subprocess.run(["git", "init", "--bare", "--quiet", str(git_dir)],
            cwd=source_root, env=_git_environment(environment), capture_output=True, text=True, timeout=30)
        if initialized.returncode:
            raise ValueError("Cannot initialize local source history: " + initialized.stderr.strip())
    if parent_git_dir is not None and parent_commit is not None:
        resolved = _git(parent_git_dir, environment, "rev-parse", "--verify", parent_commit + "^{commit}").stdout.strip()
        if resolved != parent_commit:
            raise ValueError("Parent source object must be a commit")
        git("fetch", "--quiet", "--no-tags", "--no-write-fetch-head", "--no-auto-gc",
            str(parent_git_dir), parent_commit)
    previous = git("rev-parse", "--verify", "HEAD", check=False)
    git("read-tree", "--empty")
    for relative, path in files:
        # Plumbing bypasses .gitattributes, line-ending conversion and clean filters.
        blob = git("hash-object", "-w", "--no-filters", "--", str(path)).stdout.strip()
        mode = "100755" if path.stat().st_mode & 0o111 else "100644"
        git("update-index", "--add", "--cacheinfo", mode, blob, relative)
    tree = git("write-tree").stdout.strip()
    if previous.returncode == 0:
        if git("rev-parse", "HEAD^{tree}").stdout.strip() != tree:
            raise ValueError("Previously committed candidate source changed")
        parents = git("show", "-s", "--format=%P", "HEAD").stdout.strip().split()
        if parents != ([parent_commit] if parent_commit else []):
            raise ValueError("Previously committed source lineage changed")
        return previous.stdout.strip()
    commit = git("-c", "user.name=MARS CLI", "-c", "user.email=mars-cli@localhost",
        "commit-tree", tree, *(["-p", parent_commit] if parent_commit else []),
        "-m", "Freeze research source before execution").stdout.strip()
    git("update-ref", "HEAD", commit)
    return git("rev-parse", "HEAD").stdout.strip()


def source_commit_diff(*, git_dir: Path, commit: str, environment: Mapping[str, str],
                       parent_commit: str | None = None) -> str:
    """Return an inspectable patch with exact recorded parentage, without external tools."""
    _commit_id(commit)
    if git_dir.is_symlink() or not git_dir.is_dir():
        raise ValueError("Source history must be a local non-symlink directory")
    git_dir = git_dir.resolve()
    parents = _git(git_dir, environment, "show", "-s", "--format=%P", commit).stdout.strip().split()
    if parent_commit is not None:
        _commit_id(parent_commit)
    if parents != ([parent_commit] if parent_commit else []):
        raise ValueError("Source diff parent does not match committed lineage")
    return _git(git_dir, environment, "diff-tree", "--root", "--no-commit-id", "--binary",
                "--no-ext-diff", "--no-textconv", "-r", "-p", commit).stdout
