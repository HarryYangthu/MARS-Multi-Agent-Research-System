"""Repository browsing against real temporary files, without execution substitutes."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.bridge.code_repository import BrowsePolicy, CodeRepository, run_code_repository
from app.harness.tools.project_repo import ProjectRepo
from app.storage.run_store import RunStore


def browser(root: Path, *, allowed: tuple[str, ...] = ('',), ignored: tuple[str, ...] = ()) -> CodeRepository:
    return CodeRepository(ProjectRepo('test', root, 'local_path', True, allowed, (), ignored),
                          BrowsePolicy(directory_page_size=2, file_page_lines=2, max_file_bytes=1000))


def test_complete_directory_pagination_includes_unchanged_files(tmp_path: Path) -> None:
    (tmp_path / 'libs').mkdir()
    for name in ['README.md', 'train.py', '配置.yaml']:
        (tmp_path / name).write_text('unchanged\n')
    repo = browser(tmp_path)
    first = repo.directory()
    second = repo.directory(offset=first['next_offset'], token=first['repository_token'])
    assert [item['path'] for item in first['entries'] + second['entries']] == ['libs', 'README.md', 'train.py', '配置.yaml']
    assert second['next_offset'] is None and first['total'] == 4
    assert first['source'] == 'project_current' and first['read_only'] is True


def test_complete_file_pages_keep_unicode_and_final_line(tmp_path: Path) -> None:
    content = '第一行\r\n\n第三行\nfinal without newline'
    (tmp_path / '代码.tsx').write_bytes(content.encode())
    repo = browser(tmp_path)
    first = repo.file('代码.tsx')
    second = repo.file('代码.tsx', start=first['next_start'], version=first['version'], token=repo.token)
    assert ''.join(first['lines'] + second['lines']) == content
    assert second['next_start'] is None and second['total_lines'] == 4
    assert (tmp_path / '代码.tsx').read_bytes() == content.encode()
    (tmp_path / 'empty').touch()
    assert repo.file('empty')['lines'] == []


def test_file_changes_and_repository_rebinding_cannot_mix_pages(tmp_path: Path) -> None:
    target = tmp_path / 'code.py'; target.write_text('a\nb\nc\n')
    repo = browser(tmp_path)
    first = repo.file('code.py')
    target.write_text('changed\nb\nc\n')
    with pytest.raises(ValueError, match='已更新'):
        repo.file('code.py', start=2, version=first['version'])
    with pytest.raises(ValueError, match='关联已变更'):
        repo.directory(token='old binding')
    with pytest.raises(ValueError, match='关联已变更'):
        repo.file('code.py', token='old binding')


def test_exclusions_are_enforced_for_directory_and_direct_file_access(tmp_path: Path) -> None:
    for directory in ['src', 'private', '.git', 'node_modules', '.env-secrets']:
        (tmp_path / directory).mkdir()
        (tmp_path / directory / 'secret.py').write_text('not-for-display')
    (tmp_path / 'src/visible.py').write_text('visible')
    (tmp_path / 'src/.env').write_text('SECRET=value')
    repo = browser(tmp_path, allowed=('src/visible.py',), ignored=('private/',))
    assert [item['path'] for item in repo.directory()['entries']] == ['src']
    assert [item['path'] for item in repo.directory('src')['entries']] == ['src/visible.py']
    for name in ['private/secret.py', '.git/secret.py', 'src/.env', 'src/secret.py', '.env-secrets/secret.py']:
        with pytest.raises(ValueError):
            repo.file(name)
    with pytest.raises(ValueError):
        repo.directory('private')
    assert browser(tmp_path, ignored=('src/',)).directory('')['total'] == 1  # private only
    with pytest.raises(ValueError):
        browser(tmp_path, ignored=('src/',)).file('src/visible.py')


def test_no_symlink_hardlink_special_file_or_path_escape(tmp_path: Path) -> None:
    root = tmp_path / 'repo'; root.mkdir()
    outside = tmp_path / 'secret'; outside.write_text('PRIVATE')
    (root / 'linked.py').symlink_to(outside)
    (root / 'linked-dir').symlink_to(tmp_path, target_is_directory=True)
    os.link(outside, root / 'hard.py')
    os.mkfifo(root / 'pipe.py')
    repo = browser(root)
    assert repo.directory()['entries'] == []
    for name in ['../secret', str(outside), 'linked.py', 'hard.py', 'pipe.py', 'linked-dir/secret']:
        with pytest.raises((ValueError, OSError)):
            repo.file(name)
    with pytest.raises(OSError):
        repo.directory('linked-dir')
    with pytest.raises(ValueError):
        browser(Path('/'))


def test_binary_large_and_invalid_paging_fail_explicitly(tmp_path: Path) -> None:
    (tmp_path / 'binary.bin').write_bytes(b'a\x00b')
    (tmp_path / 'non-utf.txt').write_bytes(b'\xff')
    (tmp_path / 'large.py').write_bytes(b'x' * 1001)
    repo = browser(tmp_path)
    for name in ['binary.bin', 'non-utf.txt', 'large.py']:
        with pytest.raises(ValueError):
            repo.file(name)
    with pytest.raises(ValueError):
        repo.directory(offset=-1)
    with pytest.raises(ValueError):
        repo.file('large.py', start=-1)


def test_run_project_mismatch_is_rejected_before_loading_repository(tmp_path: Path) -> None:
    run = RunStore(tmp_path / 'runs').create(task='browse', project='test')
    with pytest.raises(ValueError, match='项目不匹配'):
        run_code_repository(run, project='other')
