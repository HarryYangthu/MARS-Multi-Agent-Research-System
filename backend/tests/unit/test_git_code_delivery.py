"""Committed maintainer changes are reviewed from actual Git objects, never fake tools."""
from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
import hashlib
from pathlib import Path

import pytest
import yaml

from app.bridge.coding_approval import verify_written_code
from app.bridge.completed_code_changes import completed_code_changes
from app.bridge.git_code_delivery import describe_committed_delivery, git_delivery_is_approved, verify_git_delivery, verify_approved_git_history
from app.bridge.research_branch import research_branch_scope
from app.harness.project_workspace import open_folder
from app.harness.schema.frontmatter_parser import dumps
from app.harness.tools.git_branch import GitWorkspaceError, git, prepare
from app.settings import reset_settings_cache
from app.storage.artifact_store import ArtifactStore
from app.storage.run_store import RunHandle, RUN_SUBDIRS


def commit(root: Path, message: str) -> str:
    git(root, 'add', '.')
    git(root, '-c', 'user.name=Test', '-c', 'user.email=test@localhost',
        '-c', 'commit.gpgsign=false', 'commit', '-m', message)
    return git(root, 'rev-parse', 'HEAD')


@pytest.fixture
def committed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[RunHandle, Path]]:
    monkeypatch.setenv('MARS_FOLDER_PROJECTS_REGISTRY', str(tmp_path / 'registry.json'))
    reset_settings_cache()
    project = open_folder(str(tmp_path / 'project'), create=True)
    source = tmp_path / 'source'
    source.mkdir()
    git(source, 'init', '-b', 'main')
    (source / 'main.py').write_text('VALUE = 1\n')
    (source / 'removed.py').write_text('REMOVE = True\n')
    (source / 'baseline').mkdir()
    (source / 'baseline/ref.py').write_text('PROTECTED = 1\n')
    commit(source, 'Actual source baseline')
    (project.metadata_root / 'repo_link.yaml').write_text(yaml.safe_dump({'repo_path': str(source),
        'read_only': True, 'allowed_paths': ['main.py', 'new.py', 'removed.py', 'baseline/'],
        'protected_paths': ['baseline/'], 'ignore_patterns': ['.git/', '.env*']}))
    run = RunHandle('maintainer-review', project.root / 'runs/maintainer-review', project.name,
        'Actual Git review', 'coding', '2026-10-13T00:00:00Z')
    for name in RUN_SUBDIRS:
        run.subdir(name).mkdir(parents=True, exist_ok=True)
    branch = prepare(source, run.root, run.project, run.run_id)
    (branch.root / 'main.py').write_text('VALUE = 2\n')
    (branch.root / 'new.py').write_text('NEW = True\n')
    (branch.root / 'removed.py').unlink()
    commit(branch.root, 'Actual maintainer repair')
    try:
        yield run, branch.root
    finally:
        reset_settings_cache()


def metadata(run: RunHandle) -> dict[str, object]:
    actual = describe_committed_delivery(run)
    return {'schema': 'code_spec.v1', 'project': run.project, 'agent': 'coding', 'target_lang': 'python',
        'baseline_compat': {'preserved': True}, 'files_changed': actual['files_changed'],
        'coding_backend': actual['coding_backend']}


def test_actual_commit_diff_and_net_file_types_can_be_independently_reviewed(committed: tuple[RunHandle, Path]) -> None:
    run, root = committed
    doc = metadata(run)
    actual = verify_git_delivery(run, doc)
    assert actual['files_changed'] == [{'path': 'main.py', 'type': 'modified'},
        {'path': 'new.py', 'type': 'added'}, {'path': 'removed.py', 'type': 'deleted'}]
    assert actual['coding_backend']['commit'] == git(root, 'rev-parse', 'HEAD')
    assert actual['coding_backend']['diff_sha256'] == hashlib.sha256(actual['canonical_diff'].encode()).hexdigest()
    assert verify_written_code(run, dumps(doc, 'Maintainer-authored committed delivery; no model output is fabricated.'))
    assert not (run.root / 'coding/tool_applications').exists()
    ref = ArtifactStore(run).write(text=dumps(doc, 'Actual diff proposed for review.'))
    view = completed_code_changes(run, project=run.project)
    assert view['source'] == 'verified_git_commit' and not view['approved']
    assert {item['path'] for item in view['items']} == {'main.py', 'new.py', 'removed.py'}
    assert all(item['source'] == 'verified_git_commit' for item in view['items'])
    detail = completed_code_changes(run, project=run.project, change_id=view['items'][0]['id'])
    assert detail['lines']
    assert ref.version == 'v1' and not (run.root / 'coding/code_spec.approved.md').exists()


@pytest.mark.parametrize('fault', ['wrong_commit', 'wrong_baseline', 'wrong_diff', 'missing_file', 'wrong_type'])
def test_forged_or_incomplete_git_declarations_are_rejected(committed: tuple[RunHandle, Path], fault: str) -> None:
    run, _ = committed
    doc = deepcopy(metadata(run))
    backend = doc['coding_backend']
    assert isinstance(backend, dict)
    files = doc['files_changed']
    assert isinstance(files, list)
    if fault == 'wrong_commit':
        backend['commit'] = backend['baseline_commit']
    elif fault == 'wrong_baseline':
        backend['baseline_commit'] = backend['commit']
    elif fault == 'wrong_diff':
        backend['diff_sha256'] = 'a' * 64
    elif fault == 'missing_file':
        files.pop()
    else:
        files[0]['type'] = 'added'
    with pytest.raises(ValueError):
        verify_git_delivery(run, doc)


def test_uncommitted_or_protected_changes_cannot_be_reviewed(committed: tuple[RunHandle, Path]) -> None:
    run, root = committed
    doc = metadata(run)
    (root / 'main.py').write_text('PENDING = True\n')
    with pytest.raises(ValueError, match='未提交'):
        verify_git_delivery(run, doc)
    commit(root, 'Actual later mutation')
    (root / 'baseline/ref.py').write_text('PROTECTED = 2\n')
    commit(root, 'Actual forbidden mutation for rejection')
    with pytest.raises(ValueError, match='baseline-protected'):
        describe_committed_delivery(run)


def test_committed_draft_cannot_execute_without_real_durable_approval(committed: tuple[RunHandle, Path]) -> None:
    run, root = committed
    doc = metadata(run)
    text = dumps(doc, 'Actual human-authored committed delivery awaiting review.')
    store = ArtifactStore(run)
    ref = store.write(text=text)
    # A caller-created approved-looking filename is deliberately insufficient.
    (run.root / 'coding/code_spec.approved.md').write_text(text)
    assert not git_delivery_is_approved(run, text)
    with pytest.raises(GitWorkspaceError, match='真实审核批准'):
        with research_branch_scope(run, 'execution'):
            pytest.fail('unapproved committed code must not be admitted')
    store.approve(ref)
    assert git_delivery_is_approved(run, text)
    head = git(root, 'rev-parse', 'HEAD')
    with research_branch_scope(run, 'execution'):
        pass  # Admission check only; no training process is launched by the test.
    assert git(root, 'rev-parse', 'HEAD') == head
    assert completed_code_changes(run, project=run.project)['approved']
    (root / 'main.py').write_text('VALUE = 3\n')
    commit(root, 'Actual new code after approval')
    with pytest.raises(ValueError, match='HEAD 不一致'):
        with research_branch_scope(run, 'execution'):
            pytest.fail('changed code must require a new review')


def test_approved_commit_history_remains_readable_after_genuine_report_generation(
    committed: tuple[RunHandle, Path],
) -> None:
    run, root = committed
    doc = metadata(run)
    text = dumps(doc, 'Actual committed repair proposed for durable human review.')
    store = ArtifactStore(run)
    store.approve(store.write(text=text))
    before = completed_code_changes(run, project=run.project)
    assert before['source'] == 'approved_git_history' and before['historical']
    assert not before['current_mismatch'] and before['execution_eligible'] is False
    report = root / 'experiments/actual-report'
    report.mkdir(parents=True)
    (report / 'summary.json').write_text('{"optimizer_steps":50}')
    head = git(root, 'rev-parse', 'HEAD')
    view = completed_code_changes(run, project=run.project)
    assert view['coding_backend'] == before['coding_backend']
    assert view['items'] == before['items']
    assert view['workspace'] == {'head': head, 'head_matches': True, 'clean': False,
                                 'tracked_changes': False, 'untracked_files': True}
    assert view['approved'] and view['current_mismatch'] and view['warnings']
    assert all(item['status'] == 'approved_history' for item in view['items'])
    detail = completed_code_changes(run, project=run.project, change_id=view['items'][0]['id'])
    assert detail['source'] == 'approved_git_history' and detail['execution_eligible'] is False
    assert git(root, 'rev-parse', 'HEAD') == head and (report / 'summary.json').is_file()
    with pytest.raises(ValueError, match='未提交'):
        verify_git_delivery(run, doc)
    with pytest.raises(ValueError, match='未提交'):
        with research_branch_scope(run, 'execution'):
            pytest.fail('browsing history cannot grant execution permission')


def test_approved_history_shows_original_git_objects_after_worktree_and_head_change(
    committed: tuple[RunHandle, Path],
) -> None:
    run, root = committed
    doc = metadata(run)
    text = dumps(doc, 'Actual durable reviewed code; later edits are not approved here.')
    store = ArtifactStore(run)
    store.approve(store.write(text=text))
    (root / 'main.py').write_text('VALUE = 3\n')
    modified = completed_code_changes(run, project=run.project)
    assert modified['current_mismatch'] and modified['workspace']['tracked_changes']
    assert modified['workspace']['head_matches']
    original_commit = modified['coding_backend']['commit']
    later = commit(root, 'Actual later change requiring independent review')
    historical = completed_code_changes(run, project=run.project)
    assert historical['coding_backend']['commit'] == original_commit != later
    assert historical['workspace']['head'] == later and not historical['workspace']['head_matches']
    assert historical['current_mismatch'] and historical['execution_eligible'] is False
    main = next(item for item in historical['items'] if item['path'] == 'main.py')
    detail = completed_code_changes(run, project=run.project, change_id=main['id'])
    additions = [row['text'] for row in detail['lines'] if row['kind'] == 'add']
    assert 'VALUE = 2' in additions and 'VALUE = 3' not in additions
    with pytest.raises(ValueError, match='HEAD 不一致'):
        verify_git_delivery(run, doc)


def test_unapproved_candidate_never_uses_historical_browsing_exception(
    committed: tuple[RunHandle, Path],
) -> None:
    run, root = committed
    text = dumps(metadata(run), 'Actual unapproved candidate remains subject to clean checkout review.')
    ArtifactStore(run).write(text=text)
    (run.root / 'coding/code_spec.approved.md').write_text(text)
    (root / 'untracked.py').write_text('UNREVIEWED = True\n')
    with pytest.raises(ValueError, match='真实、持久'):
        verify_approved_git_history(run, text)
    with pytest.raises(ValueError, match='未提交'):
        completed_code_changes(run, project=run.project)
