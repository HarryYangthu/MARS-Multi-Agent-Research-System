"""Real Git checkouts and actual code tools; no model or tool substitutes."""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from app.bridge.research_branch import coding_workspace_blocker, research_branch_scope
from app.harness.agent_loop.executor import permits_fresh_read
from app.harness.project_workspace import open_folder
from app.harness.tools.code import apply_patch_tool, repo_reader_tool, rollback_patch_tool, write_file_tool
from app.harness.tools.git_branch import GitWorkspaceError, branch_name, current_git_branch, git, receipt_path
from app.harness.tools.project_repo import load_project_repo, resolve_allowed_path
from app.harness.tools.registry import ToolContext, ToolPolicy, get_registry
from app.settings import reset_settings_cache
from app.storage.run_store import RunHandle


@pytest.fixture
def research(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[RunHandle, Path]]:
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
        result = subprocess.run([sys.executable, "-m", "py_compile", str(source / "main.py")], capture_output=True)
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
    baseline = git(source, "rev-parse", "baseline")
    with pytest.raises(RuntimeError, match="interrupted"):
        with research_branch_scope(run, "coding"):
            assert (await write_file_tool({"path": "main.py", "content": "VALUE = 9\n"}, ctx(run))).ok
            raise RuntimeError("interrupted")
    original_receipt = receipt_path(run.root).read_bytes()
    commit = git(source, "rev-parse", "HEAD")
    assert commit != baseline
    assert git(source, "status", "--porcelain") == ""
    assert coding_workspace_blocker(run) == ""
    with research_branch_scope(run, "coding_attempt_2"):
        assert (source / "main.py").read_text() == "VALUE = 9\n"
    assert receipt_path(run.root).read_bytes() == original_receipt
    assert git(source, "rev-parse", "HEAD") == commit
    assert git(source, "branch", "--show-current") == branch_name(run.project, run.run_id)


@pytest.mark.asyncio
async def test_repeated_local_read_observes_the_written_file(research: tuple[RunHandle, Path]) -> None:
    run, source = research
    spec = get_registry().spec("code.repo_reader")
    assert spec is not None and permits_fresh_read(spec.policy)
    with research_branch_scope(run, "coding"):
        before = await repo_reader_tool({"path": "main.py"}, ctx(run))
        assert (await write_file_tool({"path": "main.py", "content": "VALUE = 4\n"}, ctx(run))).ok
        after = await repo_reader_tool({"path": "main.py"}, ctx(run))
        assert before.ok and after.ok
        assert before.output["sha256"] != after.output["sha256"]
        assert after.output["content"] == "VALUE = 4\n"


def test_repetition_permission_does_not_cover_writes_network_or_approvals() -> None:
    assert not permits_fresh_read(ToolPolicy())
    assert permits_fresh_read(ToolPolicy(repeatable_read=True))
    assert not permits_fresh_read(ToolPolicy(repeatable_read=True, mutation_level="write"))
    assert not permits_fresh_read(ToolPolicy(repeatable_read=True, network=True))
    assert not permits_fresh_read(ToolPolicy(repeatable_read=True, requires_approval=True))


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
    saved = git(source, "rev-parse", "HEAD")
    assert saved != base and git(source, "status", "--porcelain") == ""
    with pytest.raises(GitWorkspaceError, match="尚未审核"):
        with research_branch_scope(run, "execution"):
            pytest.fail("should not enter")
    (run.root / "coding/code_spec.approved.md").write_text("---\nfiles_changed:\n- path: main.py\n  type: modified\n---\nApproved by test author, no model invoked.\n")
    with research_branch_scope(run, "execution"):
        assert git(source, "status", "--porcelain") == ""
        assert git(source, "rev-parse", "HEAD") == saved
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


@pytest.mark.asyncio
async def test_new_task_uses_baseline_and_old_task_resumes_its_saved_commit(research: tuple[RunHandle, Path]) -> None:
    run, source = research
    baseline = git(source, "rev-parse", "HEAD")
    with research_branch_scope(run, "coding"):
        assert (await write_file_tool({"path": "main.py", "content": "VALUE = 7\n"}, ctx(run))).ok
    saved = git(source, "rev-parse", "HEAD")
    other = replace(run, run_id="research_2", root=run.root.parent / "run2")
    other.root.mkdir()
    assert coding_workspace_blocker(other) == ""
    with research_branch_scope(other, "coding"):
        assert git(source, "rev-parse", "HEAD") == baseline
        assert (source / "main.py").read_text() == "VALUE = 1\n"
        assert (await write_file_tool({"path": "main.py", "content": "VALUE = 8\n"}, ctx(other))).ok
    with research_branch_scope(run, "coding_attempt_2"):
        assert git(source, "rev-parse", "HEAD") == saved
        assert (source / "main.py").read_text() == "VALUE = 7\n"
    assert git(source, "rev-parse", "baseline") == baseline
    branches = git(source, "for-each-ref", "--format=%(refname:short)", "refs/heads/mars/").splitlines()
    assert set(branches) == {branch_name(run.project, run.run_id), branch_name(other.project, other.run_id)}


@pytest.mark.asyncio
async def test_crash_recovery_saves_recorded_work_before_switch_without_polling_mutation(research: tuple[RunHandle, Path]) -> None:
    from app.harness.tools.git_branch import bind_git_branch, prepare, repository_lock
    run, source = research
    baseline = git(source, "rev-parse", "HEAD")
    # Real governed write with no stage-finalizer, representing process death.
    with repository_lock(source):
        branch = prepare(source, run.root, run.project, run.run_id)
        with bind_git_branch(branch):
            assert (await write_file_tool({"path": "main.py", "content": "VALUE = 9\n"}, ctx(run))).ok
    assert git(source, "status", "--porcelain")
    other = replace(run, run_id="research_2", root=run.root.parent / "run2")
    other.root.mkdir()
    assert coding_workspace_blocker(other) == ""
    assert git(source, "rev-parse", "HEAD") == baseline  # Read-only polling.
    with research_branch_scope(other, "coding"):
        assert (source / "main.py").read_text() == "VALUE = 1\n"
    saved = git(source, "rev-parse", branch.branch)
    assert saved != baseline
    assert git(source, "show", branch.branch + ":main.py") == "VALUE = 9"


@pytest.mark.asyncio
async def test_external_changes_are_preserved_and_never_included_in_auto_commit(research: tuple[RunHandle, Path]) -> None:
    run, source = research
    with research_branch_scope(run, "coding"):
        assert (await write_file_tool({"path": "main.py", "content": "VALUE = 2\n"}, ctx(run))).ok
        (source / "user_notes.txt").write_text("private notes\n")
        git(source, "add", "user_notes.txt")
    assert git(source, "show", "HEAD:main.py") == "VALUE = 2"
    assert git(source, "ls-tree", "HEAD", "user_notes.txt") == ""
    assert "A  user_notes.txt" in git(source, "status", "--porcelain")
    assert (source / "user_notes.txt").read_text() == "private notes\n"
    other = replace(run, run_id="research_2", root=run.root.parent / "run2")
    other.root.mkdir()
    assert "非本任务记录" in coding_workspace_blocker(other)


@pytest.mark.asyncio
async def test_external_overwrite_of_recorded_file_is_not_saved(research: tuple[RunHandle, Path]) -> None:
    run, source = research
    base = git(source, "rev-parse", "HEAD")
    with research_branch_scope(run, "coding"):
        assert (await write_file_tool({"path": "main.py", "content": "VALUE = 2\n"}, ctx(run))).ok
        (source / "main.py").write_text("EXTERNAL = True\n")
    assert git(source, "rev-parse", "HEAD") == base
    assert (source / "main.py").read_text() == "EXTERNAL = True\n"
    (run.root / "coding/code_spec.approved.md").write_text("---\nfiles_changed:\n- path: main.py\n  type: modified\n---\nApproved prior implementation.\n")
    with pytest.raises(GitWorkspaceError, match="写入记录不一致"):
        with research_branch_scope(run, "execution"):
            pytest.fail("external overwrite must not execute")
    assert git(source, "rev-parse", "HEAD") == base


@pytest.mark.asyncio
async def test_add_delete_and_rollback_are_saved_and_noop_retry_creates_no_commit(research: tuple[RunHandle, Path]) -> None:
    from app.harness.tools.code import delete_file_tool
    run, source = research
    with research_branch_scope(run, "coding"):
        assert (await write_file_tool({"path": "configs/new config.yaml", "content": "enabled: true\n"}, ctx(run))).ok
    assert git(source, "show", "HEAD:configs/new config.yaml") == "enabled: true"
    with research_branch_scope(run, "coding_attempt_2"):
        assert (await delete_file_tool({"path": "configs/new config.yaml"}, ctx(run))).ok
    assert git(source, "ls-tree", "HEAD", "configs/new config.yaml") == ""
    with research_branch_scope(run, "coding_attempt_3"):
        written = await write_file_tool({"path": "main.py", "content": "VALUE = 99\n"}, ctx(run))
        assert written.ok
        assert (await rollback_patch_tool({"rollback_ref": written.rollback_ref}, ctx(run))).ok
    saved = git(source, "rev-parse", "HEAD")
    with research_branch_scope(run, "coding_attempt_4"):
        assert (await write_file_tool({"path": "main.py", "content": "VALUE = 1\n"}, ctx(run))).ok
    assert git(source, "rev-parse", "HEAD") == saved


@pytest.mark.asyncio
async def test_deleted_previous_task_does_not_orphan_branch_or_replace_baseline(research: tuple[RunHandle, Path]) -> None:
    import shutil
    run, source = research
    baseline = git(source, "rev-parse", "HEAD")
    with research_branch_scope(run, "coding"):
        assert (await write_file_tool({"path": "main.py", "content": "VALUE = 7\n"}, ctx(run))).ok
    saved = git(source, "rev-parse", "HEAD")
    shutil.rmtree(run.root)
    other = replace(run, run_id="research_2", root=run.root.parent / "run2")
    other.root.mkdir()
    assert coding_workspace_blocker(other) == ""
    with research_branch_scope(other, "coding"):
        assert git(source, "rev-parse", "HEAD") == baseline
    assert git(source, "rev-parse", branch_name(run.project, run.run_id)) == saved
    assert not run.root.exists()
