"""Exercise the real stdlib Windows core updater on temporary installations."""
from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType
from typing import Any
import zipfile

import pytest


SOURCE_ROOT = Path(__file__).resolve().parents[3]
UPDATER_PATH = SOURCE_ROOT / 'deploy/windows-native/update_core.py'


def _updater_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location('actual_windows_core_updater', UPDATER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


UPDATER = _updater_module()


def _write(root: Path, name: str, content: bytes) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _manifest(root: Path, names: list[str], version: str) -> dict[str, Any]:
    marker: dict[str, Any] = {
        'schema': UPDATER.SCHEMA,
        'version': version,
        'dependency_fingerprint': UPDATER.dependency_fingerprint(root),
        'files': [{
            'path': name, 'bytes': (root / name).stat().st_size,
            'sha256': UPDATER.digest(root / name),
            'policy': 'seed' if name.startswith(('configs/', 'projects/')) else 'replace',
        } for name in names],
    }
    (root / UPDATER.MANIFEST).write_text(json.dumps(marker), encoding='utf-8')
    return marker


@dataclass(frozen=True)
class Installation:
    root: Path
    source: Path
    receipt: Path
    protected: dict[Path, bytes]
    original_core: dict[str, bytes]


@pytest.fixture
def installation(tmp_path: Path) -> Installation:
    root = tmp_path / '安装 目录 (x64)'
    source = tmp_path / '核心更新 (新版)'
    root.mkdir()
    source.mkdir()
    dependencies = {
        'uv.lock': b'version = 1\nrevision = 1\n',
        'frontend/package-lock.json': b'{"name":"mars","lockfileVersion":3,"packages":{}}\n',
        'configs/windows_native.yaml': (
            'python_version: 3.11.14\nfrontend_port: 3001\nbackend_port: 8010\n'
            + 'uv_sha256: ' + 'a' * 64 + '\nnode_sha256: ' + 'b' * 64
            + '\ngit_sha256: ' + 'c' * 64 + '\n'
        ).encode(),
    }
    for name, content in dependencies.items():
        _write(root, name, content)
        _write(source, name, content)
    _write(root, 'configs/windows_native.yaml', dependencies['configs/windows_native.yaml'].replace(b'3001', b'3011'))
    _write(root, 'backend/app/main.py', b'VERSION = "old"\n')
    _write(root, 'backend/app/obsolete.py', b'RETIRED = True\n')
    _write(root, 'configs/agents.yaml', b'coding:\n  model: local-choice\n')
    _write(root, 'projects/example/project.json', b'{"owner":"local"}\n')
    _manifest(root, list(dependencies) + ['backend/app/main.py', 'backend/app/obsolete.py',
        'configs/agents.yaml', 'projects/example/project.json'], 'before')
    additions = {
        'backend/app/main.py': b'VERSION = "new"\n',
        'backend/app/execution/local/runner.py': b'LOCAL_RUNNER = True\n',
        'backend/app/模型 (新版).py': b'CHANNELS = 16\n',
        'configs/agents.yaml': b'coding:\n  model: distribution-default\n',
        'configs/new_defaults.yaml': b'enabled: true\n',
        'projects/example/project.json': b'{"owner":"distribution"}\n',
    }
    for name, content in additions.items():
        _write(source, name, content)
    _manifest(source, list(dependencies) + list(additions), 'next')
    preserved = {
        '.env': b'MARS_LOCAL_NOTE=retained\n',
        'dependencies/windows/runtime.bin': b'installed dependency bytes',
        'dependencies/offline/packages.bin': b'downloaded offline dependency bytes',
        'offline/legacy-cache.bin': b'legacy offline cache',
        'local/windows/installation.json': b'{"retained":true}\n',
        'workspace/user-project/source.txt': b'user project source',
        'runs/existing/metrics.json': b'{"measurement":2.5}\n',
        'conversations/existing/messages.json': b'[]\n',
        'knowledge/notes.txt': b'user research notes',
        'projects/example/local-notes.txt': b'local project notes',
    }
    for name, content in preserved.items():
        _write(root, name, content)
    protected = {root / name: (root / name).read_bytes() for name in (
        *preserved, 'configs/agents.yaml', 'configs/windows_native.yaml', 'projects/example/project.json')}
    original_core = {name: (root / name).read_bytes() for name in (
        'backend/app/main.py', 'backend/app/obsolete.py', UPDATER.MANIFEST)}
    return Installation(root, source, root / 'local/core-updates/latest.json', protected, original_core)


def _assert_preserved(value: Installation) -> None:
    assert all(path.read_bytes() == content for path, content in value.protected.items())


def test_current_installed_six_field_stamp_reuses_dependencies(installation: Installation) -> None:
    value = installation
    installed = {**UPDATER.dependency_fingerprint(value.root), 'root': str(value.root)}
    stamp = _write(value.root, 'dependencies/windows/installed.json', json.dumps(installed).encode())
    # A stale legacy stamp cannot override the selected modern environment.
    _write(value.root, 'local/windows/installed.json', b'{"uv.lock":"stale"}')
    before = stamp.read_bytes()
    assert UPDATER.check(value.root, value.source)['version'] == 'next'
    UPDATER.apply(value.root, value.source, value.receipt)
    assert stamp.read_bytes() == before
    _assert_preserved(value)


def test_legacy_installed_stamp_accepts_locks_and_exact_native_config(installation: Installation) -> None:
    value = installation
    modern = value.root / 'dependencies/windows'
    dependency_bytes = (modern / 'runtime.bin').read_bytes()
    (modern / 'runtime.bin').unlink()
    modern.rmdir()
    fingerprint = UPDATER.dependency_fingerprint(value.root)
    installed = {key: fingerprint[key] for key in ('uv.lock', 'frontend/package-lock.json')}
    installed['configs/windows_native.yaml'] = UPDATER.digest(value.root / 'configs/windows_native.yaml')
    stamp = _write(value.root, 'local/windows/installed.json', json.dumps(installed).encode())
    retained = _write(value.root, 'local/windows/runtime.bin', dependency_bytes)
    before = stamp.read_bytes()
    assert UPDATER.check(value.root, value.source)['version'] == 'next'
    UPDATER.apply(value.root, value.source, value.receipt)
    assert stamp.read_bytes() == before
    assert retained.read_bytes() == dependency_bytes


@pytest.mark.parametrize('stale_key', ['windows.python_version', 'uv.lock', 'configs/windows_native.yaml'])
def test_stale_installed_environment_is_rejected_without_reinstall(
    installation: Installation, stale_key: str,
) -> None:
    value = installation
    fingerprint = UPDATER.dependency_fingerprint(value.root)
    if stale_key == 'configs/windows_native.yaml':
        installed = {key: fingerprint[key] for key in ('uv.lock', 'frontend/package-lock.json')}
        installed[stale_key] = '0' * 64
    else:
        installed = dict(fingerprint)
        installed[stale_key] = 'stale-installed-value'
    _write(value.root, 'dependencies/windows/installed.json', json.dumps(installed).encode())
    before = {path.relative_to(value.root): path.read_bytes() for path in value.root.rglob('*') if path.is_file()}
    with pytest.raises(ValueError, match='Installed dependencies do not match'):
        UPDATER.apply(value.root, value.source, value.receipt)
    after = {path.relative_to(value.root): path.read_bytes() for path in value.root.rglob('*') if path.is_file()}
    assert after == before
    assert not value.receipt.exists()


def _cli(value: Installation, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(UPDATER_PATH), '--root', str(value.root), *arguments],
        stdin=subprocess.DEVNULL, capture_output=True, encoding='utf-8', timeout=20, check=False,
        env={**os.environ, 'PYTHONUTF8': '1'})


def _zip(value: Installation, path: Path, prefix: str = 'MARS-feature/') -> Path:
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for file in value.source.rglob('*'):
            if file.is_file():
                archive.write(file, prefix + file.relative_to(value.source).as_posix())
    return path


def test_real_apply_and_rollback_restore_core_and_preserve_user_state(installation: Installation) -> None:
    value = installation
    assert UPDATER.check(value.root, value.source)['version'] == 'next'
    receipt = UPDATER.apply(value.root, value.source, value.receipt)
    assert receipt['phase'] == 'applied'
    assert (value.root / 'backend/app/main.py').read_bytes() == b'VERSION = "new"\n'
    assert not (value.root / 'backend/app/obsolete.py').exists()
    assert (value.root / 'backend/app/execution/local/runner.py').is_file()
    assert (value.root / 'backend/app/模型 (新版).py').is_file()
    assert (value.root / 'configs/new_defaults.yaml').read_bytes() == b'enabled: true\n'
    _assert_preserved(value)
    restored = UPDATER.rollback(value.root, value.receipt)
    assert restored['phase'] == 'rolled_back'
    assert all((value.root / name).read_bytes() == content for name, content in value.original_core.items())
    assert not (value.root / 'backend/app/execution/local/runner.py').exists()
    assert not (value.root / 'configs/new_defaults.yaml').exists()
    _assert_preserved(value)


def test_actual_cli_accepts_github_zip_commit_and_repeat_update(installation: Installation, tmp_path: Path) -> None:
    value = installation
    archive = _zip(value, tmp_path / 'GitHub 核心更新.zip')
    checked = _cli(value, '--source', str(archive), '--check')
    assert checked.returncode == 0, checked.stderr
    assert json.loads(checked.stdout)['status'] == 'compatible'
    assert not value.receipt.exists()
    applied = _cli(value, '--source', str(archive), '--apply')
    assert applied.returncode == 0, applied.stderr
    assert json.loads(applied.stdout)['phase'] == 'applied'
    first = json.loads(value.receipt.read_text())
    duplicate = _cli(value, '--source', str(archive), '--apply')
    assert duplicate.returncode != 0
    assert json.loads(value.receipt.read_text()) == first
    committed = _cli(value, '--commit')
    assert committed.returncode == 0, committed.stderr
    assert json.loads(value.receipt.read_text())['phase'] == 'committed'
    repeated = _cli(value, '--source', str(archive), '--apply')
    assert repeated.returncode == 0, repeated.stderr
    assert json.loads(repeated.stdout)['phase'] == 'applied'
    _assert_preserved(value)


def test_actual_process_lock_rejects_concurrent_update_but_leftover_file_is_not_stale_owner(
    installation: Installation,
) -> None:
    value = installation
    with UPDATER.update_lock(value.root):
        blocked = _cli(value, '--source', str(value.source), '--apply')
        assert blocked.returncode != 0
        assert not value.receipt.exists()
        assert (value.root / 'backend/app/main.py').read_bytes() == value.original_core['backend/app/main.py']
    assert (value.root / 'local/core-updates/updating.lock').is_file()
    applied = _cli(value, '--source', str(value.source), '--apply')
    assert applied.returncode == 0, applied.stderr
    _assert_preserved(value)


@pytest.mark.parametrize('name', [
    '../README.md', '/README.md', 'backend\\app\\main.py', 'backend/app/../main.py',
    'backend/app/file?.py', 'backend/app/file*.py', 'backend/app/file|.py',
    'backend/app/file<.py', 'backend/app/file>.py', 'backend/app/file".py',
    'backend/app/file\x01.py', 'backend/app/CON.py', 'backend/app/nul.txt',
    'backend/app/COM1', 'backend/app/LPT9.txt', 'backend/app/file.py.',
    'backend/app/file.py ', 'dependencies/windows/runtime.bin', 'local/settings.json',
    'runs/user/metrics.json', 'backend/app/node_modules/module.js',
])
def test_windows_unsafe_paths_are_rejected_before_application(name: str) -> None:
    with pytest.raises(ValueError):
        UPDATER.safe_name(name)


def test_legitimate_nested_local_and_unicode_names_remain_supported() -> None:
    for name in ('backend/app/execution/local/runner.py', 'backend/app/模型 (新版).py', 'configs/实验 1.yaml'):
        assert UPDATER.safe_name(name) == name


def test_receipt_dotdot_escape_is_rejected_without_touching_user_configuration(installation: Installation) -> None:
    value = installation
    escaped = value.root / 'local/core-updates/../../configs/agents.yaml'
    result = _cli(value, '--source', str(value.source), '--receipt', str(escaped), '--apply')
    assert result.returncode != 0
    assert not value.receipt.exists()
    assert (value.root / 'backend/app/main.py').read_bytes() == value.original_core['backend/app/main.py']
    _assert_preserved(value)


def test_dependency_change_is_rejected_before_writing_the_installation(installation: Installation) -> None:
    value = installation
    _write(value.root, 'uv.lock', b'version = 1\nrevision = 2\n')
    result = _cli(value, '--source', str(value.source), '--apply')
    assert result.returncode != 0 and 'Dependency versions have changed' in result.stderr
    assert not value.receipt.exists()
    assert (value.root / 'backend/app/main.py').read_bytes() == value.original_core['backend/app/main.py']
    _assert_preserved(value)


def test_payload_corruption_fails_and_unlisted_git_metadata_is_not_copied(installation: Installation) -> None:
    value = installation
    original = (value.source / 'backend/app/main.py').read_bytes()
    _write(value.source, 'backend/app/main.py', b'corrupted bytes\n')
    with pytest.raises(ValueError, match='checksum'):
        UPDATER.apply(value.root, value.source, value.receipt)
    assert not value.receipt.exists()
    assert (value.root / 'backend/app/main.py').read_bytes() == value.original_core['backend/app/main.py']
    _write(value.source, 'backend/app/main.py', original)
    _write(value.source, '.git/config', b'[core]\n repositoryformatversion = 0\n')
    _write(value.source, '.DS_Store', b'Finder metadata')
    _write(value.source, 'README.md', b'unlisted checkout notes\n')
    applied = UPDATER.apply(value.root, value.source, value.receipt)
    assert applied['phase'] == 'applied'
    assert not (value.root / '.git').exists()
    assert not (value.root / '.DS_Store').exists()
    assert not (value.root / 'README.md').exists()
    _assert_preserved(value)


def test_existing_user_configurations_cannot_be_marked_replace(installation: Installation) -> None:
    value = installation
    marker = json.loads((value.source / UPDATER.MANIFEST).read_text())
    for row in marker['files']:
        if row['path'] == 'configs/agents.yaml':
            row['policy'] = 'replace'
    (value.source / UPDATER.MANIFEST).write_text(json.dumps(marker))
    with pytest.raises(ValueError, match='preserved'):
        UPDATER.apply(value.root, value.source, value.receipt)
    assert not value.receipt.exists()
    _assert_preserved(value)


def test_rollback_verifies_all_backups_before_restoring_any_file(installation: Installation) -> None:
    value = installation
    receipt = UPDATER.apply(value.root, value.source, value.receipt)
    before = {row['path']: (value.root / row['path']).read_bytes() if (value.root / row['path']).is_file()
              else None for row in receipt['changes']}
    (Path(receipt['backup']) / 'backend/app/main.py').write_bytes(b'corrupted backup')
    with pytest.raises(ValueError, match='backup checksum'):
        UPDATER.rollback(value.root, value.receipt)
    assert all(((value.root / name).read_bytes() if (value.root / name).is_file() else None) == content
               for name, content in before.items())
    _assert_preserved(value)


def test_real_copy_rechecks_copied_bytes_before_replacing_the_target(tmp_path: Path) -> None:
    source = tmp_path / 'source.py'
    target = tmp_path / 'installed.py'
    source.write_bytes(b'VERSION = "checked"\n')
    expected = UPDATER.digest(source)
    source.write_bytes(b'VERSION = "changed"\n')
    target.write_bytes(b'VERSION = "old"\n')
    with pytest.raises(ValueError):
        UPDATER._copy(source, target, expected_sha256=expected)
    assert target.read_bytes() == b'VERSION = "old"\n'
    assert not list(tmp_path.glob('*.part'))
    UPDATER._copy(source, target, expected_sha256=UPDATER.digest(source))
    assert target.read_bytes() == b'VERSION = "changed"\n'
    assert not list(tmp_path.glob('*.part'))


def test_symbolic_destination_cannot_redirect_core_writes(installation: Installation, tmp_path: Path) -> None:
    value = installation
    target = value.root / 'backend/app/main.py'
    outside = tmp_path / 'external.py'
    outside.write_bytes(b'outside installation\n')
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError as exc:
        if os.name == 'nt' and getattr(exc, 'winerror', None) == 1314:
            pytest.skip('Windows requires symbolic-link privileges for this specific file-link test')
        raise
    with pytest.raises(ValueError, match='link'):
        UPDATER.apply(value.root, value.source, value.receipt)
    assert outside.read_bytes() == b'outside installation\n'
    assert not value.receipt.exists()
    _assert_preserved(value)


def test_manifest_case_duplicates_and_zip_symlinks_are_rejected(installation: Installation, tmp_path: Path) -> None:
    value = installation
    marker = json.loads((value.source / UPDATER.MANIFEST).read_text())
    main = next(row for row in marker['files'] if row['path'] == 'backend/app/main.py')
    marker['files'].append({**main, 'path': 'backend/app/Main.py'})
    (value.source / UPDATER.MANIFEST).write_text(json.dumps(marker))
    with pytest.raises(ValueError):
        UPDATER.check(value.root, value.source)
    archive = tmp_path / 'linked-core.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr(UPDATER.MANIFEST, '{}')
        entry = zipfile.ZipInfo('backend/app/link.py')
        entry.create_system = 3
        entry.external_attr = 0o120777 << 16
        output.writestr(entry, 'outside.py')
    with pytest.raises(ValueError, match='linked'):
        with UPDATER.source_tree(archive):
            raise AssertionError('Linked ZIP must not produce an accepted source tree')
    assert not value.receipt.exists()
    _assert_preserved(value)


def test_reviewed_release_paths_are_all_supported_by_the_updater() -> None:
    names = [line.strip() for line in (SOURCE_ROOT / 'scripts/release/v30_tree_allowlist.txt').read_text().splitlines()
             if line.strip() and not line.lstrip().startswith('#')]
    assert names
    for name in names:
        assert UPDATER.safe_name(name) == name
