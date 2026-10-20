"""Freeze pending source-code inputs without writing the source Git index."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatchcase
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
from typing import Any
import uuid

from app.harness.agent_loop.trace import atomic_json, digest
from app.harness.discovery.snapshots import SnapshotPolicy
from app.harness.project_storage import storage_policy
from app.harness.runtime.git_runtime import run_git
from app.harness.runtime.project_scope import forbidden_source_path, safe_scope_path


def _git(root: Path, *args: str) -> str:
    return run_git(('-C', str(root), '--no-optional-locks', *args)).stdout.rstrip('\r\n')


def _sha(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _index_hash(root: Path) -> str | None:
    path = Path(_git(root, 'rev-parse', '--path-format=absolute', '--git-path', 'index'))
    return _sha(path) if path.is_file() else None


def _signature(path: Path) -> tuple[int, int, int, int, int, int] | None:
    try:
        info = path.lstat()
        return info.st_size, info.st_mtime_ns, info.st_ino, info.st_dev, info.st_mode, info.st_ctime_ns
    except FileNotFoundError:
        return None


@dataclass(frozen=True)
class SourceSnapshot:
    root: Path
    manifest: dict[str, Any]

    @property
    def identity(self) -> str:
        return digest(self.manifest)


def _directory(run_root: Path) -> Path:
    return run_root / 'input/research_source_snapshot'


def load_source_snapshot(source: Path, run_root: Path, project: str, run_id: str,
                         *, verify_files: bool = False) -> SourceSnapshot | None:
    root = _directory(run_root)
    if not root.exists():
        return None
    path = safe_scope_path(root, 'manifest.json', must_exist=True)
    raw = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(raw, dict) or raw.get('schema_id') != 'research.source_snapshot.v1'
            or raw.get('project') != project or raw.get('run_id') != run_id
            or raw.get('source_repo_path') != str(source.resolve()) or not isinstance(raw.get('files'), list)):
        raise ValueError('源代码输入快照身份无法核验；已保存副本未覆盖。')
    snapshot = SourceSnapshot(root, raw)
    if verify_files:
        for row in raw['files']:
            if row['sha256'] is not None:
                copied = safe_scope_path(root / 'files', row['path'], must_exist=True)
                if _sha(copied) != row['sha256']:
                    raise ValueError('源代码输入快照内容已变化；未重新读取其他版本。')
    return snapshot


def snapshot_initial_commit(snapshot: SourceSnapshot, run_root: Path) -> str:
    path = run_root / 'input/research_source_snapshot.json'
    if not path.exists():
        return str(snapshot.manifest['baseline_commit'])
    raw = json.loads(safe_scope_path(run_root, 'input/research_source_snapshot.json', must_exist=True).read_text())
    if (raw.get('schema_id') != 'research.source_snapshot_binding.v1'
            or raw.get('snapshot_sha256') != snapshot.identity
            or raw.get('project') != snapshot.manifest['project'] or raw.get('run_id') != snapshot.manifest['run_id']
            or raw.get('baseline_commit') != snapshot.manifest['baseline_commit']):
        raise ValueError('源代码快照绑定无法核验；未更换任务起点。')
    return str(raw['initial_commit'])


def _selection(root: Path) -> tuple[list[str], list[dict[str, str]]]:
    from app.harness.tools.project_repo import TEXT_SUFFIXES
    changed = set(filter(None, _git(root, 'diff', '--no-ext-diff', '--no-textconv', '--name-only',
                                    '--no-renames', '-z', 'HEAD', '--').split('\0')))
    untracked = set(filter(None, _git(root, 'ls-files', '--others', '--exclude-standard', '-z').split('\0')))
    excluded = [str(value) for value in storage_policy()['code_excludes']]
    selected: list[str] = []
    skipped: list[dict[str, str]] = []
    for name in sorted(changed | untracked):
        parts = Path(name).parts
        reason = ''
        if forbidden_source_path(name) or any(any(fnmatchcase(part, pattern) for part in parts) for pattern in excluded):
            reason = 'excluded_runtime_data_or_control'
        elif name in untracked and Path(name).suffix.lower() not in TEXT_SUFFIXES:
            reason = 'untracked_non_source_file'
        else:
            try:
                path = safe_scope_path(root, name)
                if path.exists() and not stat.S_ISREG(path.stat().st_mode):
                    reason = 'non_regular_source_file'
            except ValueError:
                reason = 'unsafe_source_path'
        if reason:
            if name in changed and reason in {'non_regular_source_file', 'unsafe_source_path'}:
                raise ValueError('source_snapshot_input_unsupported: 已修改的源码不是可独立复制的普通文件；没有运行旧版本或修改源仓。')
            skipped.append({'path': name, 'reason': reason})
        else:
            selected.append(name)
    return selected, skipped


def capture_source_snapshot(source: Path, run_root: Path, project: str, run_id: str,
                            baseline_branch: str, baseline_commit: str) -> SourceSnapshot | None:
    existing = load_source_snapshot(source, run_root, project, run_id, verify_files=True)
    if existing is not None:
        return existing
    head = _git(source, 'rev-parse', 'HEAD')
    branch = _git(source, 'branch', '--show-current')
    index = _index_hash(source)
    if head != baseline_commit or branch != baseline_branch:
        raise ValueError('源代码版本在建立快照前已变化，请重试；没有修改源仓。')
    names, skipped = _selection(source)
    if not names and not skipped:
        return None
    policy = SnapshotPolicy(allowed_paths=('*',))
    parent = run_root / 'input'
    temporary = parent / ('.source-snapshot-' + uuid.uuid4().hex)
    temporary.mkdir(parents=True)
    (temporary / 'files').mkdir()
    signatures: dict[str, tuple[int, int, int, int, int, int] | None] = {}
    records: list[dict[str, Any]] = []
    total = 0
    try:
        for name in names:
            path = safe_scope_path(source, name)
            before = _signature(path)
            if before is not None and (before[0] > policy.max_file_bytes
                    or total + before[0] > policy.max_total_bytes or len(records) >= policy.max_files):
                # A source-code input cannot silently fall back to older HEAD
                # bytes. Large data/runtime inputs were excluded by selection.
                raise ValueError('source_snapshot_input_too_large: 源码输入超出快照大小限制；没有运行旧版本或修改源仓。')
            signatures[name] = before
            checksum: str | None = None
            mode = '100644'
            if before is not None:
                target = temporary / 'files' / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
                checksum = _sha(target)
                if _signature(path) != before or _sha(path) != checksum:
                    raise ValueError('源代码在复制时已变化，请重试；没有修改源仓。')
                mode = '100755' if os.name != 'nt' and before[4] & 0o111 else '100644'
                target.chmod(0o444)
                total += before[0]
            records.append({'path': name, 'sha256': checksum, 'bytes': 0 if before is None else before[0], 'mode': mode})
        if (_git(source, 'rev-parse', 'HEAD') != head or _git(source, 'branch', '--show-current') != branch
                or _index_hash(source) != index or _selection(source) != (names, skipped)
                or any(_signature(source / name) != value for name, value in signatures.items())
                or any(row['sha256'] is not None and _sha(source / row['path']) != row['sha256'] for row in records)):
            raise ValueError('源代码在建立快照期间已变化，请重试；没有修改源仓。')
        manifest = {'schema_id': 'research.source_snapshot.v1', 'project': project, 'run_id': run_id,
            'source_repo_path': str(source.resolve()), 'source_branch': branch, 'baseline_branch': baseline_branch,
            'baseline_commit': baseline_commit, 'source_head_commit': head, 'source_index_sha256': index,
            'captured_at': datetime.now(timezone.utc).isoformat(), 'files': records, 'excluded': skipped}
        atomic_json(temporary / 'manifest.json', manifest)
        (temporary / 'manifest.json').chmod(0o444)
        temporary.rename(_directory(run_root))
        return SourceSnapshot(_directory(run_root), manifest)
    except BaseException:
        for copied in (temporary / 'files').rglob('*'):
            if copied.is_file() and not copied.is_symlink():
                copied.chmod(0o600)
        if (temporary / 'manifest.json').exists():
            (temporary / 'manifest.json').chmod(0o600)
        shutil.rmtree(temporary)
        raise


def apply_source_snapshot(snapshot: SourceSnapshot, checkout: Path) -> str:
    """Write only this independent checkout/index; Git filters/hooks never run."""
    manifest = snapshot.manifest
    changes = manifest['files']
    _git(checkout, 'config', 'core.autocrlf', 'false')
    for row in changes:
        name = row['path']
        target = safe_scope_path(checkout, name)
        if row['sha256'] is None:
            target.unlink(missing_ok=True)
            _git(checkout, 'update-index', '--force-remove', '--', name)
        else:
            copied = safe_scope_path(snapshot.root / 'files', name, must_exist=True)
            if _sha(copied) != row['sha256']:
                raise ValueError('已冻结的源代码快照内容已变化。')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(copied, target)
            target.chmod(0o755 if row['mode'] == '100755' else 0o644)
            object_id = _git(checkout, 'hash-object', '--no-filters', '-w', '--', str(copied))
            _git(checkout, 'update-index', '--add', '--cacheinfo', row['mode'], object_id, name)
    tree = _git(checkout, 'write-tree')
    if tree == _git(checkout, 'rev-parse', manifest['baseline_commit'] + '^{tree}'):
        return str(manifest['baseline_commit'])
    stamp = str(int(datetime.fromisoformat(manifest['captured_at']).timestamp())) + ' +0000'
    env = {**os.environ, 'GIT_AUTHOR_NAME': 'MARS', 'GIT_AUTHOR_EMAIL': 'mars@localhost',
        'GIT_COMMITTER_NAME': 'MARS', 'GIT_COMMITTER_EMAIL': 'mars@localhost',
        'GIT_AUTHOR_DATE': stamp, 'GIT_COMMITTER_DATE': stamp}
    commit = run_git(('-C', str(checkout), '-c', 'commit.gpgsign=false', 'commit-tree', tree, '-p', manifest['baseline_commit'],
                      '-m', 'Research source input snapshot: ' + manifest['run_id']), environment=env).stdout.strip()
    _git(checkout, 'update-ref', 'HEAD', commit, manifest['baseline_commit'])
    return commit


def save_source_snapshot_binding(snapshot: SourceSnapshot, run_root: Path, initial_commit: str) -> None:
    atomic_json(run_root / 'input/research_source_snapshot.json', {
        'schema_id': 'research.source_snapshot_binding.v1', 'project': snapshot.manifest['project'],
        'run_id': snapshot.manifest['run_id'], 'source_repo_path': snapshot.manifest['source_repo_path'],
        'baseline_commit': snapshot.manifest['baseline_commit'], 'initial_commit': initial_commit,
        'snapshot_sha256': snapshot.identity, 'manifest_ref': 'input/research_source_snapshot/manifest.json',
        'files': snapshot.manifest['files'], 'excluded': snapshot.manifest['excluded'],
        'source_head_commit': snapshot.manifest['source_head_commit'],
        'source_index_sha256': snapshot.manifest['source_index_sha256'],
        'source_worktree_dirty': True, 'snapshot_is_agent_code_change': False, 'source_modified': False})
