"""Verify the exact inventory of a local test .app; this is not notarization."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
import subprocess
import shutil
import tempfile
from typing import Any

MACHO = {b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca', b'\xca\xfe\xba\xbf'}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def inventory(bundle: Path) -> list[dict[str, Any]]:
    root = bundle.resolve()
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        if path.is_symlink():
            if not path.resolve(strict=True).is_relative_to(root):
                raise ValueError(f'Bundle symlink escapes resources: {relative}')
            rows.append({'path': relative, 'kind': 'symlink', 'target': str(path.readlink())})
        elif path.is_file():
            if path.name == 'pyvenv.cfg' or path.name.startswith(('.env', '__editable__')):
                raise ValueError(f'Developer state is forbidden: {relative}')
            rows.append({'path': relative, 'kind': 'file', 'bytes': info.st_size,
                         'mode': stat.S_IMODE(info.st_mode), 'sha256': sha256(path)})
        elif path.is_dir():
            rows.append({'path': relative, 'kind': 'directory', 'mode': stat.S_IMODE(info.st_mode)})
        else:
            raise ValueError(f'Unexpected bundle filesystem entry: {relative}')
    return rows


def native_records(bundle: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    root = bundle.resolve()
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.is_symlink():
            continue
        with path.open('rb') as handle:
            if handle.read(4) not in MACHO:
                continue
        # otool treats '(GPU)' at the end of a filename as archive syntax.
        with tempfile.TemporaryDirectory(prefix='mars-macho-inspection-') as temporary:
            inspection = path
            if '(' in path.name:
                inspection = Path(temporary) / 'image'
                shutil.copyfile(path, inspection)
            loads = subprocess.check_output(['/usr/bin/otool', '-L', str(inspection)], text=True)
            identifiers = subprocess.run(['/usr/bin/otool', '-D', str(inspection)], capture_output=True, text=True, check=False).stdout.splitlines()[1:]
            commands = subprocess.check_output(['/usr/bin/otool', '-l', str(inspection)], text=True).splitlines()
            headers = subprocess.check_output(['/usr/bin/otool', '-hv', str(inspection)], text=True)
        ids = {value.strip() for value in identifiers}
        dependencies = [line.strip().split(' (compatibility')[0] for line in loads.splitlines()
                        if line.startswith('\t') and line.strip().split(' (compatibility')[0] not in ids]
        rpaths = [commands[index + 2].strip().removeprefix('path ').split(' (offset')[0]
                  for index, line in enumerate(commands) if line.strip() == 'cmd LC_RPATH']
        rows.append({'path': path.relative_to(root).as_posix(), 'loads': dependencies,
                     'rpaths': rpaths, 'executable': 'EXECUTE' in headers.split()})
    return rows


def system_path(value: str) -> bool:
    return value.startswith(('/usr/lib/', '/System/Library/'))


def native_dependencies(bundle: Path) -> dict[str, Any]:
    root = bundle.resolve()
    rows = native_records(root)
    images = {str((root / row['path']).resolve()): row for row in rows}
    executables = [root / row['path'] for row in rows if row['executable']]
    main_executables = [p for p in executables if p.parent == root / 'Contents/MacOS']
    python_executables = [p for p in executables if p.parent == root / 'Contents/Resources/python/bin']

    def expand(value: str, loader: Path, executable: Path) -> Path:
        if value.startswith('@loader_path/'):
            result = loader.parent / value.removeprefix('@loader_path/')
        elif value == '@loader_path':
            result = loader.parent
        elif value.startswith('@executable_path/'):
            result = executable.parent / value.removeprefix('@executable_path/')
        elif value.startswith('/'):
            result = Path(value)
        else:
            raise ValueError(f'Unsupported native search path: {value}')
        resolved = result.resolve()
        if not resolved.is_relative_to(root) and not system_path(str(resolved)):
            raise ValueError(f'Native path escapes bundle: {value}')
        return resolved

    def inspect(loader: Path, executable: Path, inherited: list[Path], seen: set[tuple[str, str]]) -> None:
        key = (str(loader.resolve()), str(executable.resolve()))
        if key in seen:
            return
        seen.add(key)
        row = images.get(key[0])
        if row is None:
            raise ValueError('A native dependency is not an inventoried Mach-O image')
        search = [expand(value, loader, executable) for value in row['rpaths']] + inherited
        for value in row['loads']:
            if system_path(value):
                continue  # macOS shared-cache libraries need not exist as loose files.
            if value.startswith('@rpath/'):
                candidates = [(directory / value.removeprefix('@rpath/')).resolve() for directory in search]
                dependency = next((p for p in candidates if p.is_file()), None)
                if dependency is None:
                    raise ValueError(f'Unresolved native dependency: {row["path"]} -> {value}')
                if not dependency.is_relative_to(root):
                    raise ValueError('Resolved native dependency escapes bundle')
            else:
                dependency = expand(value, loader, executable)
                if system_path(str(dependency)):
                    continue
                if not dependency.is_file():
                    raise ValueError(f'Missing native dependency: {value}')
            inspect(dependency, executable, search, seen)

    for row in rows:
        image = root / row['path']
        if row['executable']:
            host = image
        elif image.is_relative_to(root / 'Contents/Resources/python') and python_executables:
            host = python_executables[0]
        elif main_executables:
            host = main_executables[0]
        elif len(executables) == 1:
            host = executables[0]
        else:
            # Standalone dylib with only loader-relative/system loads is valid.
            host = image
        host_row = images[str(host.resolve())]
        inherited = [expand(value, host, host) for value in host_row['rpaths']] if host != image else []
        inspect(image, host, inherited, set())
    return {'macho_count': len(rows), 'libraries': rows, 'static_loads_resolved': True}


def verify(bundle: Path, manifest: Path) -> dict[str, Any]:
    expected = json.loads(manifest.read_text())
    if expected.get('schema') != 'mars.bundle-inventory.v1' or expected.get('release') is not False:
        raise ValueError('Unknown bundle inventory')
    actual = inventory(bundle)
    if actual != expected.get('entries'):
        raise ValueError('Bundle contents differ from the recorded inventory')
    native = native_dependencies(bundle)
    if native != expected.get('native_dependencies'):
        raise ValueError('Bundle native dependency inventory differs')
    for record in native['libraries']:
        subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(bundle / record['path'])], check=True)
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(bundle)], check=True)
    return {'schema': 'mars.bundle-verification.v1', 'exact_inventory': True,
            'entries': len(actual), 'macho_count': native['macho_count'],
            'ad_hoc_signature_valid': True, 'individual_native_signatures_valid': True,
            'static_native_loads_resolved': True, 'developer_id_signed': False, 'notarized': False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('manifest', type=Path)
    args = parser.parse_args()
    result = verify(args.bundle, args.manifest)
    # A machine-readable CLI receipt, not production application logging.
    import sys
    sys.stdout.write(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
