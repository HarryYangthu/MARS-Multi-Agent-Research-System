"""Diff projection and actual local code tool receipts; no provider substitutes."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.bridge.code_changes import code_changes, parse_diff
from app.harness.tools.registry import ToolContext, get_registry
from app.storage.run_store import RunStore


def test_diff_line_numbers_headers_and_multiple_files() -> None:
    text = ('diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n'
            '@@ -4,2 +4,3 @@\n-old\n+new\n+++ looks like a header\n keep\n'
            'diff --git a/deleted.txt b/deleted.txt\n--- a/deleted.txt\n+++ /dev/null\n'
            '@@ -1 +0,0 @@\n-gone\n'
            '--- /dev/null\n+++ b/中文 文件.py\n@@ -0,0 +1 @@\n+created\n')
    files = parse_diff(text)
    assert [item['path'] for item in files] == ['a.py', 'deleted.txt', '中文 文件.py']
    assert (files[0]['additions'], files[0]['deletions']) == (2, 1)
    added = [row for row in files[0]['lines'] if row['kind'] == 'add']
    assert added[1] == {'kind': 'add', 'text': '++ looks like a header', 'old_line': None, 'new_line': 5}
    assert files[1]['change'] == 'deleted' and files[2]['change'] == 'added'


def test_rename_binary_and_quoted_paths_are_visible() -> None:
    files = parse_diff('diff --git a/old.py b/new.py\nsimilarity index 100%\nrename from old.py\nrename to new.py\n'
                       'diff --git a/img.bin b/img.bin\nBinary files a/img.bin and b/img.bin differ\n'
                       'diff --git "a/space name.py" "b/space name.py"\nold mode 100644\nnew mode 100755\n')
    assert [f['path'] for f in files] == ['new.py', 'img.bin', 'space name.py']
    assert files[0]['change'] == 'renamed' and files[1]['additions'] == 0


def test_malformed_hunk_keeps_all_proposed_lines_and_exposes_warning() -> None:
    item = parse_diff('--- /dev/null\n+++ b/new.py\n@@ -0,0 +1,1 @@\n+first\n+extra\n')[0]
    assert item['additions'] == 2 and item['warning']
    assert [line['new_line'] for line in item['lines'] if line['kind'] == 'add'] == [1, 2]


@pytest.mark.asyncio
async def test_real_write_receipt_has_exact_diff_without_reading_current_repo(tmp_path: Path) -> None:
    run = RunStore(tmp_path / 'runs').create(task='code-review', project='code-review-local-test')
    repo = tmp_path / 'repo'; repo.mkdir()
    target = repo / 'candidate.py'; target.write_text('VALUE = 1\nkeep = True\n')
    ctx = ToolContext(run_id=run.run_id, project=run.project, agent='coding',
                      project_repo_root=str(repo), extra={'run_root': str(run.root)})
    result = await get_registry().dispatch('code.write_file', {'path': 'candidate.py', 'content': 'VALUE = 2\nkeep = True\n'}, ctx)
    assert result.ok and target.read_text().startswith('VALUE = 2')
    target.write_text('Unrelated later edit must not enter the archived diff.\n')
    view = code_changes(run, project=run.project)
    assert not view['warnings'] and len(view['items']) == 1
    item = view['items'][0]
    assert item['status'] == 'applied' and (item['additions'], item['deletions']) == (1, 1)
    detail = code_changes(run, project=run.project, change_id=item['id'])
    assert [line['text'] for line in detail['lines'] if line['kind'] == 'delete'] == ['VALUE = 1']
    assert [line['text'] for line in detail['lines'] if line['kind'] == 'add'] == ['VALUE = 2']
    assert 'Unrelated later edit' not in json.dumps(detail)
    assert 'lines' not in item
    with pytest.raises(ValueError, match='项目'):
        code_changes(run, project='other')


@pytest.mark.asyncio
async def test_real_refused_write_keeps_content_without_fabricated_stats(tmp_path: Path) -> None:
    run = RunStore(tmp_path / 'runs').create(task='refused-code', project='code-review-local-test')
    ctx = ToolContext(run_id=run.run_id, project=run.project, agent='coding', extra={'run_root': str(run.root)})
    result = await get_registry().dispatch('code.write_file', {'path': 'candidate.py', 'content': 'VALUE = 3\n'}, ctx)
    assert not result.ok
    view = code_changes(run, project=run.project)
    item = view['items'][0]
    assert item['status'] == 'not_applied' and item['additions'] is None
    detail = code_changes(run, project=run.project, change_id=item['id'])
    assert detail['change'] == 'content' and detail['lines'][0]['text'] == 'VALUE = 3'


def test_archived_patch_does_not_require_final_artifact(tmp_path: Path) -> None:
    run = RunStore(tmp_path / 'runs').create(task='patch-preview', project='code-review-local-test')
    path = run.root / 'coding/patch.v1.diff'
    path.write_text('--- a/value.py\n+++ b/value.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n')
    before = path.read_bytes()
    view = code_changes(run, project=run.project)
    assert view['items'][0]['status'] == 'proposed' and view['items'][0]['additions'] == 1
    assert path.read_bytes() == before
    with pytest.raises(KeyError):
        code_changes(run, project=run.project, change_id='../../outside')


def test_symlink_corrupt_and_oversized_records_are_not_read(tmp_path: Path) -> None:
    run = RunStore(tmp_path / 'runs').create(task='unsafe-records', project='code-review-local-test')
    secret = tmp_path / 'outside'; secret.write_text('PRIVATE_EXTERNAL_CONTENT')
    (run.root / 'coding/patch.v1.diff').symlink_to(secret)
    directory = run.root / 'coding/tool_applications'; directory.mkdir()
    (directory / 'broken.json').write_text('{')
    (directory / 'large.json').write_bytes(b' ' * 2_097_153)
    view = code_changes(run, project=run.project)
    assert not view['items'] and len(view['warnings']) == 3
    assert 'PRIVATE_EXTERNAL_CONTENT' not in json.dumps(view)


def test_large_diff_preview_is_explicitly_bounded(tmp_path: Path) -> None:
    run = RunStore(tmp_path / 'runs').create(task='large-preview', project='code-review-local-test')
    (run.root / 'coding/patch.v1.diff').write_text('--- /dev/null\n+++ b/big.txt\n@@ -0,0 +1,3000 @@\n' + '+value\n' * 3000)
    item = code_changes(run, project=run.project)['items'][0]
    assert item['additions'] == 3000 and item['truncated']
    detail = code_changes(run, project=run.project, change_id=item['id'])
    assert len(detail['lines']) == 2000 and detail['truncated']
