"""Build a small, dependency-free Windows core update from reviewed source files."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any
import zipfile

import yaml

_FORBIDDEN_PARTS = frozenset({'.git', '.venv', 'venv', 'node_modules', '.next', '__pycache__', 'credentials', 'secrets'})
_STATE_ROOTS = frozenset({'offline', 'dependencies', 'local', 'data', 'workspace', 'runs', 'knowledge', 'conversations'})
_FORBIDDEN_SUFFIXES = frozenset({'.exe', '.dll', '.whl', '.zip', '.7z', '.gz', '.pyc', '.pyo', '.pem', '.key', '.log'})
_EXTRA_FILES = ('README.md', 'README.zh-CN.md')


def sha256(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _safe_file(root: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if (not name or relative.is_absolute() or relative.as_posix() != name
            or any(part in {'', '.', '..'} for part in name.split('/'))
            or any(char in name for char in '\\:\x00<>"|?*')
            or any(part.endswith((' ', '.')) or re.match(r'(?i)^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)', part)
                   for part in relative.parts)):
        raise ValueError('Release path must be a normalized relative file: ' + name)
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError('Release paths cannot traverse symbolic links: ' + name)
    if not current.is_file() or not current.resolve().is_relative_to(root):
        raise ValueError('Release file is missing or outside source: ' + name)
    return current


def dependency_fingerprint(source: Path) -> dict[str, str]:
    """The six compatibility fields shared by full bundles and core updates."""
    if source.is_symlink() or not source.is_dir():
        raise ValueError('Source directory is unavailable or symbolic')
    root = source.resolve(strict=True)
    config: Any = yaml.safe_load(_safe_file(root, 'configs/windows_native.yaml').read_text(encoding='utf-8'))
    if not isinstance(config, dict):
        raise ValueError('Windows dependency configuration must be a mapping')
    result = {name: sha256(_safe_file(root, name)) for name in ('uv.lock', 'frontend/package-lock.json')}
    for key in ('python_version', 'uv_sha256', 'node_sha256', 'git_sha256'):
        value = config.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError('Windows dependency configuration lacks ' + key)
        if key != 'python_version' and not re.fullmatch(r'[a-fA-F0-9]{64}', value):
            raise ValueError('Windows dependency checksum is invalid: ' + key)
        result['windows.' + key] = value if key == 'python_version' else value.lower()
    return result


def source_files(source: Path) -> tuple[str, ...]:
    """Select literal reviewed files, never local state or a directory wildcard."""
    if source.is_symlink() or not source.is_dir():
        raise ValueError('Source directory is unavailable or symbolic')
    root = source.resolve(strict=True)
    manifest = _safe_file(root, 'scripts/release/v30_tree_allowlist.txt')
    names = [line.strip() for line in manifest.read_text(encoding='utf-8').splitlines()
             if line.strip() and not line.lstrip().startswith('#')]
    if not names or len(names) != len({name.casefold() for name in names}):
        raise ValueError('Release manifest is empty or has duplicate file paths')
    declared = {name.casefold() for name in names}
    names.extend(name for name in _EXTRA_FILES if (root / name).is_file() and name.casefold() not in declared)
    for name in names:
        path = _safe_file(root, name)
        parts = {part.casefold() for part in PurePosixPath(name).parts}
        if (parts & _FORBIDDEN_PARTS or PurePosixPath(name).parts[0].casefold() in _STATE_ROOTS or path.suffix.lower() in _FORBIDDEN_SUFFIXES
                or path.name.startswith('.env') and path.name not in {'.env.example', '.env.production.example'}
                or path.name in {'CORE-UPDATE.json', 'WINDOWS-OFFLINE.json'}):
            raise ValueError('Private, generated or dependency file cannot enter a core update: ' + name)
        text = path.read_bytes().decode('utf-8', errors='replace')
        # Existing project terminology is legitimate product content. Only
        # secret-shaped credentials and personal machine bindings are excluded.
        if re.search(r'(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{25,}|AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)', text):
            raise ValueError('Secret-shaped source content requires review: ' + name)
        if re.search(r'(?:' + re.escape('/' + 'Users') + r'/[^/\s]+/|[A-Za-z]:\\Users\\[^\\\s]+\\)', text):
            raise ValueError('Personal machine path cannot enter a core update: ' + name)
    return tuple(sorted(names))


def file_bytes(source: Path, name: str) -> bytes:
    """The same portable product defaults are used by both delivery formats."""
    root = source.resolve(strict=True)
    path = _safe_file(root, name)
    if name == 'configs/execution.yaml':
        config: Any = yaml.safe_load(path.read_text(encoding='utf-8'))
        if not isinstance(config, dict) or not isinstance(config.get('execution'), dict):
            raise ValueError('Execution defaults must be a mapping')
        execution = config['execution']
        execution.update(device='cpu', backend='local_command', local_commands=[])
        if not isinstance(execution.get('paper_static'), dict):
            raise ValueError('Paper adapter defaults must be a mapping')
        execution['paper_static'].update(enabled=False, python='python', repo_path='', data_path='')
        return yaml.safe_dump(config, sort_keys=False, allow_unicode=True).encode('utf-8')
    if name in _EXTRA_FILES and (root / 'deploy/windows-native/README-OFFLINE.md').is_file():
        return _safe_file(root, 'deploy/windows-native/README-OFFLINE.md').read_bytes()
    content = path.read_bytes()
    if '/' not in name and name.endswith('.cmd'):
        text = content.decode('utf-8')
        if 'windows-native\\Mars.ps1' in text and '%*' in text and '-OfflineRequired' not in text:
            text = text.replace(' %*', ' -OfflineRequired %*')
            content = text.replace('\r\n', '\n').replace('\n', '\r\n').encode('utf-8')
    return content


def core_manifest(source: Path, *, version: str) -> dict[str, Any]:
    if not version.strip() or len(version) > 128 or any(ord(char) < 32 for char in version):
        raise ValueError('A nonempty release version is required')
    root = source.resolve(strict=True)
    names = source_files(source)
    fingerprint = dependency_fingerprint(source)
    files = []
    for name in names:
        content = file_bytes(root, name)
        files.append({'path': name, 'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest(),
                      'policy': 'seed' if PurePosixPath(name).parts[0] in {'configs', 'projects'} else 'replace'})
    return {'schema': 'mars.windows.core.v1', 'version': version,
            'dependency_fingerprint': fingerprint, 'files': files}


def build_core(source: Path, output: Path, *, version: str) -> dict[str, Any]:
    marker = core_manifest(source, version=version)
    root = source.resolve(strict=True)
    fingerprint = dependency_fingerprint(source)
    files = marker['files']
    if output.exists() or output.is_symlink():
        raise ValueError('Output must be a new ZIP file')
    if output.resolve().is_relative_to(root):
        raise ValueError('Release output must be outside the source tree')
    output.parent.mkdir(parents=True, exist_ok=True)
    part = output.with_suffix(output.suffix + '.part')
    if part.exists() or part.is_symlink():
        raise ValueError('An unfinished output already exists')
    try:
        with zipfile.ZipFile(part, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr('CORE-UPDATE.json', json.dumps(marker, ensure_ascii=False, indent=2) + '\n')
            for row in files:
                content = file_bytes(root, str(row['path']))
                if len(content) != row['bytes'] or hashlib.sha256(content).hexdigest() != row['sha256']:
                    raise ValueError('Source changed while building core update: ' + str(row['path']))
                archive.writestr(str(row['path']), content)
        if dependency_fingerprint(source) != fingerprint:
            raise ValueError('Dependency inputs changed during core assembly')
        part.replace(output)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return {'archive': str(output.resolve()), 'sha256': sha256(output), 'bytes': output.stat().st_size,
            'version': version, 'files': len(files), 'dependency_fingerprint': fingerprint}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--version')
    args = parser.parse_args()
    version = args.version
    if version is None:
        version = subprocess.check_output(['git', '-C', str(args.source), 'rev-parse', 'HEAD'], text=True).strip()
    print(json.dumps(build_core(args.source, args.output, version=version), ensure_ascii=False))


if __name__ == '__main__':
    main()
