"""Portable path policy and real Git execution; no substitute Git or model."""
from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest
import yaml

from app.harness.discovery.source_commit import archive_source_commit, source_commit_diff
from app.harness.agent_loop.zcode.config import runtime_environment
from app.harness.runtime.git_runtime import (
    GitRuntimeError, candidate_paths, git_child_environment, git_environment,
    git_failure, resolve_git, run_git,
)
from app.harness.runtime.readiness import assert_ready_for_run, check_git_readiness
from app.harness.tools.code import _git_apply
from app.harness.tools.git_branch import GitWorkspaceError, git, prepare, preflight, receipt_path, repository_lock


def test_windows_paths_preserve_spaces_and_do_not_search_current_directory() -> None:
    paths = candidate_paths(platform="win32", environment={
        "PATH": ';relative;"C:\\Program Files\\Git\\cmd";C:\\Windows\\System32;c:\\program files\\git\\cmd',
        "ProgramFiles": r"C:\Program Files", "LOCALAPPDATA": r"C:\Users\研究 User\AppData\Local",
    }, fallback_paths=["{ProgramFiles}/Git/cmd/git.exe", "{LOCALAPPDATA}/Programs/Git/cmd/git.exe",
                       "{MISSING}/git.exe"])
    assert paths == (r"C:\Program Files\Git\cmd\git.exe", r"C:\Windows\System32\git.exe",
                     r"C:\Users\研究 User\AppData\Local\Programs\Git\cmd\git.exe")


def test_macos_paths_deduplicate_and_skip_relative_directories() -> None:
    assert candidate_paths(platform="darwin", environment={"PATH": ":.:relative:/usr/bin:/usr/bin"},
        fallback_paths=["/opt/homebrew/bin/git", "/usr/local/bin/git"]) == (
            "/usr/bin/git", "/opt/homebrew/bin/git", "/usr/local/bin/git")


def test_explicit_missing_path_never_falls_back_to_installed_git(tmp_path: Path) -> None:
    environment = {**os.environ, "MARS_GIT_EXECUTABLE": str(tmp_path / "missing git.exe")}
    with pytest.raises(GitRuntimeError) as caught:
        resolve_git(environment=environment)
    assert caught.value.reason["code"] == "git_not_found"


def test_relative_executable_is_rejected() -> None:
    with pytest.raises(GitRuntimeError, match="完整路径"):
        resolve_git(environment={**os.environ, "MARS_GIT_EXECUTABLE": "git"})


def test_discovery_skips_unusable_file_and_uses_real_git(tmp_path: Path) -> None:
    real_git = resolve_git().executable
    wrong = tmp_path / "unusable"; wrong.mkdir()
    (wrong / ("git.exe" if sys.platform == "win32" else "git")).write_bytes(b"not an executable")
    config = tmp_path / "git.yaml"
    config.write_text(yaml.safe_dump({"probe_timeout_seconds": 5, "command_timeout_seconds": 15,
        "cache_seconds": 30, "fallback_paths": {sys.platform: [real_git]}}))
    environment = {**os.environ, "MARS_GIT_EXECUTABLE": "", "PATH": str(wrong) + os.pathsep + os.environ.get("PATH", "")}
    selected = resolve_git(environment=environment, config_path=config)
    # A valid earlier PATH candidate is also acceptable. Selection always ran Git.
    assert Path(selected.executable).parent != wrong
    assert selected.source == "discovered" and selected.version.startswith("git version ")


@pytest.mark.parametrize(("message", "code"), [
    ("You have not agreed to the Xcode license agreements", "git_license_required"),
    ("Permission denied", "git_permission_denied"),
    ("fatal: detected dubious ownership in repository", "git_unsafe_repository"),
    ("Unable to create index.lock: File exists", "git_repository_locked"),
    ("not a git repository", "git_repository_missing"),
    ("git: switch is not a git command", "git_unsupported"),
    ("hook output containing PRIVATE_DATA", "git_operation_failed"),
])
def test_failure_classification_retains_reason_without_arbitrary_output(message: str, code: str) -> None:
    error = git_failure(message, returncode=69)
    assert error.reason["code"] == code
    assert "PRIVATE_DATA" not in str(error)


def test_missing_git_blocks_admission_before_task_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARS_RUNTIME_MODE", "development")
    monkeypatch.setenv("MARS_GIT_EXECUTABLE", str(tmp_path / "missing.exe"))
    from app.settings import reset_settings_cache
    reset_settings_cache()
    try:
        check = check_git_readiness()
        assert not check.ready and check.severity == "blocker" and check.details["code"] == "git_not_found"
        with pytest.raises(GitRuntimeError):
            assert_ready_for_run(project="pimc")
        with pytest.raises(GitWorkspaceError) as caught:
            git(tmp_path, "status")
        assert caught.value.reason["code"] == "git_not_found"
    finally:
        reset_settings_cache()


def test_real_branch_uses_configured_git_with_spaces_and_preserves_baseline(tmp_path: Path) -> None:
    root = tmp_path / "代码 repo with spaces"; root.mkdir()
    run_root = tmp_path / "run"; run_root.mkdir()
    git(root, "init", "-b", "baseline")
    (root / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "-c", "user.name=Git Test", "-c", "user.email=git-test@localhost",
        "-c", "commit.gpgsign=false", "commit", "-m", "Baseline")
    base = git(root, "rev-parse", "HEAD")
    assert preflight(root, run_root, "project", "run") == ""
    assert git(root, "branch", "--show-current") == "baseline" and not receipt_path(run_root).exists()
    with repository_lock(root):
        branch = prepare(root, run_root, "project", "run")
        branch.validate("project", "run")
    assert git(root, "rev-parse", "baseline") == base
    assert git(root, "branch", "--show-current") == branch.branch
    assert not (run_root / "candidate").exists()


@pytest.mark.asyncio
async def test_patch_ignores_ambient_git_repository_routing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "patch with spaces"; root.mkdir()
    (root / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "wrong-repo"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "wrong-worktree"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.worktree")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(tmp_path / "wrong-worktree"))
    diff = "--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n"
    assert (await _git_apply(root, diff, check_only=True, isolated=True)).ok
    assert (root / "main.py").read_text() == "VALUE = 1\n"
    assert (await _git_apply(root, diff, check_only=False, isolated=True)).ok
    assert (root / "main.py").read_text() == "VALUE = 2\n"
    assert not (tmp_path / "wrong-worktree").exists()


def test_real_source_history_and_child_git_share_runtime(tmp_path: Path) -> None:
    root = tmp_path / "source"; root.mkdir()
    (root / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    archive = tmp_path / "history.git"
    environment = git_environment()
    commit = archive_source_commit(source_root=root, git_dir=archive, paths=["main.py"], environment=environment)
    patch = source_commit_diff(git_dir=archive, commit=commit, environment=environment)
    assert "+VALUE = 1" in patch
    assert run_git(["--git-dir", str(archive), "show", commit + ":main.py"]).stdout == "VALUE = 1\n"
    selected = resolve_git()
    assert git_child_environment()["PATH"].split(os.pathsep)[0] == str(Path(selected.executable).parent)


def test_zcode_environment_uses_verified_git_without_ambient_routing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "wrong.git"))
    environment = runtime_environment(tmp_path / "runtime", provider={"configuration": "local file"})
    assert "GIT_DIR" not in environment
    assert environment["PATH"].split(os.pathsep)[0] == str(Path(resolve_git().executable).parent)
