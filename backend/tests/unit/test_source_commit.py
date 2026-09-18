"""Exercise actual local Git archives; no provider, Git or filesystem doubles."""
from __future__ import annotations

import os
from pathlib import Path
import shlex
import subprocess

import pytest

from app.harness.discovery.source_commit import archive_source_commit, source_commit_diff
from app.harness.tools.process_runtime import sanitized_subprocess_environment


def git(git_dir: Path, *args: str) -> str:
    result = subprocess.run(["git", "--git-dir", str(git_dir), *args],
                            check=True, text=True, capture_output=True)
    return result.stdout.strip()


def test_candidate_has_baseline_parent_and_reviewable_diff(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "libs").mkdir(parents=True)
    (source / "libs/baseline.py").write_text("VALUE = 1\n")
    env = sanitized_subprocess_environment()
    baseline_git = tmp_path / "baseline.git"
    baseline = archive_source_commit(source_root=source, git_dir=baseline_git,
                                    paths=["libs/baseline.py"], environment=env)
    (source / "libs/candidate.py").write_text("VALUE = 2\n")
    candidate_git = tmp_path / "candidate.git"
    def archive_candidate() -> str:
        return archive_source_commit(source_root=source, git_dir=candidate_git,
            paths=["libs/baseline.py", "libs/candidate.py"], environment=env,
            parent_git_dir=baseline_git, parent_commit=baseline)

    candidate = archive_candidate()
    assert git(candidate_git, "rev-parse", candidate + "^") == baseline
    assert archive_candidate() == candidate
    patch = source_commit_diff(git_dir=candidate_git, commit=candidate,
                               parent_commit=baseline, environment=env)
    assert "+VALUE = 2" in patch and "libs/candidate.py" in patch
    assert "libs/baseline.py" not in patch
    assert "remote" not in git(candidate_git, "config", "--list")
    with pytest.raises(ValueError, match="lineage"):
        source_commit_diff(git_dir=candidate_git, commit=candidate, environment=env)
    (source / "libs/candidate.py").write_text("VALUE = 3\n")
    with pytest.raises(ValueError, match="source changed"):
        archive_candidate()
    assert git(candidate_git, "rev-parse", "HEAD") == candidate


def test_standalone_archive_preserves_exact_bytes_and_replays(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "file.txt").write_bytes(b"exact\r\nbytes\r\n")
    env = sanitized_subprocess_environment()
    archive = tmp_path / "standalone.git"
    commit = archive_source_commit(source_root=source, git_dir=archive,
                                  paths=["file.txt"], environment=env)
    assert archive_source_commit(source_root=source, git_dir=archive,
                                 paths=["file.txt"], environment=env) == commit
    result = subprocess.run(["git", "--git-dir", str(archive), "show", commit + ":file.txt"],
                            check=True, capture_output=True)
    assert result.stdout == b"exact\r\nbytes\r\n"
    assert git(archive, "show", "-s", "--format=%P", commit) == ""
    assert "+exact" in source_commit_diff(git_dir=archive, commit=commit, environment=env)


def test_parent_change_is_rejected_even_when_tree_is_identical(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_text("first\n")
    env = sanitized_subprocess_environment()
    first_git, second_git, child_git = (tmp_path / name for name in ("first.git", "second.git", "child.git"))
    first = archive_source_commit(source_root=source, git_dir=first_git, paths=["a.txt"], environment=env)
    (source / "a.txt").write_text("second\n")
    second = archive_source_commit(source_root=source, git_dir=second_git, paths=["a.txt"], environment=env)
    archive_source_commit(source_root=source, git_dir=child_git, paths=["a.txt"], environment=env,
                          parent_git_dir=first_git, parent_commit=first)
    with pytest.raises(ValueError, match="lineage changed"):
        archive_source_commit(source_root=source, git_dir=child_git, paths=["a.txt"], environment=env,
                              parent_git_dir=second_git, parent_commit=second)


def test_git_filters_hooks_signing_and_environment_injection_do_not_run(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "file.txt").write_bytes(b"exact\r\nbytes\r\n")
    (source / ".gitattributes").write_text("*.txt filter=unsafe text eol=lf\n")
    marker = tmp_path / "executed"
    program = tmp_path / "unexpected-hook"
    program.write_text("#!/bin/sh\nprintf executed > " + shlex.quote(str(marker)) + "\nexit 1\n")
    program.chmod(0o755)
    archive = tmp_path / "archive.git"
    subprocess.run(["git", "init", "--bare", "--quiet", str(archive)], check=True)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "pre-commit").symlink_to(program)
    git(archive, "config", "core.hooksPath", str(hooks))
    git(archive, "config", "filter.unsafe.clean", shlex.quote(str(program)))
    git(archive, "config", "filter.unsafe.required", "true")
    git(archive, "config", "commit.gpgSign", "true")
    git(archive, "config", "gpg.program", str(program))
    injected_index = tmp_path / "injected-index"
    env = {**sanitized_subprocess_environment(), "GIT_INDEX_FILE": str(injected_index),
           "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": str(hooks)}
    commit = archive_source_commit(source_root=source, git_dir=archive,
                                  paths=["file.txt", ".gitattributes"], environment=env)
    assert not marker.exists() and not injected_index.exists()
    result = subprocess.run(["git", "--git-dir", str(archive), "show", commit + ":file.txt"],
                            check=True, capture_output=True, env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"})
    assert result.stdout == b"exact\r\nbytes\r\n"


@pytest.mark.parametrize("relative", ["../outside.py", "/tmp/outside.py", "./safe.py", "a/../safe.py"])
def test_archive_rejects_unsafe_paths(tmp_path: Path, relative: str) -> None:
    with pytest.raises(ValueError, match="safe relative"):
        archive_source_commit(source_root=tmp_path, git_dir=tmp_path / "archive.git",
                              paths=[relative], environment=sanitized_subprocess_environment())


def test_archive_rejects_symlinked_files(tmp_path: Path) -> None:
    (tmp_path / "original.py").write_text("VALUE = 1\n")
    (tmp_path / "alias.py").symlink_to(tmp_path / "original.py")
    with pytest.raises(ValueError, match="without symlinks"):
        archive_source_commit(source_root=tmp_path, git_dir=tmp_path / "archive.git",
                              paths=["alias.py"], environment=sanitized_subprocess_environment())
