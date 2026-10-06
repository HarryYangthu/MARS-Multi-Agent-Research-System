"""Real Git checkouts and actual code tools; no model or tool substitutes."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import subprocess

import pytest
import yaml

from app.bridge.research_branch import coding_workspace_blocker, research_branch_scope
from app.harness.project_workspace import open_folder
from app.harness.tools.code import apply_patch_tool, repo_reader_tool, rollback_patch_tool, write_file_tool
from app.harness.tools.git_branch import GitWorkspaceError, branch_name, current_git_branch, git, receipt_path
from app.harness.tools.project_repo import load_project_repo, resolve_allowed_path
from app.harness.tools.registry import ToolContext
from app.settings import reset_settings_cache
from app.storage.run_store import RunHandle


@pytest.fixture
def research(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[RunHandle, Path]:
    monkeypatch.setenv("MARS_FOLDER_PROJECTS_REGISTRY", str(tmp_path / "registry.json"))
    reset_settings_cache()
    project = open_folder(str(tmp_path / "project"), create=True)
    source = tmp_path / "code"; source.mkdir()
    git(source, "init", "-b", "baseline")
    (source / "main.py").write_text("VALUE = 1\n")
    (source / "baseline").mkdir()
    (source / "baseline/ref.py").write_text("REFERENCE = 1\n")
    git(source, "add", ".")
    git(source, "-c", "user.name=Test", "-c", "user.email=test@localhost", "-c", "commit.gpgsign=false",
        "commit", "-m", "Real baseline")
    (project.metadata_root / "repo_link.yaml").write_text(yaml.safe_dump({"repo_path": str(source),
        "repo_mode": "local_path", "read_only": True, "protected_paths": ["baseline/"],
        "allowed_paths": ["main.py", "baseline/", "configs/"], "ignore_patterns": [".git/", ".env*"]}))
    run_root = tmp_path / "run"; run_root.mkdir()
    run = RunHandle("research_1", run_root, project.name, "branch", "coding", "2026-10-06T00:00:00Z")
    try:
        yield run, source
    finally:
        reset_settings_cache()


def ctx(run: RunHandle) -> ToolContext:
    return ToolContext(run_id=run.run_id, project=run.project, agent="coding", extra={"run_root": str(run.root)})


@pytest.mark.asyncio
async def test_real_write_patch_rollback_compile_and_original_branch_preserved(research: tuple[RunHandle, Path]) -> None:
    run, source = research
    base = git(source, "rev-parse", "HEAD")
    assert coding_workspace_blocker(run) == ""
    assert git(source, "branch", "--show-current") == "baseline"
    assert not receipt_path(run.root).exists()
    assert not (await write_file_tool({"path": "main.py", "content": "VALUE = 2\n"}, ctx(run))).ok
    with research_branch_scope(run, "coding"):
        repo = load_project_repo(run.project)
        assert repo.root == source and not repo.read_only and repo.git_branch is not None
        written = await write_file_tool({"path": "main.py", "content": "VALUE = 2\n"}, ctx(run))
        assert written.ok and written.rollback_ref
        patch = "--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-VALUE = 2\n+VALUE = 3\n"
        assert (await apply_patch_tool({"diff": patch}, ctx(run))).ok
        result = subprocess.run(["python3", "-m", "py_compile", str(source / "main.py")], capture_output=True)
        assert result.returncode == 0
        restored = await rollback_patch_tool({"rollback_ref": written.rollback_ref}, ctx(run))
        assert restored.ok and (source / "main.py").read_text() == "VALUE = 1\n"
    assert current_git_branch(run.project) is None
    assert load_project_repo(run.project).read_only
    assert git(source, "rev-parse", "baseline") == base
    assert git(source, "show", "baseline:main.py") == "VALUE = 1"
    assert json.loads(receipt_path(run.root).read_text())["repo_path"] == str(source)
    assert not (run.root / "candidate").exists()


def test_dirty_initial_repo_is_not_stashed_or_switched(research: tuple[RunHandle, Path]) -> None:
    run, source = research
    (source / "main.py").write_text("USER_CHANGE = True\n")
    assert "未提交改动" in coding_workspace_blocker(run)
    with pytest.raises(GitWorkspaceError, match="未提交改动"):
        with research_branch_scope(run, "coding"):
            pytest.fail("should not enter")
    assert git(source, "branch", "--show-current") == "baseline"
    assert (source / "main.py").read_text() == "USER_CHANGE = True\n"
    assert not receipt_path(run.root).exists()
    assert not (run.root / "resources/model_budget.v1.json").exists()


def test_detached_head_requires_branch_selection(research: tuple[RunHandle, Path]) -> None:
    run, source = research
    git(source, "checkout", "--detach", "HEAD")
    assert "游离提交状态" in coding_workspace_blocker(run)
    assert not receipt_path(run.root).exists()


@pytest.mark.asyncio
async def test_retry_retains_partial_changes_on_same_branch(research: tuple[RunHandle, Path]) -> None:
    run, source = research
    with pytest.raises(RuntimeError, match="interrupted"):
        with research_branch_scope(run, "coding"):
            assert (await write_file_tool({"path": "main.py", "content": "VALUE = 9\n"}, ctx(run))).ok
            raise RuntimeError("interrupted")
    original_receipt = receipt_path(run.root).read_bytes()
    assert coding_workspace_blocker(run) == ""
    with research_branch_scope(run, "coding_attempt_2"):
        assert (source / "main.py").read_text() == "VALUE = 9\n"
    assert receipt_path(run.root).read_bytes() == original_receipt
    assert git(source, "branch", "--show-current") == branch_name(run.project, run.run_id)


def test_repository_lock_blocks_concurrent_tasks(research: tuple[RunHandle, Path]) -> None:
    run, source = research
    other = replace(run, run_id="research_2", root=run.root.parent / "run2")
    other.root.mkdir()
    # Enter before binding the context, representing another host process/task.
    from app.harness.tools.git_branch import repository_lock
    with repository_lock(source):
        assert "已有研究阶段" in coding_workspace_blocker(other)
        with pytest.raises(GitWorkspaceError, match="已有研究阶段"):
            with research_branch_scope(other, "coding"):
                pytest.fail("should not enter")
    assert not receipt_path(other.root).exists()


@pytest.mark.asyncio
async def test_switching_branch_revokes_read_write_grant(research: tuple[RunHandle, Path]) -> None:
    run, source = research
    with research_branch_scope(run, "coding"):
        repo = load_project_repo(run.project)
        git(source, "switch", "baseline")
        assert not (await write_file_tool({"path": "main.py", "content": "VALUE = 9\n"}, ctx(run))).ok
        assert not (await repo_reader_tool({"path": "main.py"}, ctx(run))).ok
        with pytest.raises(GitWorkspaceError, match="其他分支"):
            resolve_allowed_path(repo, "main.py", for_write=True)
    assert git(source, "show", "baseline:main.py") == "VALUE = 1"


@pytest.mark.asyncio
async def test_protection_allowlist_control_files_and_symlinks(research: tuple[RunHandle, Path]) -> None:
    run, source = research
    outside = source.parent / "private.py"; outside.write_text("PRIVATE = True\n")
    with research_branch_scope(run, "coding"):
        for name in ["baseline/ref.py", ".git/config", ".env.local", "unknown.py", "../private.py"]:
            assert not (await write_file_tool({"path": name, "content": "OVERWRITE\n"}, ctx(run))).ok
        (source / "configs").mkdir()
        (source / "configs/link.py").symlink_to(outside)
        assert not (await write_file_tool({"path": "configs/link.py", "content": "OVERWRITE\n"}, ctx(run))).ok
        patch = "diff --git a/configs/link b/configs/link\nnew file mode 120000\n--- /dev/null\n+++ b/configs/link\n@@ -0,0 +1 @@\n+../private.py\n"
        assert not (await apply_patch_tool({"diff": patch}, ctx(run))).ok
        wrong = ctx(run); wrong.run_id = "another_run"
        assert not (await write_file_tool({"path": "main.py", "content": "NO\n"}, wrong)).ok
        external = source.parent / "external_rollback.json"
        external.write_text(json.dumps({"schema": "tool_rollback.v1", "run_id": run.run_id,
            "project": run.project, "snapshots": [{"path": "main.py", "existed": True, "content": "NO\n"}]}))
        assert not (await rollback_patch_tool({"rollback_ref": str(external)}, ctx(run))).ok
        assert not (await apply_patch_tool({"patch_path": str(external)}, ctx(run))).ok
    assert outside.read_text() == "PRIVATE = True\n"
    assert (source / "baseline/ref.py").read_text() == "REFERENCE = 1\n"


def test_existing_record_switches_back_without_creating_copy(research: tuple[RunHandle, Path]) -> None:
    run, source = research
    with research_branch_scope(run, "coding"):
        pass
    git(source, "switch", "baseline")
    assert coding_workspace_blocker(run) == ""
    assert git(source, "branch", "--show-current") == "baseline"  # GET/preflight is read-only.
    with research_branch_scope(run, "coding_attempt_2"):
        assert git(source, "branch", "--show-current") == branch_name(run.project, run.run_id)


def test_identity_or_baseline_change_stops_recovery(research: tuple[RunHandle, Path]) -> None:
    run, source = research
    with research_branch_scope(run, "coding"):
        pass
    git(source, "switch", "baseline")
    (source / "main.py").write_text("VALUE = 2\n")
    git(source, "add", "main.py")
    git(source, "-c", "user.name=Test", "-c", "user.email=test@localhost", "-c", "commit.gpgsign=false",
        "commit", "-m", "Baseline updated by user")
    assert "基线分支已有新提交" in coding_workspace_blocker(run)
    data = json.loads(receipt_path(run.root).read_text()); data["run_id"] = "wrong"
    receipt_path(run.root).write_text(json.dumps(data))
    assert "无法校验" in coding_workspace_blocker(run)


@pytest.mark.asyncio
async def test_execution_commits_only_approved_changes_on_run_branch(research: tuple[RunHandle, Path]) -> None:
    run, source = research
    base = git(source, "rev-parse", "HEAD")
    with research_branch_scope(run, "coding"):
        assert (await write_file_tool({"path": "main.py", "content": "VALUE = 2\n"}, ctx(run))).ok
    with pytest.raises(GitWorkspaceError, match="尚未审核"):
        with research_branch_scope(run, "execution"):
            pytest.fail("should not enter")
    (run.root / "coding/code_spec.approved.md").write_text("---\nfiles_changed:\n- path: main.py\n  type: modified\n---\nApproved by test author, no model invoked.\n")
    with research_branch_scope(run, "execution"):
        assert git(source, "status", "--porcelain") == ""
        assert git(source, "rev-parse", "HEAD") != base
    assert git(source, "rev-parse", "baseline") == base
    assert git(source, "show", "baseline:main.py") == "VALUE = 1"


@pytest.mark.asyncio
async def test_unreviewed_changes_are_not_committed(research: tuple[RunHandle, Path]) -> None:
    run, source = research
    with research_branch_scope(run, "coding"):
        assert (await write_file_tool({"path": "main.py", "content": "VALUE = 2\n"}, ctx(run))).ok
    (run.root / "coding/code_spec.approved.md").write_text("---\nfiles_changed: []\n---\nNo approved changes.\n")
    before = git(source, "rev-parse", "HEAD")
    with pytest.raises(GitWorkspaceError, match="未纳入"):
        with research_branch_scope(run, "execution"):
            pytest.fail("should not enter")
    assert git(source, "rev-parse", "HEAD") == before
