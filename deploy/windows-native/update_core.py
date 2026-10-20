"""Apply a dependency-free MARS core distribution, preserving local user state.

This bootstrap uses only Python's standard library. The PowerShell wrapper owns
service shutdown, real import/build/start checks, then commit or rollback.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile
from typing import Any, Iterator
import uuid
import zipfile

MANIFEST = 'CORE-UPDATE.json'
SCHEMA = 'mars.windows.core.v1'
RECEIPT_SCHEMA = 'mars.windows.core-update.v1'
PROTECTED = frozenset({'dependencies', 'offline', 'local', 'workspace', 'runs',
    'conversations', 'knowledge', 'data', 'uploads', '.git', '.venv', 'venv',
    'node_modules', '.next', '__pycache__', 'credentials', 'secrets'})
PREFIXES = ('backend/app/', 'frontend/src/', 'frontend/public/', 'configs/',
            'templates/', 'projects/', 'posttrain/src/', 'deploy/windows-native/',
            'scripts/release/', 'frontend/scripts/', 'scripts/evaluators/',
            'desktop/src/', 'scripts/desktop/', 'deploy/desktop/', 'docs/')
ROOT_FILES = frozenset({'LICENSE', 'README.md', 'README.zh-CN.md', 'pyproject.toml',
    'uv.lock', '.env.example', '.gitignore', '.gitattributes', 'MARS-Windows.cmd', 'Update-MARS.cmd',
    'manage-mars-windows.cmd', 'install-mars-windows.cmd', 'configure-mars-api.cmd',
    'start-mars-windows-native.cmd', 'stop-mars-windows-native.cmd',
    'frontend/package.json', 'frontend/package-lock.json', 'frontend/tsconfig.json',
    'frontend/next.config.ts', 'frontend/next.config.mjs', 'frontend/next-env.d.ts',
    'frontend/postcss.config.mjs', 'frontend/postcss.config.js',
    'frontend/tailwind.config.ts', 'frontend/components.json',
    'frontend/eslint.config.mjs', 'frontend/.eslintrc.json', 'desktop/config.yaml',
    'desktop/package.json', 'desktop/package-lock.json', 'desktop/bundle.yaml'})


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def _is_link(path: Path) -> bool:
    # Python 3.11 has no Path.is_junction; Windows junctions are reparse points.
    try:
        return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400)
    except FileNotFoundError:
        return False


def safe_name(name: str) -> str:
    if (not isinstance(name, str) or not name or '\\' in name or ':' in name
            or name.startswith('/') or any(ord(c) < 32 or c in '*?"<>|' for c in name)):
        raise ValueError('Invalid core path: ' + str(name))
    parts = name.split('/')
    if any(part in {'', '.', '..'} or part.endswith((' ', '.')) for part in parts):
        raise ValueError('Invalid core path: ' + name)
    if parts[0].casefold() in PROTECTED:
        raise ValueError('Protected core path: ' + name)
    for part in parts:
        if part.casefold() in {'.git', '.venv', 'node_modules', '.next', '__pycache__', 'credentials', 'secrets'} or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?', part):
            raise ValueError('Protected core path: ' + name)
    if any(part.startswith('.env') for part in parts) and name != '.env.example':
        raise ValueError('User environment settings are never updated')
    if name != MANIFEST and name not in ROOT_FILES and not name.startswith(PREFIXES):
        raise ValueError('Path is outside the application core: ' + name)
    return name


def safe_target(root: Path, name: str) -> Path:
    safe_name(name)
    path = root
    for part in PurePosixPath(name).parts:
        path /= part
        if _is_link(path):
            raise ValueError('Core destination cannot be a link: ' + name)
    if not path.resolve().is_relative_to(root):
        raise ValueError('Core path escaped its root')
    return path


def dependency_fingerprint(root: Path) -> dict[str, str]:
    config: dict[str, str] = {}
    for line in (root / 'configs/windows_native.yaml').read_text(encoding='utf-8-sig').splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, separator, value = line.partition(':')
        if separator:
            config[key.strip()] = value.strip()
    values = {name: digest(root / name) for name in ('uv.lock', 'frontend/package-lock.json')}
    for key in ('python_version', 'uv_sha256', 'node_sha256', 'git_sha256'):
        if not config.get(key):
            raise ValueError('Missing Windows dependency setting: ' + key)
        values['windows.' + key] = config[key].lower()
    return values


def _json(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('Distribution manifest is too large')
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('Expected a manifest object')
    return value


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.part')
    try:
        temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _copy(source: Path, target: Path, *, expected_sha256: str | None = None) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.' + uuid.uuid4().hex + '.part')
    try:
        shutil.copyfile(source, temporary)
        if expected_sha256 is not None and digest(temporary) != expected_sha256:
            raise ValueError('Core bytes changed during copying: ' + str(source))
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def source_tree(source: Path) -> Iterator[Path]:
    if source.is_dir():
        if source.is_symlink():
            raise ValueError('Core source cannot be a symbolic link')
        if (source / MANIFEST).is_file():
            yield source.resolve()
            return
        candidates = [child for child in source.iterdir() if child.is_dir() and (child / MANIFEST).is_file()]
        if len(candidates) != 1:
            raise ValueError('Select the core ZIP or its extracted folder containing CORE-UPDATE.json')
        yield candidates[0].resolve()
        return
    with tempfile.TemporaryDirectory(prefix='mars-core-') as temporary:
        stage = Path(temporary).resolve()
        with zipfile.ZipFile(source) as archive:
            entries = [entry for entry in archive.infolist() if not entry.is_dir()]
            markers = [e.filename for e in entries if e.filename == MANIFEST or e.filename.endswith('/' + MANIFEST)]
            if len(markers) != 1:
                raise ValueError('The archive must contain one core manifest')
            prefix = markers[0][:-len(MANIFEST)]
            if prefix and (len(PurePosixPath(prefix).parts) != 1 or prefix.startswith('/')):
                raise ValueError('Only one enclosing GitHub archive folder is supported')
            seen: set[str] = set()
            total = 0
            if len(entries) > 16000:
                raise ValueError('The core archive has too many files')
            for entry in entries:
                if not entry.filename.startswith(prefix):
                    raise ValueError('Unexpected files outside the core folder')
                name = safe_name(entry.filename[len(prefix):])
                if name.casefold() in seen or stat.S_ISLNK(entry.external_attr >> 16):
                    raise ValueError('Duplicate or linked core archive entry')
                seen.add(name.casefold())
                total += entry.file_size
                if total > 512 * 1024 * 1024:
                    raise ValueError('Core archive exceeds its uncompressed size budget')
                target = safe_target(stage, name)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as content, target.open('wb') as output:
                    shutil.copyfileobj(content, output)
        yield stage


def validate(source: Path) -> dict[str, Any]:
    manifest = _json(safe_target(source, MANIFEST))
    if manifest.get('schema') != SCHEMA or not isinstance(manifest.get('version'), str):
        raise ValueError('Invalid MARS core manifest')
    rows = manifest.get('files')
    if not isinstance(rows, list) or not rows:
        raise ValueError('The core manifest has no files')
    seen = {MANIFEST.casefold()}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Invalid core file record')
        name = safe_name(row.get('path', ''))
        policy = row.get('policy')
        if name.casefold() in seen or policy not in {'replace', 'seed'}:
            raise ValueError('Duplicate core path or invalid update policy: ' + name)
        if name.startswith(('configs/', 'projects/')) and policy != 'seed':
            raise ValueError('User configurations and projects must be preserved: ' + name)
        seen.add(name.casefold())
        path = safe_target(source, name)
        checksum = row.get('sha256', '')
        if (not path.is_file() or type(row.get('bytes')) is not int
                or path.stat().st_size != row['bytes'] or not re.fullmatch('[0-9a-f]{64}', checksum)
                or digest(path) != checksum):
            raise ValueError('Core checksum verification failed: ' + name)
    # Only declared files are ever copied. A Git checkout's .git directory or
    # Finder metadata is irrelevant and must not cause an otherwise valid
    # update to fail or trigger a walk through unrelated dependency trees.
    if manifest.get('dependency_fingerprint') != dependency_fingerprint(source):
        raise ValueError('Core dependency fingerprint does not match its lock files')
    return manifest


def check(root: Path, source: Path) -> dict[str, Any]:
    manifest = validate(source)
    if manifest['dependency_fingerprint'] != dependency_fingerprint(root):
        raise ValueError('Dependency versions have changed. Use the matching full offline package; no dependency download or local modification was attempted.')
    modern = root / 'dependencies/windows'
    stamp = (modern if modern.exists() else root / 'local/windows') / 'installed.json'
    if stamp.is_file():
        installed = _json(stamp)
        expected = manifest['dependency_fingerprint']
        keys = tuple(expected) if 'windows.python_version' in installed else ('uv.lock', 'frontend/package-lock.json')
        compatible = all(isinstance(installed.get(key), str) and installed[key].lower() == expected[key] for key in keys)
        if 'windows.python_version' not in installed:
            compatible = compatible and str(installed.get('configs/windows_native.yaml', '')).lower() == digest(root / 'configs/windows_native.yaml')
        if not compatible:
            raise ValueError('Installed dependencies do not match the core version. Use its matching full offline package; the installed environment was preserved.')
    for row in manifest['files']:
        path = safe_target(root, row['path'])
        if path.exists() and not path.is_file():
            raise ValueError('Core destination is not a regular file: ' + row['path'])
    return manifest


def _receipt_path(root: Path, receipt: Path | None) -> Path:
    base = root / 'local/core-updates'
    path = receipt.absolute() if receipt else base / 'latest.json'
    if '..' in path.parts:
        raise ValueError('Update receipt paths cannot contain parent traversal')
    if not path.is_relative_to(base) or _is_link(path):
        raise ValueError('Update receipts must stay in local/core-updates')
    for part in (base, *path.parents):
        if part.is_relative_to(root) and _is_link(part):
            raise ValueError('Update receipt directory cannot be a link')
    return path


def rollback(root: Path, receipt_path: Path) -> dict[str, Any]:
    receipt = _json(receipt_path)
    if receipt.get('schema') != RECEIPT_SCHEMA or receipt.get('root') != str(root):
        raise ValueError('Update receipt belongs to a different installation')
    if receipt.get('phase') == 'rolled_back':
        return receipt
    if re.fullmatch('[a-f0-9]{32}', str(receipt.get('transaction_id', ''))) is None:
        raise ValueError('Invalid core transaction identifier')
    backup = Path(receipt['backup'])
    expected = root / 'local/core-updates' / receipt['transaction_id'] / 'before'
    if backup != expected or _is_link(backup):
        raise ValueError('Invalid core backup location')
    # Verify the complete rollback set before restoring the first byte.
    for row in receipt['changes']:
        safe_target(root, row['path'])
        if row['existed'] and digest(safe_target(backup, row['path'])) != row['before_sha256']:
            raise ValueError('Core rollback backup checksum mismatch')
    for row in reversed(receipt['changes']):
        target = safe_target(root, row['path'])
        if row['existed']:
            _copy(safe_target(backup, row['path']), target, expected_sha256=row['before_sha256'])
        else:
            target.unlink(missing_ok=True)
    receipt['phase'] = 'rolled_back'
    atomic_json(receipt_path, receipt)
    return receipt


def apply(root: Path, source: Path, receipt_path: Path) -> dict[str, Any]:
    manifest = check(root, source)
    if receipt_path.exists() and _json(receipt_path).get('phase') in {'applying', 'applied'}:
        raise ValueError('The previous core update needs commit or rollback before another update')
    old_manifest = _json(root / MANIFEST) if (root / MANIFEST).is_file() else {'files': []}
    incoming = {row['path']: row for row in manifest['files']}
    deleted = [row['path'] for row in old_manifest['files']
               if row.get('policy') == 'replace' and row['path'] not in incoming
               and not row['path'].startswith(('configs/', 'projects/'))]
    names = list(incoming) + deleted + [MANIFEST]
    transaction = uuid.uuid4().hex
    backup = root / 'local/core-updates' / transaction / 'before'
    receipt: dict[str, Any] = {'schema': RECEIPT_SCHEMA, 'transaction_id': transaction,
        'root': str(root), 'backup': str(backup), 'version': manifest['version'],
        'phase': 'applying', 'changes': [], 'preserved_seed_files': []}
    for name in names:
        target = safe_target(root, name)
        row = incoming.get(name)
        if row and row['policy'] == 'seed' and target.exists():
            receipt['preserved_seed_files'].append(name)
            continue
        if row and target.is_file() and digest(target) == row['sha256']:
            continue
        change = {'path': name, 'existed': target.is_file(),
                  'before_sha256': digest(target) if target.is_file() else None}
        if change['existed']:
            _copy(target, safe_target(backup, name), expected_sha256=change['before_sha256'])
        receipt['changes'].append(change)
    atomic_json(receipt_path, receipt)
    try:
        for row in receipt['changes']:
            name = row['path']
            target = safe_target(root, name)
            if name in deleted:
                target.unlink(missing_ok=True)
            elif name == MANIFEST:
                atomic_json(target, manifest)
            else:
                _copy(safe_target(source, name), target, expected_sha256=incoming[name]['sha256'])
        receipt['phase'] = 'applied'
        atomic_json(receipt_path, receipt)
        return receipt
    except BaseException:
        rollback(root, receipt_path)
        raise


@contextmanager
def update_lock(root: Path) -> Iterator[None]:
    directory = root / 'local/core-updates'
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / 'updating.lock'
    with lock.open('a+b') as stream:
        stream.seek(0)
        stream.write(b'\0')
        stream.flush()
        stream.seek(0)
        if sys.platform == 'win32':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if sys.platform == 'win32':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--receipt', type=Path)
    actions = parser.add_mutually_exclusive_group(required=True)
    for action in ('check', 'apply', 'rollback', 'commit'):
        actions.add_argument('--' + action, action='store_true')
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    receipt_path = _receipt_path(root, args.receipt)
    if args.check or args.apply:
        if args.source is None:
            parser.error('--source is required with --check or --apply')
        with source_tree(args.source) as source:
            if args.check:
                value = {'version': check(root, source)['version'], 'status': 'compatible'}
            else:
                with update_lock(root):
                    value = apply(root, source, receipt_path)
    else:
        with update_lock(root):
            if args.rollback:
                value = rollback(root, receipt_path)
            else:
                value = _json(receipt_path)
                if value.get('schema') != RECEIPT_SCHEMA or value.get('root') != str(root) or value.get('phase') != 'applied':
                    raise ValueError('Only a verified applied update can be committed')
                value['phase'] = 'committed'
                atomic_json(receipt_path, value)
    print(json.dumps({key: value[key] for key in ('version', 'status', 'phase', 'transaction_id') if key in value}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        print('MARS core update failed: ' + str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
