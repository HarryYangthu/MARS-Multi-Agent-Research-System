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


def checkout(run: RunHandle) -> Path:
    return Path(json.loads(receipt_path(run.root).read_text())["repo_path"])


def other_run(run: RunHandle) -> RunHandle:
    other = replace(run, run_id="research_2", root=run.root.parent / "run2")
    other.root.mkdir()
    return other


@pytest.mark.asyncio
async def test_actual_mutations_compile_rollback_and_baseline_preserved(research: tuple[RunHandle, Path]) -> None:
    run, source = research
    baseline = git(source, "rev-parse", "HEAD")
    assert coding_workspace_blocker(run) == "" and not receipt_path(run.root).exists()
    assert not (await write_file_tool({"path": "main.py", "content": "VALUE = 2\n"}, ctx(run))).ok
    with research_branch_scope(run, "coding"):
        repo = load_project_repo(run.project)
        assert repo.root != source and repo.root.is_dir() and not repo.read_only
        written = await write_file_tool({"path": "main.py", "content": "VALUE = 2\n"}, ctx(run))
        assert written.ok and written.rollback_ref
        patch = "--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-VALUE = 2\n+VALUE = 3\n"
        assert (await apply_patch_tool({"diff": patch}, ctx(run))).ok
        compiled = subprocess.run([sys.executable, "-m", "py_compile", str(repo.root / "main.py")], capture_output=True)
        assert compiled.returncode == 0
        assert (await rollback_patch_tool({"rollback_ref": written.rollback_ref}, ctx(run))).ok
        assert (repo.root / "main.py").read_text() == "VALUE = 1\n"
    assert current_git_branch(run.project) is None and load_project_repo(run.project).read_only
    assert git(source, "branch", "--show-current") == "baseline"
    assert git(source, "rev-parse", "HEAD") == baseline and git(source, "status", "--porcelain") == ""


def test_preflight_never_switches_or_copies_and_preserves_dirty_baseline(research: tuple[RunHandle, Path]) -> None:
    run, source = research
    (source / "main.py").write_text("USER_CHANGE = True\n")
    assert "未提交改动" in coding_workspace_blocker(run)
    with pytest.raises(GitWorkspaceError, match="未提交改动"):
        with research_branch_scope(run, "coding"):
            pytest.fail("should not enter")
    assert git(source, "branch", "--show-current") == "baseline"
    assert (source / "main.py").read_text() == "USER_CHANGE = True\n"
    assert not receipt_path(run.root).exists()


def test_detached_baseline_and_changed_saved_identity_are_rejected(research: tuple[RunHandle, Path]) -> None:
    run, source = research
    git(source, "checkout", "--detach", "HEAD")
    assert "游离提交状态" in coding_workspace_blocker(run)
    git(source, "switch", "baseline")
    with research_branch_scope(run, "coding"):
        pass
    raw = json.loads(receipt_path(run.root).read_text()); raw["run_id"] = "another_run"
    receipt_path(run.root).write_text(json.dumps(raw))
    assert "无法校验" in coding_workspace_blocker(run)


@pytest.mark.asyncio
async def test_stop_retry_reuses_own_checkout_branch_and_saved_commit(research: tuple[RunHandle, Path]) -> None:
    run, source = research
    with pytest.raises(RuntimeError, match="interrupted"):
        with research_branch_scope(run, "coding"):
            assert (await write_file_tool({"path": "main.py", "content": "VALUE = 9\n"}, ctx(run))).ok
            raise RuntimeError("interrupted")
    work = checkout(run); receipt = receipt_path(run.root).read_bytes(); head = git(work, "rev-parse", "HEAD")
    assert git(work, "status", "--porcelain") == ""
    other = other_run(run)
    with research_branch_scope(other, "coding"):
        assert (load_project_repo(run.project).root / "main.py").read_text() == "VALUE = 1\n"
    with research_branch_scope(run, "coding_attempt_2"):
        assert load_project_repo(run.project).root == work
        assert (work / "main.py").read_text() == "VALUE = 9\n"
    assert receipt_path(run.root).read_bytes() == receipt and git(work, "rev-parse", "HEAD") == head
    assert git(source, "branch", "--show-current") == "baseline"


@pytest.mark.asyncio
async def test_two_actual_tasks_write_run_and_browse_different_code_at_once(research: tuple[RunHandle, Path]) -> None:
    import asyncio
    from app.bridge.code_repository import run_code_repository
    run, source = research; other = other_run(run)
    ready = asyncio.Queue[int](); release = asyncio.Event()
    async def worker(task: RunHandle, value: int) -> tuple[Path, bytes]:
        with research_branch_scope(task, "coding"):
            root = load_project_repo(task.project).root
            assert (await write_file_tool({"path": "main.py", "content": f"VALUE = {value}\n"}, ctx(task))).ok
            await ready.put(value)
            await release.wait()
            process = await asyncio.create_subprocess_exec(sys.executable, "-c",
                "import runpy; print(runpy.run_path('main.py')['VALUE'] ** 2)", cwd=root,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await process.communicate()
            assert process.returncode == 0 and not stderr
            return root, stdout
    first = asyncio.create_task(worker(run, 7)); second = asyncio.create_task(worker(other, 8))
    try:
        await asyncio.wait_for(ready.get(), 20); await asyncio.wait_for(ready.get(), 20)
        first_view = run_code_repository(run, project=run.project).file("main.py")
        second_view = run_code_repository(other, project=other.project).file("main.py")
        assert first_view["lines"] == ["VALUE = 7\n"] and second_view["lines"] == ["VALUE = 8\n"]
    finally:
        release.set()
    results = await asyncio.wait_for(asyncio.gather(first, second), 20)
    assert results[0][0] != results[1][0] and [r[1] for r in results] == [b"49\n", b"64\n"]
    assert (source / "main.py").read_text() == "VALUE = 1\n"
    assert git(source, "for-each-ref", "--format=%(refname)", "refs/heads/mars/") == ""
    assert all(git(root, "status", "--porcelain") == "" for root, _ in results)


def test_only_same_research_lock_blocks_other_research_can_start(research: tuple[RunHandle, Path]) -> None:
    from app.harness.tools.git_branch import repository_lock
    run, source = research
    with research_branch_scope(run, "coding"):
        pass
    other = other_run(run)
    with repository_lock(checkout(run)):
        assert "此研究" in coding_workspace_blocker(run)
        assert coding_workspace_blocker(other) == ""
        with research_branch_scope(other, "coding"):
            assert load_project_repo(run.project).root != checkout(run)
    assert git(source, "branch", "--show-current") == "baseline"


@pytest.mark.asyncio
async def test_external_branch_switch_revokes_only_that_checkout(research: tuple[RunHandle, Path]) -> None:
    run, source = research
    with research_branch_scope(run, "coding"):
        repo = load_project_repo(run.project)
        git(repo.root, "switch", "-c", "external")
        assert not (await write_file_tool({"path": "main.py", "content": "NO\n"}, ctx(run))).ok
        assert not (await repo_reader_tool({"path": "main.py"}, ctx(run))).ok
        with pytest.raises(GitWorkspaceError, match="其他分支"):
            resolve_allowed_path(repo, "main.py", for_write=True)
    assert git(source, "branch", "--show-current") == "baseline"


@pytest.mark.asyncio
async def test_allowed_paths_baseline_rules_and_symlinks_still_protect_each_copy(research: tuple[RunHandle, Path]) -> None:
    run, source = research
    outside = source.parent / "private.py"; outside.write_text("PRIVATE = True\n")
    with research_branch_scope(run, "coding"):
        work = load_project_repo(run.project).root
        for name in ["baseline/ref.py", ".git/config", ".env.local", "unknown.py", "../private.py"]:
            assert not (await write_file_tool({"path": name, "content": "OVERWRITE\n"}, ctx(run))).ok
        (work / "configs").mkdir(); (work / "configs/link.py").symlink_to(outside)
        assert not (await write_file_tool({"path": "configs/link.py", "content": "OVERWRITE\n"}, ctx(run))).ok
        wrong = ctx(run); wrong.run_id = "another_run"
        assert not (await write_file_tool({"path": "main.py", "content": "NO\n"}, wrong)).ok
    assert outside.read_text() == "PRIVATE = True\n"
    assert (source / "baseline/ref.py").read_text() == "REFERENCE = 1\n"


@pytest.mark.asyncio
async def test_unreviewed_and_external_changes_never_execute_or_auto_commit(research: tuple[RunHandle, Path]) -> None:
    run, source = research
    with research_branch_scope(run, "coding"):
        work = load_project_repo(run.project).root
        assert (await write_file_tool({"path": "main.py", "content": "VALUE = 2\n"}, ctx(run))).ok
        (work / "user_notes.txt").write_text("private notes\n"); git(work, "add", "user_notes.txt")
    assert git(work, "show", "HEAD:main.py") == "VALUE = 2"
    assert git(work, "ls-tree", "HEAD", "user_notes.txt") == ""
    assert "A  user_notes.txt" in git(work, "status", "--porcelain")
    with pytest.raises(GitWorkspaceError, match="尚未审核"):
        with research_branch_scope(run, "execution"):
            pytest.fail("should not enter")
    (run.root / "coding/code_spec.approved.md").write_text("---\nfiles_changed: []\n---\nNo approval.\n")
    with pytest.raises(GitWorkspaceError, match="未纳入"):
        with research_branch_scope(run, "execution"):
            pytest.fail("should not enter")
    other = other_run(run)
    assert coding_workspace_blocker(other) == ""
    with research_branch_scope(other, "coding"):
        assert (load_project_repo(run.project).root / "main.py").read_text() == "VALUE = 1\n"


@pytest.mark.asyncio
async def test_execution_uses_approved_saved_code_in_its_own_copy(research: tuple[RunHandle, Path]) -> None:
    run, source = research
    baseline = git(source, "rev-parse", "HEAD")
    with research_branch_scope(run, "coding"):
        assert (await write_file_tool({"path": "main.py", "content": "VALUE = 2\n"}, ctx(run))).ok
    work = checkout(run); head = git(work, "rev-parse", "HEAD")
    (run.root / "coding/code_spec.approved.md").write_text("---\nfiles_changed:\n- path: main.py\n  type: modified\n---\nApproved actual write.\n")
    with research_branch_scope(run, "execution"):
        assert load_project_repo(run.project).root == work and git(work, "rev-parse", "HEAD") == head
    assert git(source, "rev-parse", "HEAD") == baseline


@pytest.mark.asyncio
async def test_external_overwrite_is_preserved_but_not_checkpointed(research: tuple[RunHandle, Path]) -> None:
    run, source = research
    with research_branch_scope(run, "coding"):
        work = load_project_repo(run.project).root; base = git(work, "rev-parse", "HEAD")
        assert (await write_file_tool({"path": "main.py", "content": "VALUE = 2\n"}, ctx(run))).ok
        (work / "main.py").write_text("EXTERNAL = True\n")
    assert git(work, "rev-parse", "HEAD") == base and (work / "main.py").read_text() == "EXTERNAL = True\n"
    (run.root / "coding/code_spec.approved.md").write_text("---\nfiles_changed:\n- path: main.py\n  type: modified\n---\nApproval.\n")
    with pytest.raises(GitWorkspaceError, match="写入记录不一致"):
        with research_branch_scope(run, "execution"):
            pytest.fail("should not enter")


@pytest.mark.asyncio
async def test_repeated_read_observes_written_file_and_noop_retry_does_not_commit(research: tuple[RunHandle, Path]) -> None:
    run, source = research
    spec = get_registry().spec("code.repo_reader"); assert spec is not None and permits_fresh_read(spec.policy)
    with research_branch_scope(run, "coding"):
        before = await repo_reader_tool({"path": "main.py"}, ctx(run))
        assert (await write_file_tool({"path": "main.py", "content": "VALUE = 4\n"}, ctx(run))).ok
        after = await repo_reader_tool({"path": "main.py"}, ctx(run))
        assert before.output["sha256"] != after.output["sha256"] and after.output["content"] == "VALUE = 4\n"
    work = checkout(run); head = git(work, "rev-parse", "HEAD")
    with research_branch_scope(run, "coding_attempt_2"):
        assert (await write_file_tool({"path": "main.py", "content": "VALUE = 4\n"}, ctx(run))).ok
    assert git(work, "rev-parse", "HEAD") == head


@pytest.mark.asyncio
async def test_legacy_migration_saves_governed_partial_work_keeps_branch_and_receipts(research: tuple[RunHandle, Path]) -> None:
    from dataclasses import asdict
    from app.harness.agent_loop.trace import atomic_json
    from app.harness.tools.git_branch import GitBranch, _owner_path, bind_git_branch
    run, source = research; baseline = git(source, "rev-parse", "HEAD")
    branch = GitBranch(run.project, run.run_id, str(source), branch_name(run.project, run.run_id), "baseline", baseline)
    git(source, "switch", "-c", branch.branch)
    atomic_json(receipt_path(run.root), {"schema_id": "research.git_branch.v1", **asdict(branch)})
    atomic_json(_owner_path(source, branch.branch), {"run_root": str(run.root), "project": run.project,
        "run_id": run.run_id, "binding": asdict(branch)})
    with bind_git_branch(branch):
        assert (await write_file_tool({"path": "main.py", "content": "VALUE = 11\n"}, ctx(run))).ok
    old_receipt = receipt_path(run.root).read_bytes()
    with research_branch_scope(run, "coding_attempt_2"):
        work = load_project_repo(run.project).root
        assert work != source and (work / "main.py").read_text() == "VALUE = 11\n"
    assert receipt_path(run.root).with_name("research_branch.pre_isolation.json").read_bytes() == old_receipt
    assert git(work, "branch", "--show-current") == branch.branch
    assert git(work, "rev-parse", "HEAD") == git(source, "rev-parse", "HEAD")
    assert git(source, "rev-parse", "baseline") == baseline
    other = other_run(run)
    with research_branch_scope(other, "coding"):
        assert (load_project_repo(run.project).root / "main.py").read_text() == "VALUE = 1\n"


def test_missing_checkout_or_changed_baseline_never_restarts_from_wrong_code(research: tuple[RunHandle, Path]) -> None:
    import shutil
    run, source = research
    with research_branch_scope(run, "coding"):
        pass
    work = checkout(run); renamed = work.with_name(work.name + "-retained"); work.rename(renamed)
    assert "代码目录缺失" in coding_workspace_blocker(run)
    renamed.rename(work)
    (source / "main.py").write_text("VALUE = 2\n"); git(source, "add", ".")
    git(source, "-c", "user.name=Test", "-c", "user.email=test@localhost", "-c", "commit.gpgsign=false", "commit", "-m", "New baseline")
    assert "基线分支已有新提交" in coding_workspace_blocker(run)


def test_read_permission_does_not_cover_mutations_or_network() -> None:
    assert not permits_fresh_read(ToolPolicy())
    assert permits_fresh_read(ToolPolicy(repeatable_read=True))
    assert not permits_fresh_read(ToolPolicy(repeatable_read=True, mutation_level="write"))
    assert not permits_fresh_read(ToolPolicy(repeatable_read=True, network=True))
    assert not permits_fresh_read(ToolPolicy(repeatable_read=True, requires_approval=True))


@pytest.mark.asyncio
async def test_parallel_registered_commands_use_exact_reviewed_checkouts_and_commits(
    research: tuple[RunHandle, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    from app.harness.tools.execution.local_command import LocalCommandJob, _command, run_local_command
    from app.harness.schema.frontmatter_parser import dumps
    from app.settings import repo_root
    run, source = research
    other = other_run(run)
    data = tmp_path / "measurements.json"
    data.write_text(json.dumps([[1, 2], [2, 4], [3, 6]]))
    runner = (
        "import json, os, pathlib, time\n"
        "request=json.loads(pathlib.Path(os.environ['MARS_JOB_REQUEST']).read_text())\n"
        "assert os.environ['MARS_CODE_ROOT'] == request['code_workspace']\n"
        "time.sleep(0.1)\n"
        "samples=json.loads(pathlib.Path(request['config']['data_path']).read_text())\n"
        "predictions=[VALUE*x for x,y in samples]\n"
        "mse=sum((p-y)**2 for p,(x,y) in zip(predictions,samples))/len(samples)\n"
        "pathlib.Path('measurements.json').write_text(json.dumps({'samples':samples,'predictions':predictions}))\n"
        "pathlib.Path(os.environ['MARS_RESULT_PATH']).write_text(json.dumps({'schema':'local_command_result.v1',"
        "'status':'completed','invocation_id':request['invocation_id'],'run_id':request['run_id'],"
        "'experiment_id':request['experiment_id'],'metrics':{'mse':mse},'evidence_paths':['measurements.json']}))\n"
    )
    tools = yaml.safe_load((repo_root() / "configs/tools.yaml").read_text())
    tools['tools']['execution.simulation_runner']['command_allowlist'] = [[sys.executable]]
    tools_path = tmp_path / "tools.yaml"
    tools_path.write_text(yaml.safe_dump(tools))
    commands = [
        {"id": "measure", "argv": [sys.executable, str(source / "main.py")], "required_metrics": ["mse"]},
        {"id": "template", "argv": [sys.executable, "{code_root}/main.py"], "required_metrics": ["mse"]},
        {"id": "unsafe", "argv": [sys.executable, "-c", f"import runpy; runpy.run_path({str(source / 'main.py')!r})"]},
    ]
    config_path = tmp_path / "execution.yaml"
    config_path.write_text(yaml.safe_dump({"execution": {"backend": "local_command",
        "command_timeout_seconds": 5, "local_commands": commands}}))
    monkeypatch.setenv("MARS_TOOLS_CONFIG_PATH", str(tools_path))
    monkeypatch.setenv("MARS_EXECUTION_CONFIG_PATH", str(config_path))
    for task, value in ((run, 2), (other, 3)):
        with research_branch_scope(task, "coding"):
            assert (await write_file_tool({"path": "main.py", "content": f"VALUE = {value}\n" + runner}, ctx(task))).ok
        approved = task.root / "coding/code_spec.approved.md"
        approved.write_text(dumps({"files_changed": [{"path": "main.py"}]}, "Actual delivered code review."))
        preview_job = LocalCommandJob(task.run_id, "measure", task.project, task.root, command_id="measure")
        preview_argv = _command(preview_job, "execution.simulation_runner")[0]
        assert preview_argv == (sys.executable, str(checkout(task) / "main.py"))
        template = replace(preview_job, command_id="template")
        assert _command(template, "execution.simulation_runner")[0] == preview_argv
        with pytest.raises(ValueError, match="共享代码仓路径"):
            _command(replace(preview_job, command_id="unsafe"), "execution.simulation_runner")

    async def execute(task: RunHandle) -> float:
        with research_branch_scope(task, "execution"):
            result = await run_local_command(LocalCommandJob(task.run_id, "measure", task.project, task.root,
                config={"data_path": str(data)}, command_id="measure"))
            assert result.status == "completed", result.error
            receipt = json.loads(Path(result.artifacts[0]['path']).read_text())
            request = json.loads(Path(result.artifacts[1]['path']).read_text())
            assert request['code_workspace'] == str(checkout(task))
            assert request['code_commit'] == git(checkout(task), 'rev-parse', 'HEAD')
            assert receipt['argv'][1] == str(checkout(task) / 'main.py')
            assert all(Path(row['path']).is_relative_to(task.root) for row in receipt['evidence'])
            return result.metrics['mse']
    measured = await asyncio.gather(execute(run), execute(other))
    assert measured == [0, pytest.approx(14 / 3)]
    assert (source / 'main.py').read_text() == 'VALUE = 1\n'
