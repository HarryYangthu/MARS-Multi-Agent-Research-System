"""Read-only code review from archived tool receipts, never the live baseline."""
from __future__ import annotations

import ast
import difflib
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from app.settings import repo_root
from app.storage.run_store import RunHandle


def _path_label(value: str) -> str:
    value = value.split('\t', 1)[0].strip()
    if value.startswith('"'):
        try:
            decoded = ast.literal_eval(value)
            if isinstance(decoded, str):
                value = decoded
                try:
                    value = value.encode('latin1').decode('utf-8')
                except UnicodeError:
                    pass
        except (SyntaxError, ValueError):
            pass
    return value[2:] if value.startswith(('a/', 'b/')) else value


def parse_diff(text: str) -> list[dict[str, Any]]:
    """Project unified hunks, preserving line numbers and metadata-only changes."""
    files: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    old = new = old_left = new_left = 0
    old_path = ''
    in_hunk = False
    for line in text.splitlines():
        if line.startswith('diff --git ') or (line.startswith('--- ') and old_left == new_left == 0 and current is not None and current.get('header_complete')):
            if current is not None and (old_left or new_left):
                current['warning'] = '补丁行数与头部不一致，仅供查看。'
            current = None
        if current is None:
            current = {'path': '', 'additions': 0, 'deletions': 0, 'lines': [], 'change': 'modified'}
            files.append(current)
            old_left = new_left = 0
            old_path = ''
            in_hunk = False
        match = re.match(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', line)
        if match:
            if old_left or new_left:
                current['warning'] = '补丁行数与头部不一致，仅供查看。'
            old, new = int(match[1]), int(match[3])
            old_left = int(match[2]) if match[2] is not None else 1
            new_left = int(match[4]) if match[4] is not None else 1
            in_hunk = True
            current['lines'].append({'kind': 'hunk', 'text': line, 'old_line': None, 'new_line': None})
        elif in_hunk and line[:1] in {'+', '-', ' '}:
            kind = {'+': 'add', '-': 'delete', ' ': 'context'}[line[0]]
            if (kind != 'add' and not old_left) or (kind != 'delete' and not new_left):
                current['warning'] = '补丁行数与头部不一致，仅供查看。'
            current['lines'].append({'kind': kind, 'text': line[1:],
                                     'old_line': old if kind != 'add' else None,
                                     'new_line': new if kind != 'delete' else None})
            if kind != 'add':
                old += 1
                old_left = max(0, old_left - 1)
            if kind != 'delete':
                new += 1
                new_left = max(0, new_left - 1)
            if kind == 'add':
                current['additions'] += 1
            elif kind == 'delete':
                current['deletions'] += 1
        elif line.startswith('--- '):
            old_path = _path_label(line[4:])
            current['path'] = old_path
        elif line.startswith('+++ '):
            path = _path_label(line[4:])
            current['path'] = old_path if path == '/dev/null' else path
            current['change'] = 'added' if old_path == '/dev/null' else 'deleted' if path == '/dev/null' else 'modified'
            current['header_complete'] = True
        elif line.startswith('rename to '):
            current['path'] = _path_label(line[10:])
            current['change'] = 'renamed'
            current['lines'].append({'kind': 'meta', 'text': line, 'old_line': None, 'new_line': None})
        else:
            if line.startswith('diff --git '):
                # Standard git headers; +++/rename to is authoritative when present.
                tail = line.removeprefix('diff --git ')
                if ' b/' in tail:
                    current['path'] = tail.rsplit(' b/', 1)[1]
                elif ' "b/' in tail:
                    current['path'] = _path_label('"b/' + tail.rsplit(' "b/', 1)[1])
            current['lines'].append({'kind': 'meta', 'text': line, 'old_line': None, 'new_line': None})
    if current is not None and (old_left or new_left):
        current['warning'] = '补丁行数与头部不一致，仅供查看。'
    for item in files:
        item.pop('header_complete', None)
    return [item for item in files if item['path'] and item['path'] != '/dev/null']


def _read(path: Path, root: Path, limit: int) -> str:
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('record escapes run')
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('record too large')
    return raw.decode('utf-8')


def _before(run: RunHandle, record: dict[str, Any], path: str, limit: int) -> str | None:
    ref = record.get('rollback_ref')
    if not isinstance(ref, str) or not ref:
        return None
    target = Path(ref)
    if not target.is_absolute():
        target = run.root / target
    data = json.loads(_read(target, run.root, limit))
    if (data.get('run_id') != run.run_id or data.get('project') != run.project
            or data.get('schema') != 'tool_rollback.v1' or data.get('tool') != record['tool']):
        raise ValueError('rollback identity mismatch')
    for snapshot in data['snapshots']:
        if snapshot['path'] == path:
            content = snapshot['content']
            if not isinstance(content, str):
                raise ValueError('invalid snapshot')
            expected = 'sha256:' + hashlib.sha256(content.encode()).hexdigest() if snapshot['existed'] else ''
            if snapshot['sha256'] != expected:
                raise ValueError('snapshot hash mismatch')
            return content
    return None


def code_changes(run: RunHandle, *, project: str, change_id: str | None = None) -> dict[str, Any]:
    if run.project != project:
        raise ValueError('任务所属项目不匹配')
    policy = yaml.safe_load((repo_root() / 'configs/code_review.yaml').read_text())['code_review']
    if any(type(policy.get(key)) is not int or policy[key] <= 0 for key in
           ('max_record_bytes', 'max_records', 'max_preview_lines', 'context_lines')):
        raise ValueError('invalid code review policy')
    limit = policy['max_record_bytes']
    root = run.root / 'coding'
    paths = sorted((root / 'tool_applications').glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
    paths = [p for p in paths if not p.name.startswith('rollback_')]
    paths += sorted(root.glob('patch.v*.diff'), key=lambda p: p.stat().st_mtime, reverse=True)
    warnings: list[str] = []
    if len(paths) > policy['max_records']:
        warnings.append('记录较多，仅显示最近部分记录。')
    items: list[dict[str, Any]] = []
    for source in paths[:policy['max_records']]:
        try:
            raw = _read(source, run.root, limit)
            status = 'proposed'
            timestamp = ''
            if source.suffix == '.diff':
                files = parse_diff(raw)
                label = source.name
                timestamp = datetime.fromtimestamp(source.stat().st_mtime, tz=timezone.utc).isoformat()
                marker = source.with_suffix('.approved.json')
                if marker.exists():
                    receipt = json.loads(_read(marker, run.root, limit))
                    if receipt.get('applied') is True and receipt.get('run_id') == run.run_id and receipt.get('project') == run.project:
                        status = 'applied'
            else:
                record = json.loads(raw)
                if (record.get('run_id') != run.run_id or record.get('project') != run.project
                        or record.get('agent') != 'coding'):
                    continue
                tool = record.get('tool')
                if tool not in {'code.write_file', 'code.delete_file', 'code.apply_patch', 'code.patch_generator'}:
                    continue
                args = record['args']
                label = str(tool)
                timestamp = str(record.get('ended_at') or record.get('started_at') or '')
                status = 'recorded' if record['status'] == 'success' else 'not_applied'
                if tool in {'code.patch_generator', 'code.apply_patch'} and isinstance(args.get('diff'), str):
                    files = parse_diff(args['diff'])
                    if tool == 'code.patch_generator':
                        status = 'proposed'
                    elif status == 'recorded' and record.get('rollback_ref'):
                        if all(_before(run, record, item['path'], limit) is not None for item in files):
                            status = 'applied'
                elif tool in {'code.write_file', 'code.patch_generator', 'code.delete_file'} and isinstance(args.get('path'), str):
                    path = args['path']
                    after = '' if tool == 'code.delete_file' else args.get('content')
                    if not isinstance(after, str):
                        continue
                    before = _before(run, record, path, limit)
                    if before is None:
                        files = [{'path': path, 'additions': None, 'deletions': None, 'change': 'content',
                                  'lines': [{'kind': 'preview', 'text': line, 'old_line': None, 'new_line': i + 1}
                                            for i, line in enumerate(after.splitlines())]}]
                    else:
                        diff = '\n'.join(difflib.unified_diff(before.splitlines(), after.splitlines(),
                            fromfile='a/' + path, tofile='b/' + path, n=policy['context_lines'], lineterm=''))
                        files = parse_diff(diff) if diff else [{'path': path, 'additions': 0, 'deletions': 0, 'change': 'unchanged', 'lines': []}]
                        if status == 'recorded':
                            status = 'applied'
                else:
                    continue
            for index, item in enumerate(files):
                lines = item.pop('lines')
                item.update(id=f'{source.stem}:{index}', status=status, source=label, timestamp=timestamp,
                            truncated=len(lines) > policy['max_preview_lines'])
                if change_id == item['id']:
                    return {**item, 'run_id': run.run_id, 'project': project,
                            'lines': lines[:policy['max_preview_lines']]}
                items.append(item)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            warnings.append(f'{source.name} 无法安全读取或超出大小限制。')
    if change_id is not None:
        raise KeyError('代码改动记录不存在或暂不可读取')
    return {'run_id': run.run_id, 'project': project, 'items': items, 'warnings': warnings}
