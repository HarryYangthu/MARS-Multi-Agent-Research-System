"""Real Git history checks for frozen per-run clones and legacy shared mode."""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
import json
from pathlib import Path

import pytest

from app.harness.project_workspace import open_folder
from app.harness.tools.git_branch import (
    GitBranch, GitWorkspaceError, branch_owner, git, prepare, preflight, receipt_path, saved_branch,
)
from app.settings import reset_settings_cache


def commit(root: Path, message: str) -> str:
    git(root, 'add', '.')
    git(root, '-c', 'user.name=Test', '-c', 'user.email=test@localhost',
        '-c', 'commit.gpgsign=false', 'commit', '-m', message)
    return git(root, 'rev-parse', 'HEAD')


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[GitBranch, Path]]:
    monkeypatch.setenv('MARS_FOLDER_PROJECTS_REGISTRY', str(tmp_path / 'registry.json'))
    reset_settings_cache()
    project = open_folder(str(tmp_path / 'project'), create=True)
    source = tmp_path / 'source'
    source.mkdir()
    git(source, 'init', '-b', 'main')
    (source / 'model.py').write_text('VALUE = 1\n')
    commit(source, 'Real initial baseline')
    run_root = project.root / 'runs/frozen'
    run_root.mkdir(parents=True)
    branch = prepare(source, run_root, project.name, 'frozen')
    try:
        yield branch, run_root
    finally:
        reset_settings_cache()


def test_source_main_advance_preserves_frozen_clone_and_receipt(isolated: tuple[GitBranch, Path]) -> None:
    branch, run_root = isolated
    receipt = receipt_path(run_root).read_bytes()
    head = git(branch.root, 'rev-parse', 'HEAD')
    (branch.source_root / 'model.py').write_text('VALUE = 2\n')
    next_baseline = commit(branch.source_root, 'Advance source main for future research')
    assert next_baseline != branch.baseline_commit
    assert preflight(branch.source_root, run_root, branch.project, branch.run_id) == ''
    assert saved_branch(branch.source_root, run_root, branch.project, branch.run_id) == branch
    assert prepare(branch.source_root, run_root, branch.project, branch.run_id) == branch
    assert branch_owner(branch.root) == (branch, run_root)
    assert receipt_path(run_root).read_bytes() == receipt
    assert git(branch.root, 'rev-parse', 'HEAD') == head == branch.baseline_commit
    assert (branch.root / 'model.py').read_text() == 'VALUE = 1\n'
    assert git(branch.source_root, 'branch', '--show-current') == 'main'


def test_unrelated_candidate_history_cannot_reuse_saved_identity(isolated: tuple[GitBranch, Path]) -> None:
    branch, run_root = isolated
    git(branch.root, 'switch', '--orphan', 'unrelated')
    (branch.root / 'model.py').write_text('UNRELATED = True\n')
    unrelated = commit(branch.root, 'Actual unrelated history')
    git(branch.root, 'branch', '-f', branch.branch, unrelated)
    git(branch.root, 'switch', branch.branch)
    with pytest.raises(GitWorkspaceError, match='不在本任务代码历史') as raised:
        branch.validate(branch.project, branch.run_id)
    assert raised.value.reason['code'] == 'git_workspace_history_changed'
    assert '不在本任务代码历史' in preflight(branch.source_root, run_root, branch.project, branch.run_id)


def test_missing_frozen_commit_and_wrong_branch_remain_blocked(isolated: tuple[GitBranch, Path]) -> None:
    branch, _ = isolated
    with pytest.raises(GitWorkspaceError, match='不在本任务代码历史'):
        replace(branch, baseline_commit='a' * 40).validate(branch.project, branch.run_id)
    git(branch.root, 'switch', '-c', 'other-task')
    with pytest.raises(GitWorkspaceError, match='已切换到其他分支'):
        branch.validate(branch.project, branch.run_id)


def test_owner_record_change_still_revokes_clone_binding(isolated: tuple[GitBranch, Path]) -> None:
    from app.harness.tools.git_branch import _owner_path
    branch, _ = isolated
    path = _owner_path(branch.root, branch.branch)
    owner = json.loads(path.read_text())
    owner['run_id'] = 'different-run'
    path.write_text(json.dumps(owner))
    with pytest.raises(GitWorkspaceError, match='任务记录无法校验'):
        branch_owner(branch.root)


def test_legacy_shared_checkout_still_requires_unchanged_baseline(isolated: tuple[GitBranch, Path]) -> None:
    branch, _ = isolated
    legacy = GitBranch(branch.project, 'legacy', str(branch.source_root), 'main', 'main', branch.baseline_commit)
    (branch.source_root / 'model.py').write_text('VALUE = 3\n')
    commit(branch.source_root, 'Real shared baseline change')
    with pytest.raises(GitWorkspaceError, match='原基线分支已有新提交') as raised:
        legacy.validate(legacy.project, legacy.run_id)
    assert raised.value.reason['code'] == 'git_workspace_baseline_changed'
