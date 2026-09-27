"""Build an ad-hoc-signed macOS arm64 local test app, never a public release."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
from typing import Any

import yaml

from scripts.desktop.verify_bundle import inventory, native_dependencies, native_records, sha256, system_path, verify


def execute(argv: list[str], *, cwd: Path, env: dict[str, str]) -> str:
    result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    if result.returncode:
        # Build tools run with no inherited secrets and against public lockfiles.
        sys.stderr.write(result.stderr[-12000:])
        raise RuntimeError(f'Build command failed: {Path(argv[0]).name} (exit {result.returncode})')
    return result.stdout


def copy_file(root: Path, relative: str, destination: Path) -> None:
    path = root / relative
    if Path(relative).is_absolute() or '..' in Path(relative).parts or path.is_symlink() or not path.is_file():
        raise ValueError('Build source must be an explicit regular file')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Build source escapes the checkout')
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, destination)


def seal_bundle(bundle: Path, *, env: dict[str, str]) -> None:
    """Finalize only an owned staging bundle, then seal every native image."""
    records = native_records(bundle)
    for record in records:
        image = bundle / record['path']
        for rpath in record['rpaths']:
            if rpath.startswith('/') and not system_path(rpath):
                execute(['/usr/bin/install_name_tool', '-delete_rpath', rpath, str(image)], cwd=bundle.parent, env=env)
    native_dependencies(bundle)
    # --deep does not re-sign arbitrary Mach-O files under Resources/python.
    # On Apple Silicon a changed wheel dylib with a stale signature is killed
    # by the kernel at import time even if the outer app verifies successfully.
    for record in records:
        execute(['/usr/bin/codesign', '--force', '--sign', '-', str(bundle / record['path'])], cwd=bundle.parent, env=env)
    execute(['/usr/bin/codesign', '--force', '--deep', '--sign', '-', str(bundle)], cwd=bundle.parent, env=env)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--python-runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New empty directory outside the source tree')
    parser.add_argument('--state-journal-ref', help='Explicit committed revision for the state journal when its next increment is in progress')
    args = parser.parse_args()
    source, output, python_source = args.source.resolve(), args.output.resolve(), args.python_runtime.resolve()
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise ValueError('Build requires a real macOS Apple Silicon host')
    if output.exists() or output.is_relative_to(source):
        raise ValueError('Output must be a new directory outside the checkout')
    if (python_source / 'pyvenv.cfg').exists() or not (python_source / 'bin/python3.11').is_file():
        raise ValueError('A complete standalone CPython 3.11 prefix is required, not a virtualenv')
    policy = yaml.safe_load((source / 'desktop/bundle.yaml').read_text())
    if policy.get('schema') != 'mars.desktop-build.v1':
        raise ValueError('Unknown desktop build policy')
    for tool in ('uv', 'npm'):
        if not shutil.which(tool):
            raise ValueError(f'Missing build-only tool: {tool}')
    locked_inputs = {relative: sha256(source / relative) for relative in (
        'pyproject.toml', 'uv.lock', 'desktop/package.json', 'desktop/package-lock.json',
        'desktop/bundle.yaml', 'frontend/package.json', 'frontend/package-lock.json',
    )}
    output.mkdir(parents=True)
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL') if key in os.environ}
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1', NEXT_TELEMETRY_DISABLED='1',
               npm_config_registry='https://registry.npmjs.org', UV_DEFAULT_INDEX='https://pypi.org/simple')
    # No credentials, proxy overrides, Python/Node injection, or user .env.
    env['HOME'] = str(output / 'build-home')
    Path(env['HOME']).mkdir()
    source_revision = execute(['git', 'rev-parse', 'HEAD'], cwd=source, env=env).strip()
    pinned: dict[str, bytes] = {}
    if args.state_journal_ref:
        pinned['backend/app/harness/runtime/state_journal.py'] = subprocess.check_output(
            ['git', 'show', args.state_journal_ref + ':backend/app/harness/runtime/state_journal.py'], cwd=source, env=env)
    bundle = output / (policy['product_name'] + '.app')
    execute(['/usr/bin/ditto', str(source / 'desktop/node_modules/electron/dist/Electron.app'), str(bundle)], cwd=source, env=env)
    resources = bundle / 'Contents/Resources'
    (resources / 'default_app.asar').unlink(missing_ok=True)
    app_root, runtime, python_root = resources / 'app', resources / 'mars', resources / 'python'
    app_root.mkdir()
    runtime.mkdir()
    shutil.copytree(python_source, python_root, symlinks=True)
    # Only this owned distribution copy is mutable. Keep the user's uv-managed
    # original untouched; its PEP 668 marker is not applicable to our build prefix.
    (python_root / 'lib/python3.11/EXTERNALLY-MANAGED').unlink(missing_ok=True)
    shell_files = policy['shell_files']
    source_manifest = (source / 'scripts/release/v30_tree_allowlist.txt').read_text().splitlines()
    selected = [line.strip() for line in source_manifest if line.strip() and not line.lstrip().startswith('#')
                and any(line.strip().startswith(prefix) for prefix in policy['runtime_source_prefixes'])]
    selected.append('scripts/release/runtime_assets.txt')
    selected.extend(['frontend/package.json', 'frontend/package-lock.json', 'frontend/next.config.mjs'])
    before = {relative: hashlib.sha256(pinned[relative]).hexdigest() if relative in pinned else sha256(source / relative)
              for relative in selected + shell_files}
    for relative in selected:
        if relative in pinned:
            destination = runtime / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(pinned[relative])
        else:
            copy_file(source, relative, runtime / relative)
        if sha256(runtime / relative) != before[relative]:
            raise ValueError('Source changed while copying the runtime')
    for relative in shell_files:
        copy_file(source, relative, app_root / relative)
    if any(sha256(source / relative) != digest for relative, digest in before.items() if relative not in pinned):
        raise ValueError('Source changed during its snapshot')
    committed = dict(line.split(' ', 1)[::-1] for line in execute(
        ['git', 'ls-tree', '-r', '--format=%(objectname) %(path)', source_revision], cwd=source, env=env).splitlines())
    overlay: dict[str, str] = {}
    for relative, digest in before.items():
        copied = (app_root if relative in shell_files else runtime) / relative
        data = copied.read_bytes()
        git_digest = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        if committed.get(relative) != git_digest:
            overlay[relative] = digest
    (output / 'source-snapshot.json').write_text(json.dumps({'source_revision': source_revision,
        'source_files': before, 'overlay_source_files': overlay,
        'pinned_source_revisions': {relative: args.state_journal_ref for relative in pinned}}, indent=2) + '\n')
    sys.stdout.write('Source snapshot frozen and verified.\n')
    sys.stdout.flush()
    # Reinstall locked dependencies for this ABI. Never copy the developer venv.
    requirements = output / 'requirements.lock.txt'
    execute(['uv', 'export', '--frozen', '--no-dev', '--no-emit-project', '--no-annotate', '--no-header',
             '--output-file', str(requirements)], cwd=source, env=env)
    execute(['uv', 'pip', 'install', '--python', str(python_root / 'bin/python3.11'), '--require-hashes',
             '--no-deps', '--only-binary=:all:', '--link-mode', 'copy', '-r', str(requirements)], cwd=output, env=env)
    for entry in (python_root / 'bin').iterdir():
        if entry.name not in {'python', 'python3', 'python3.11'}:
            entry.unlink()  # Console shebangs contain build-prefix paths; use python -m.
    for cache in python_root.rglob('__pycache__'):
        shutil.rmtree(cache)
    shell_package = json.loads((source / 'desktop/package.json').read_text())
    shell_package['main'] = 'desktop/src/main.cjs'
    shell_package.pop('devDependencies', None)
    shell_package.pop('scripts', None)
    (app_root / 'package.json').write_text(json.dumps(shell_package, indent=2) + '\n')
    # yaml is the shell's sole production dependency; install from its exact lock.
    shell_deps = output / 'shell-dependencies'
    shell_deps.mkdir()
    for filename in ('package.json', 'package-lock.json'):
        copy_file(source, 'desktop/' + filename, shell_deps / filename)
    execute(['npm', 'ci', '--omit=dev', '--ignore-scripts', '--no-audit', '--no-fund'], cwd=shell_deps, env=env)
    shutil.copytree(shell_deps / 'node_modules', app_root / 'node_modules', symlinks=True)
    frontend = runtime / 'frontend'
    execute(['npm', 'ci', '--omit=dev', '--ignore-scripts', '--no-audit', '--no-fund'], cwd=frontend, env=env)
    exclusions = set(policy['frontend_build_exclusions'])
    frontend_files = {item.relative_to(source / 'frontend/.next').as_posix(): sha256(item)
                      for item in (source / 'frontend/.next').rglob('*')
                      if item.is_file() and item.relative_to(source / 'frontend/.next').parts[0] not in exclusions}
    shutil.copytree(source / 'frontend/.next', frontend / '.next',
                    ignore=lambda folder, names: list(set(names) & exclusions) if Path(folder) == source / 'frontend/.next' else [])
    if (source / 'frontend/public').is_dir():
        shutil.copytree(source / 'frontend/public', frontend / 'public')
    licenses = resources / 'licenses'
    licenses.mkdir()
    for name in ('LICENSE', 'LICENSES.chromium.html'):
        copy_file(source, 'desktop/node_modules/electron/dist/' + name, licenses / ('electron-' + name))
    copy_file(source, 'LICENSE', licenses / 'MARS-LICENSE')
    shutil.copy2(requirements, licenses / 'python-requirements.lock.txt')
    (licenses / 'README.txt').write_text('Local test only; no Developer ID signature or notarization.\n'
        'Python license: ../python/lib/python3.11/LICENSE.txt\n'
        'Python dependency licenses: *.dist-info and package license files under Python site-packages.\n'
        'Node dependency licenses: bundled node_modules license files.\n')
    declaration = {'schema': 'mars.local-test-bundle.v1', 'release': False, 'arch': policy['arch'],
                   'python': '3.11', 'source_revision': source_revision,
                   'pinned_source_revisions': {relative: args.state_journal_ref for relative in pinned},
                   'source_files': before, 'overlay_source_files': overlay, 'locked_inputs': locked_inputs,
                   'uv_lock_sha256': locked_inputs['uv.lock'],
                   'frontend_lock_sha256': locked_inputs['frontend/package-lock.json'],
                   'python_requirements_sha256': sha256(requirements),
                   'frontend_build_id': (frontend / '.next/BUILD_ID').read_text().strip()}
    (resources / 'local-test.json').write_text(json.dumps(declaration, indent=2) + '\n')
    plist = bundle / 'Contents/Info.plist'
    info = plistlib.loads(plist.read_bytes())
    (bundle / 'Contents/MacOS/Electron').rename(bundle / 'Contents/MacOS' / policy['product_name'])
    info.update(CFBundleName=policy['product_name'], CFBundleDisplayName=policy['product_name'],
                CFBundleExecutable=policy['product_name'],
                CFBundleIdentifier=policy['bundle_identifier'], LSMinimumSystemVersion=policy['minimum_macos'])
    plist.write_bytes(plistlib.dumps(info))
    for helper in (bundle / 'Contents/Frameworks').glob('*.app/Contents/Info.plist'):
        info = plistlib.loads(helper.read_bytes())
        old = str(info.get('CFBundleIdentifier', 'helper'))
        info['CFBundleIdentifier'] = policy['bundle_identifier'] + '.' + old.replace('com.github.Electron.', '')
        helper.write_bytes(plistlib.dumps(info))
    # Local ad-hoc identity, no certificate lookup, account, signing service or notarization.
    seal_bundle(bundle, env=env)
    if (any(sha256(source / relative) != digest for relative, digest in locked_inputs.items())
            or any(sha256(frontend / '.next' / relative) != digest for relative, digest in frontend_files.items())):
        raise ValueError('Source changed during packaging; discard this candidate and rebuild')
    manifest = {'schema': 'mars.bundle-inventory.v1', 'release': False, 'entries': inventory(bundle),
                'native_dependencies': native_dependencies(bundle)}
    (output / 'bundle-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    verification = verify(bundle, output / 'bundle-manifest.json')
    (output / 'build-verification.json').write_text(json.dumps(verification, indent=2) + '\n')
    sys.stdout.write(json.dumps({'bundle': str(bundle), 'manifest': str(output / 'bundle-manifest.json'), **verification}, indent=2) + '\n')


if __name__ == '__main__':
    main()
