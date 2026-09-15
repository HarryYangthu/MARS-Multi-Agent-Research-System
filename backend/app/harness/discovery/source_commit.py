"""Local source commits stored outside read-only workspaces; never contacts a remote."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
import subprocess


def archive_source_commit(*, source_root: Path, git_dir: Path, paths: Sequence[str],
                          environment: Mapping[str, str]) -> str:
    """Commit exact source files before execution; replay must resolve to the same tree."""
    if git_dir.is_symlink():
        raise ValueError("Source history must not be a symlink")
    git_dir.parent.mkdir(parents=True, exist_ok=True)

    def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(["git", "--git-dir", str(git_dir), "--work-tree", str(source_root), *args],
            cwd=source_root, env=dict(environment), capture_output=True, text=True, timeout=30)
        if check and result.returncode:
            raise ValueError("Local source commit failed: " + result.stderr.strip())
        return result

    if not git_dir.exists():
        initialized = subprocess.run(["git", "init", "--bare", "--quiet", str(git_dir)],
            cwd=source_root, env=dict(environment), capture_output=True, text=True, timeout=30)
        if initialized.returncode:
            raise ValueError("Cannot initialize local source history: " + initialized.stderr.strip())
    previous = git("rev-parse", "--verify", "HEAD", check=False)
    git("read-tree", "--empty")
    git("add", "--", *paths)
    tree = git("write-tree").stdout.strip()
    if previous.returncode == 0:
        if git("rev-parse", "HEAD^{tree}").stdout.strip() != tree:
            raise ValueError("Previously committed candidate source changed")
        return previous.stdout.strip()
    git("-c", "user.name=MARS CLI", "-c", "user.email=mars-cli@localhost",
        "commit", "--quiet", "-m", "Freeze research source before execution")
    return git("rev-parse", "HEAD").stdout.strip()
