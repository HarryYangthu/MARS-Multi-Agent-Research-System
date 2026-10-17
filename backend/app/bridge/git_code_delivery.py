"""Independent evidence for explicitly reviewed, already committed code delivery.

Git evidence does not synthesize tool writes and never creates an approval.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import yaml

from app.harness.runtime.git_runtime import run_git
from app.harness.tools.git_branch import (
    bind_git_branch, branch_owner, current_git_branch, git, saved_branch,
)
from app.harness.tools.project_repo import ProjectRepo, load_project_repo, resolve_allowed_path
from app.settings import repo_root
from app.storage.run_store import RunHandle


@contextmanager
def _repository(run: RunHandle) -> Iterator[ProjectRepo]:
    repo = load_project_repo(run.project)
    if repo.scope is not None:
        raise ValueError('已冻结研究合同不能改为外部 Git 提交交付')
    branch = current_git_branch(run.project, run.run_id) or saved_branch(repo.root, run.root, run.project, run.run_id)
    if branch is None or not branch.source_repo_path:
        raise ValueError('Git 提交交付需要本任务已登记的独立研究代码目录')
    owner = branch_owner(branch.root)
    if owner is None or owner[0] != branch or owner[1].resolve() != run.root.resolve():
        raise ValueError('Git 提交不属于当前任务的代码目录')
    bound = current_git_branch(run.project, run.run_id)
    with nullcontext(branch) if bound is not None else bind_git_branch(branch):
        yield replace(repo, root=branch.root, read_only=False, git_branch=branch)


def _commit(value: Any, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r'[0-9a-f]{40,64}', value) is None:
        raise ValueError('Git 交付必须给出明确的真实提交 SHA：' + field)
    return value


def _changes(repo: ProjectRepo, baseline: str, commit: str, *, require_worktree_files: bool = True) -> list[dict[str, str]]:
    fields = git(repo.root, 'diff', '--no-ext-diff', '--no-textconv', '--no-renames',
                 '--name-status', '-z', baseline, commit, '--').split('\0')
    if fields and fields[-1] == '':
        fields.pop()
    if len(fields) % 2:
        raise ValueError('Git 改动清单无法完整解析')
    kinds = {'A': 'added', 'M': 'modified', 'D': 'deleted'}
    rows: list[dict[str, str]] = []
    for index in range(0, len(fields), 2):
        status, name = fields[index:index + 2]
        if status not in kinds:
            raise ValueError('Git 交付包含不支持的文件类型或移动，未审核')
        resolve_allowed_path(repo, name, for_write=True, require_exists=require_worktree_files and status != 'D')
        # Symlinks and submodules do not establish a regular-file code delivery.
        for revision in (baseline, commit):
            entry = git(repo.root, 'ls-tree', '-z', revision, '--', name)
            if entry and entry.split(' ', 1)[0] not in {'100644', '100755'}:
                raise ValueError('Git 交付只能包含普通代码文件：' + name)
        rows.append({'path': name, 'type': kinds[status]})
    return sorted(rows, key=lambda row: row['path'])


def _immutable_description(repo: ProjectRepo, baseline: str, commit: str, *, historical: bool = False) -> dict[str, Any]:
    branch = repo.git_branch
    assert branch is not None
    if baseline != branch.baseline_commit:
        raise ValueError('Git 交付不得替换本任务记录的冻结基线')
    for value in (baseline, commit):
        git(repo.root, 'cat-file', '-e', value + '^{commit}')
    git(repo.root, 'merge-base', '--is-ancestor', baseline, commit)
    git(repo.root, 'merge-base', '--is-ancestor', branch.initial_commit, commit)
    git(repo.root, 'merge-base', '--is-ancestor', commit, 'HEAD')
    rows = _changes(repo, baseline, commit, require_worktree_files=not historical)
    diff = run_git(('-C', str(repo.root), 'diff', '--no-ext-diff', '--no-textconv', '--no-renames',
                    '--binary', '--full-index', baseline, commit, '--')).stdout
    raw = yaml.safe_load((repo_root() / 'configs/code_review.yaml').read_text())['code_review']
    if len(diff.encode('utf-8')) > int(raw['max_record_bytes']):
        raise ValueError('Git 差异超过完整核验上限，不能用截断片段审核')
    return {'coding_backend': {'type': 'git_commit', 'commit': commit,
                'baseline_commit': baseline, 'diff_sha256': hashlib.sha256(diff.encode('utf-8')).hexdigest()},
            'files_changed': rows, 'canonical_diff': diff, 'repository': str(repo.root), 'branch': branch.branch}


def describe_committed_delivery(run: RunHandle) -> dict[str, Any]:
    """Read actual immutable Git objects; return proposed review evidence only."""
    with _repository(run) as repo:
        branch = repo.git_branch
        assert branch is not None
        if git(repo.root, 'status', '--porcelain=v1', '-z', '--untracked-files=all'):
            raise ValueError('Git 提交交付要求代码目录无未提交或未登记文件；未审核')
        head = git(repo.root, 'rev-parse', 'HEAD')
        actual = _immutable_description(repo, branch.baseline_commit, head)
        if git(repo.root, 'rev-parse', 'HEAD') != head or git(repo.root, 'status', '--porcelain=v1', '-z', '--untracked-files=all'):
            raise ValueError('核验期间代码发生变化，未审核')
        return actual


def _descriptor(run: RunHandle, metadata: dict[str, Any]) -> dict[str, Any]:
    if metadata.get('project') != run.project or metadata.get('schema') != 'code_spec.v1' or metadata.get('agent') != 'coding':
        raise ValueError('Git 交付文档身份与当前任务或项目不一致')
    descriptor = metadata.get('coding_backend')
    if not isinstance(descriptor, dict) or descriptor.get('type') != 'git_commit':
        raise ValueError('不是显式的 Git 提交交付')
    _commit(descriptor.get('commit'), 'commit')
    _commit(descriptor.get('baseline_commit'), 'baseline_commit')
    fingerprint = descriptor.get('diff_sha256')
    if not isinstance(fingerprint, str) or re.fullmatch(r'[0-9a-f]{64}', fingerprint) is None:
        raise ValueError('Git 交付的完整差异 SHA-256 无效')
    return descriptor


def _verify_manifest(metadata: dict[str, Any], actual: dict[str, Any]) -> None:
    descriptor = metadata['coding_backend']
    if descriptor['baseline_commit'] != actual['coding_backend']['baseline_commit']:
        raise ValueError('Git 交付不得替换本任务记录的冻结基线')
    if descriptor['diff_sha256'] != actual['coding_backend']['diff_sha256']:
        raise ValueError('Git 交付差异与实际提交不一致，未审核')
    rows = metadata.get('files_changed')
    if not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get('path'), str)
                                        or not isinstance(row.get('type'), str) for row in rows):
        raise ValueError('Git 交付文件清单无效')
    declared = {row['path']: row['type'] for row in rows}
    if len(declared) != len(rows) or declared != {row['path']: row['type'] for row in actual['files_changed']}:
        raise ValueError('Git 交付文件名单与改动类型必须和真实完整差异一致')


def verify_git_delivery(run: RunHandle, metadata: dict[str, Any]) -> dict[str, Any]:
    """The declaration must exactly identify this checkout's real net changes."""
    descriptor = _descriptor(run, metadata)
    actual = describe_committed_delivery(run)
    if descriptor['commit'] != actual['coding_backend']['commit']:
        raise ValueError('Git 交付提交与当前研究分支 HEAD 不一致，未审核')
    _verify_manifest(metadata, actual)
    return actual


def verify_approved_git_history(run: RunHandle, text: str) -> dict[str, Any]:
    """Browse an approved immutable commit; this never grants execution admission."""
    if not git_delivery_is_approved(run, text):
        raise ValueError('历史提交展示需要本任务真实、持久的批准记录')
    from app.harness.schema.frontmatter_parser import parse
    metadata = parse(text).metadata
    descriptor = _descriptor(run, metadata)
    with _repository(run) as repo:
        actual = _immutable_description(repo, descriptor['baseline_commit'], descriptor['commit'], historical=True)
        _verify_manifest(metadata, actual)
        head = git(repo.root, 'rev-parse', 'HEAD')
        status = git(repo.root, 'status', '--porcelain=v1', '-z', '--untracked-files=all')
        records = [record for record in status.split('\0') if record]
        workspace = {'head': head, 'head_matches': head == descriptor['commit'], 'clean': not status,
                     'tracked_changes': any(not record.startswith('?? ') for record in records),
                     'untracked_files': any(record.startswith('?? ') for record in records)}
        return {**actual, 'historical': True, 'source': 'approved_git_history', 'workspace': workspace,
                'current_mismatch': not workspace['head_matches'] or not workspace['clean'],
                'execution_eligible': False}


def git_delivery_is_approved(run: RunHandle, text: str) -> bool:
    """A draft, commit or approved-looking filename is not execution permission."""
    path = run.root / 'coding/.approvals/code_spec'
    records = sorted(path.glob('*.json')) if path.is_dir() else []
    if not records:
        return False
    record = json.loads(records[-1].read_text(encoding='utf-8'))
    version = record.get('source_version')
    if (record.get('schema') != 'artifact_approval.v1' or record.get('run_id') != run.run_id
            or record.get('agent_dir') != 'coding' or record.get('stem') != 'code_spec'
            or record.get('sequence') != len(records) or not isinstance(version, str)
            or re.fullmatch(r'v[1-9]\d*', version) is None
            or records[-1].name != f'{len(records):020d}.json'):
        raise ValueError('Git 交付的批准记录无法校验')
    source = run.root / 'coding' / ('code_spec.' + version + '.md')
    if not source.is_file() or source.is_symlink() or source.read_text(encoding='utf-8') != text:
        return False
    return bool(record.get('source_sha256') == hashlib.sha256(text.encode('utf-8')).hexdigest())
