"""Real Git checkouts and actual code tools; no model or tool substitutes."""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
import json
import os
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
    index = (source / '.git/index').read_bytes()
    baseline = git(source, 'rev-parse', 'HEAD')
    assert coding_workspace_blocker(run) == ''
    assert not receipt_path(run.root).exists()
    with research_branch_scope(run, 'coding'):
        work = load_project_repo(run.project).root
        assert work != source and (work / 'main.py').read_text() == 'USER_CHANGE = True\n'
        branch = current_git_branch(run.project); assert branch is not None
        assert branch.baseline_commit == baseline and branch.initial_commit != baseline
        assert git(work, 'status', '--porcelain') == ''
        assert git(work, 'rev-parse', 'HEAD') == branch.initial_commit
    assert git(source, "branch", "--show-current") == "baseline"
    assert (source / "main.py").read_text() == "USER_CHANGE = True\n"
    assert (source / '.git/index').read_bytes() == index


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


def _dirty_source(source: Path) -> dict[str, bytes | str]:
    (source / 'configs').mkdir(exist_ok=True)
    for name in ('remove.yaml', 'staged_remove.yaml'):
        (source / 'configs' / name).write_text('enabled: true\n')
    git(source, 'add', '.')
    git(source, '-c', 'user.name=Test', '-c', 'user.email=test@localhost', '-c', 'commit.gpgsign=false',
        'commit', '-m', 'Actual source configuration baseline')
    (source / 'main.py').write_text('VALUE = 2\n')
    git(source, 'add', 'main.py')
    (source / 'main.py').write_text('VALUE = 3\n')
    (source / 'configs/remove.yaml').unlink()
    git(source, 'rm', 'configs/staged_remove.yaml')
    (source / 'configs').mkdir(exist_ok=True)
    (source / 'configs/new 中文 file.yaml').write_text('current_value: 3\n')
    return {'head': git(source, 'rev-parse', 'HEAD'), 'branch': git(source, 'branch', '--show-current'),
        'index': (source / '.git/index').read_bytes(),
        'status': git(source, '--no-optional-locks', 'status', '--porcelain=v1', '-z'),
        'refs': git(source, 'show-ref')}


def _assert_source_preserved(source: Path, before: dict[str, bytes | str]) -> None:
    assert git(source, 'rev-parse', 'HEAD') == before['head']
    assert git(source, 'branch', '--show-current') == before['branch']
    assert (source / '.git/index').read_bytes() == before['index']
    assert git(source, '--no-optional-locks', 'status', '--porcelain=v1', '-z') == before['status']
    assert git(source, 'show-ref') == before['refs']
    assert (source / 'main.py').read_text() == 'VALUE = 3\n'
    assert not (source / 'configs/remove.yaml').exists()
    assert not (source / 'configs/staged_remove.yaml').exists()


def test_dirty_snapshot_captures_final_working_code_deletions_and_untracked_without_touching_index(
        research: tuple[RunHandle, Path]) -> None:
    run, source = research
    before = _dirty_source(source)
    with research_branch_scope(run, 'coding'):
        branch = current_git_branch(run.project); assert branch is not None
        work = branch.root
        assert branch.baseline_commit == before['head'] and branch.initial_commit != before['head']
        assert git(work, 'rev-parse', 'HEAD^') == before['head']
        assert git(work, 'rev-parse', 'HEAD') == branch.initial_commit
        assert (work / 'main.py').read_text() == 'VALUE = 3\n'
        assert (work / 'configs/new 中文 file.yaml').read_text() == 'current_value: 3\n'
        assert not (work / 'configs/remove.yaml').exists() and not (work / 'configs/staged_remove.yaml').exists()
        assert git(work, 'status', '--porcelain') == ''
    _assert_source_preserved(source, before)
    receipt = json.loads((run.root / 'input/research_source_snapshot.json').read_text())
    assert receipt['initial_commit'] != receipt['baseline_commit']
    assert receipt['source_index_sha256']
    assert receipt['snapshot_is_agent_code_change'] is False and receipt['source_modified'] is False


def test_dirty_snapshot_with_stale_index_stat_cache_preserves_source_and_starts_checkout(
        research: tuple[RunHandle, Path]) -> None:
    """Relocated Windows archives commonly have fresh inodes/timestamps."""
    run, source = research
    _dirty_source(source)
    unchanged = source / 'baseline/ref.py'
    info = unchanged.stat()
    os.utime(unchanged, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
    index = (source / '.git/index').read_bytes()
    with research_branch_scope(run, 'coding'):
        branch = current_git_branch(run.project); assert branch is not None
        assert (branch.root / 'main.py').read_text() == 'VALUE = 3\n'
        assert (branch.root / 'baseline/ref.py').read_text() == 'REFERENCE = 1\n'
        assert not (branch.root / 'configs/remove.yaml').exists()
        assert not (branch.root / 'configs/staged_remove.yaml').exists()
        assert git(branch.root, 'status', '--porcelain') == ''
    # A read-only source snapshot must not refresh stat-only index entries.
    assert (source / '.git/index').read_bytes() == index
    assert unchanged.stat().st_mtime_ns == info.st_mtime_ns + 1_000_000_000
    receipt = json.loads((run.root / 'input/research_source_snapshot.json').read_text())
    assert 'baseline/ref.py' not in {row['path'] for row in receipt['files']}


def test_index_fingerprint_tolerates_real_git_cache_refresh_but_detects_staged_changes(
        research: tuple[RunHandle, Path]) -> None:
    from app.harness.tools.git_source_snapshot import _index_entries_hash, _selection
    _run, source = research
    unchanged = source / 'baseline/ref.py'
    info = unchanged.stat()
    os.utime(unchanged, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
    raw_index = (source / '.git/index').read_bytes()
    staged = _index_entries_hash(source)
    selection = _selection(source)
    assert selection == ([], [])
    assert (source / '.git/index').read_bytes() == raw_index
    assert git(source, 'status', '--porcelain') == ''
    assert (source / '.git/index').read_bytes() != raw_index
    assert _index_entries_hash(source) == staged
    assert _selection(source) == selection
    (source / 'main.py').write_text('VALUE = 5\n')
    git(source, 'add', 'main.py')
    assert _index_entries_hash(source) != staged


def test_index_fingerprint_detects_source_index_visibility_flags(research: tuple[RunHandle, Path]) -> None:
    from app.harness.tools.git_source_snapshot import _index_entries_hash
    _run, source = research
    staged = _index_entries_hash(source)
    git(source, 'update-index', '--assume-unchanged', 'main.py')
    assert _index_entries_hash(source) != staged
    git(source, 'update-index', '--no-assume-unchanged', 'main.py')
    assert _index_entries_hash(source) == staged
    git(source, 'update-index', '--skip-worktree', 'main.py')
    assert _index_entries_hash(source) != staged


@pytest.mark.skipif(os.name == 'nt', reason='Windows does not expose a POSIX executable bit')
def test_content_verified_snapshot_still_captures_executable_mode_only_changes(
        research: tuple[RunHandle, Path]) -> None:
    run, source = research
    index = (source / '.git/index').read_bytes()
    (source / 'main.py').chmod(0o755)
    with research_branch_scope(run, 'coding'):
        branch = current_git_branch(run.project); assert branch is not None
        assert (branch.root / 'main.py').read_text() == 'VALUE = 1\n'
        assert git(branch.root, 'ls-files', '--stage', 'main.py').startswith('100755 ')
        assert git(branch.root, 'status', '--porcelain') == ''
    assert (source / '.git/index').read_bytes() == index
    receipt = json.loads((run.root / 'input/research_source_snapshot.json').read_text())
    assert receipt['files'][0]['path'] == 'main.py' and receipt['files'][0]['mode'] == '100755'


def test_dirty_snapshot_retry_and_new_task_use_distinct_frozen_inputs(research: tuple[RunHandle, Path]) -> None:
    run, source = research
    _dirty_source(source)
    with research_branch_scope(run, 'coding'):
        first = current_git_branch(run.project); assert first is not None
    receipt = receipt_path(run.root).read_bytes()
    (source / 'main.py').write_text('VALUE = 8\n')
    (source / 'configs/new 中文 file.yaml').write_text('current_value: 8\n')
    other = other_run(run)
    with research_branch_scope(other, 'coding'):
        second = current_git_branch(run.project); assert second is not None
        assert (second.root / 'main.py').read_text() == 'VALUE = 8\n'
    with research_branch_scope(run, 'coding_attempt_2'):
        assert current_git_branch(run.project) == first
        assert (first.root / 'main.py').read_text() == 'VALUE = 3\n'
        assert (first.root / 'configs/new 中文 file.yaml').read_text() == 'current_value: 3\n'
    assert receipt_path(run.root).read_bytes() == receipt and first.root != second.root
    assert first.initial_commit != second.initial_commit


def test_parallel_dirty_snapshots_have_independent_indexes_and_keep_source_untouched(
        research: tuple[RunHandle, Path]) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from app.harness.tools.git_branch import prepare
    run, source = research
    before = _dirty_source(source)
    other = other_run(run)
    with ThreadPoolExecutor(max_workers=2) as executor:
        calls = [executor.submit(prepare, source, task.root, task.project, task.run_id) for task in (run, other)]
        branches = [call.result(timeout=20) for call in calls]
    assert branches[0].root != branches[1].root and branches[0].branch != branches[1].branch
    assert branches[0].initial_commit != branches[1].initial_commit
    assert len({git(branch.root, 'rev-parse', 'HEAD^{tree}') for branch in branches}) == 1
    assert all(git(branch.root, 'status', '--porcelain') == '' for branch in branches)
    _assert_source_preserved(source, before)


def test_snapshot_inputs_are_not_misreported_as_agent_writes_or_unapproved_code(
        research: tuple[RunHandle, Path]) -> None:
    from app.bridge.git_code_delivery import describe_committed_delivery
    run, source = research
    _dirty_source(source)
    with research_branch_scope(run, 'coding'):
        branch = current_git_branch(run.project); assert branch is not None
        description = describe_committed_delivery(run)
        assert description['files_changed'] == []
        assert description['canonical_diff'] == ''
        assert description['coding_backend']['baseline_commit'] == branch.initial_commit
    with research_branch_scope(run, 'execution'):
        result = subprocess.run([sys.executable, '-c', "import runpy; assert runpy.run_path('main.py')['VALUE']==3"],
                                cwd=checkout(run), capture_output=True)
        assert result.returncode == 0 and not result.stderr


def test_snapshot_explicitly_excludes_runtime_data_credentials_and_links(
        research: tuple[RunHandle, Path]) -> None:
    run, source = research
    _dirty_source(source)
    (source / 'outputs').mkdir(); (source / 'outputs/run.txt').write_text('not code\n')
    (source / 'data').mkdir(); (source / 'data/values.txt').write_text('not a code input\n')
    (source / '.env.local').write_text('PRIVATE_TEST_CONFIGURATION=not_for_snapshot\n')
    outside = source.parent / 'outside.py'; outside.write_text('OUTSIDE = True\n')
    (source / 'configs/link.py').symlink_to(outside)
    with research_branch_scope(run, 'coding'):
        branch = current_git_branch(run.project); assert branch is not None
        assert (branch.root / 'main.py').read_text() == 'VALUE = 3\n'
        for relative in ('outputs/run.txt', 'data/values.txt', '.env.local', 'configs/link.py'):
            assert not (branch.root / relative).exists()
    record = json.loads((run.root / 'input/research_source_snapshot.json').read_text())
    excluded = {row['path'] for row in record['excluded']}
    assert {'outputs/run.txt', 'data/values.txt', '.env.local', 'configs/link.py'} <= excluded


def test_oversized_source_fails_explicitly_instead_of_reusing_old_code(research: tuple[RunHandle, Path]) -> None:
    from app.harness.discovery.snapshots import SnapshotPolicy
    from app.harness.tools.git_branch import prepare
    run, source = research
    before = _dirty_source(source)
    with (source / 'main.py').open('wb') as stream:
        stream.truncate(SnapshotPolicy(allowed_paths=('*',)).max_file_bytes + 1)
    before['status'] = git(source, '--no-optional-locks', 'status', '--porcelain=v1', '-z')
    with pytest.raises(ValueError, match='source_snapshot_input_too_large'):
        prepare(source, run.root, run.project, run.run_id)
    from app.harness.tools.git_branch import workspace_path
    assert not receipt_path(run.root).exists() and not workspace_path(source, run.project, run.run_id).exists()
    assert (source / '.git/index').read_bytes() == before['index']
    assert git(source, 'rev-parse', 'HEAD') == before['head']


def test_changed_tracked_source_symlink_is_rejected_instead_of_running_old_code(
        research: tuple[RunHandle, Path]) -> None:
    from app.harness.tools.git_branch import prepare
    run, source = research
    head = git(source, 'rev-parse', 'HEAD')
    index = (source / '.git/index').read_bytes()
    outside = source.parent / 'outside-source.py'; outside.write_text('VALUE = 29\n')
    (source / 'main.py').unlink(); (source / 'main.py').symlink_to(outside)
    with pytest.raises(ValueError, match='source_snapshot_input_unsupported'):
        prepare(source, run.root, run.project, run.run_id)
    assert not receipt_path(run.root).exists() and (source / 'main.py').is_symlink()
    assert outside.read_text() == 'VALUE = 29\n'
    assert (source / '.git/index').read_bytes() == index and git(source, 'rev-parse', 'HEAD') == head


def test_snapshot_checkout_is_clean_with_inherited_autocrlf_true(
        research: tuple[RunHandle, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    run, source = research
    before = _dirty_source(source)
    global_config = run.root.parent / 'actual-global-gitconfig'
    global_config.write_text('[core]\n\tautocrlf = true\n')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(global_config))
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    assert git(source, 'config', '--get', 'core.autocrlf') == 'true'
    (source / 'main.py').write_bytes(b'VALUE = 3\r\n')
    with research_branch_scope(run, 'coding'):
        branch = current_git_branch(run.project); assert branch is not None
        assert (branch.root / 'main.py').read_bytes() == b'VALUE = 3\r\n'
        assert (branch.root / 'baseline/ref.py').read_bytes() == b'REFERENCE = 1\n'
        assert git(branch.root, 'status', '--porcelain') == ''
    _assert_source_preserved(source, before)


def test_captured_snapshot_recovers_after_source_advances_before_workspace_publication(
        research: tuple[RunHandle, Path], tmp_path: Path) -> None:
    from dataclasses import asdict, replace
    from app.harness.agent_loop.trace import atomic_json
    from app.harness.tools.git_branch import _inspect, prepare
    from app.harness.tools.git_source_snapshot import capture_source_snapshot, apply_source_snapshot, save_source_snapshot_binding
    run, source = research
    _dirty_source(source)
    (run.root / 'input').mkdir(exist_ok=True)
    original = _inspect(source, run.root, run.project, run.run_id)
    snapshot = capture_source_snapshot(source, run.root, run.project, run.run_id,
        original.baseline_branch, original.baseline_commit)
    assert snapshot is not None
    # Build actual Git objects just as a process that ended before publication;
    # no fake command response or patched tool is used.
    interrupted = tmp_path / 'interrupted-clone'
    git(source, 'clone', '--local', '--no-hardlinks', '--no-checkout', str(source), str(interrupted))
    git(interrupted, 'switch', '-c', original.branch, original.baseline_commit)
    commit = apply_source_snapshot(snapshot, interrupted)
    saved = replace(original, initial_commit=commit)
    save_source_snapshot_binding(snapshot, run.root, commit)
    atomic_json(run.root / 'input/research_workspace_intent.json',
        {'schema_id': 'research.git_workspace_intent.v1', **asdict(saved)})
    (source / 'main.py').write_text('VALUE = 19\n')
    git(source, 'add', 'main.py')
    git(source, '-c', 'user.name=Test', '-c', 'user.email=test@localhost', '-c', 'commit.gpgsign=false',
        'commit', '-m', 'Actual source advances after frozen input')
    recovered = prepare(source, run.root, run.project, run.run_id)
    assert recovered == saved and git(recovered.root, 'rev-parse', 'HEAD') == commit
    assert (recovered.root / 'main.py').read_text() == 'VALUE = 3\n'
    assert (source / 'main.py').read_text() == 'VALUE = 19\n'


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


def test_missing_checkout_blocks_but_source_advance_preserves_frozen_candidate(research: tuple[RunHandle, Path]) -> None:
    import shutil
    run, source = research
    with research_branch_scope(run, "coding"):
        pass
    work = checkout(run); renamed = work.with_name(work.name + "-retained"); work.rename(renamed)
    assert "代码目录缺失" in coding_workspace_blocker(run)
    renamed.rename(work)
    frozen = json.loads(receipt_path(run.root).read_text())
    original_head = git(work, "rev-parse", "HEAD")
    (source / "main.py").write_text("VALUE = 2\n"); git(source, "add", ".")
    git(source, "-c", "user.name=Test", "-c", "user.email=test@localhost", "-c", "commit.gpgsign=false", "commit", "-m", "New baseline")
    assert coding_workspace_blocker(run) == ""
    with research_branch_scope(run, "coding"):
        assert git(work, "rev-parse", "HEAD") == original_head
        assert (work / "main.py").read_text() == "VALUE = 1\n"
    assert json.loads(receipt_path(run.root).read_text()) == frozen


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
    assert len(measured) == 2 and measured[0] == 0 and measured[1] == pytest.approx(14 / 3)
    assert (source / 'main.py').read_text() == 'VALUE = 1\n'
