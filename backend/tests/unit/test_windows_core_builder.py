"""Real release-file selection and ZIP contracts; no dependency/network substitutes."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType
import zipfile

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location('windows_core_builder_test', ROOT / 'scripts/release/build_windows_core.py')
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CORE = _load()


def _source(tmp_path: Path) -> Path:
    source = tmp_path / 'source'
    files = {
        'uv.lock': '# Concrete lock bytes\n', 'frontend/package-lock.json': '{"lockfileVersion":3}\n',
        'pyproject.toml': '[project]\nname="archive-contract"\n',
        'configs/windows_native.yaml': yaml.safe_dump({'python_version': '3.11.14', 'uv_sha256': 'a' * 64,
            'node_sha256': 'b' * 64, 'git_sha256': 'c' * 64, 'frontend_port': 3001}),
        'configs/execution.yaml': 'execution:\n  paper_static: {}\n',
        'projects/.gitkeep': '', 'backend/app/execution/local/runner.py': 'VALUE = 1\n',
        'backend/app/harness/tools/knowledge/tool.py': 'VALUE = 2\n',
        'frontend/src/app/runs/page.tsx': 'export default function Page() { return null; }\n',
        'deploy/windows-native/README-OFFLINE.md': 'Actual offline product instructions\n',
        'MARS-Windows.cmd': '@echo off\npowershell.exe -File "%~dp0deploy\\windows-native\\Mars.ps1" -Action Start %*\n',
        'README.md': 'Repository README replaced by offline instructions in product assets\n',
    }
    manifest = 'scripts/release/v30_tree_allowlist.txt'
    files[manifest] = '\n'.join([*files, manifest]) + '\n'
    for name, content in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    return source


def test_actual_core_zip_policies_and_shared_portable_bytes(tmp_path: Path) -> None:
    source = _source(tmp_path)
    original_settings = (source / 'configs/execution.yaml').read_bytes()
    archive = tmp_path / 'core.zip'
    result = CORE.build_core(source, archive, version='test-commit')
    assert result['bytes'] == archive.stat().st_size
    assert result['sha256'] == hashlib.sha256(archive.read_bytes()).hexdigest()
    with zipfile.ZipFile(archive) as z:
        manifest = json.loads(z.read('CORE-UPDATE.json'))
        assert manifest['schema'] == 'mars.windows.core.v1'
        assert manifest['version'] == 'test-commit'
        assert set(manifest['dependency_fingerprint']) == {'uv.lock', 'frontend/package-lock.json',
            'windows.python_version', 'windows.uv_sha256', 'windows.node_sha256', 'windows.git_sha256'}
        assert set(z.namelist()) == {'CORE-UPDATE.json', *(row['path'] for row in manifest['files'])}
        for row in manifest['files']:
            data = z.read(row['path'])
            assert len(data) == row['bytes'] and hashlib.sha256(data).hexdigest() == row['sha256']
            assert row['policy'] == ('seed' if row['path'].split('/')[0] in {'configs', 'projects'} else 'replace')
        assert b'-OfflineRequired' in z.read('MARS-Windows.cmd')
        assert z.read('README.md') == z.read('deploy/windows-native/README-OFFLINE.md')
        assert yaml.safe_load(z.read('configs/execution.yaml'))['execution']['local_commands'] == []
    assert (source / 'configs/execution.yaml').read_bytes() == original_settings


def test_fingerprint_binds_only_six_declared_dependency_fields(tmp_path: Path) -> None:
    source = _source(tmp_path)
    first = CORE.dependency_fingerprint(source)
    (source / 'pyproject.toml').write_text('[project]\nname="new-core-only-description"\n')
    config = source / 'configs/windows_native.yaml'
    value = yaml.safe_load(config.read_text())
    value['frontend_port'] = 3011
    config.write_text(yaml.safe_dump(value))
    assert CORE.dependency_fingerprint(source) == first
    (source / 'uv.lock').write_text('# changed locked environment\n')
    assert CORE.dependency_fingerprint(source) != first


@pytest.mark.parametrize('path', ['runs/results.json', 'dependencies/offline/manifest.json', '.env.local',
    'backend/node_modules/library.js', 'configs/credentials/key.json', '../outside.txt', 'backend/CON.py'])
def test_core_manifest_cannot_select_state_or_escape_paths(tmp_path: Path, path: str) -> None:
    source = _source(tmp_path)
    target = source / path
    if path != 'backend/CON.py':
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('private or generated fixture\n')
    with (source / 'scripts/release/v30_tree_allowlist.txt').open('a') as stream:
        stream.write(path + '\n')
    with pytest.raises(ValueError):
        CORE.build_core(source, tmp_path / 'rejected.zip', version='refused')
    assert not (tmp_path / 'rejected.zip').exists()


def test_source_secrets_and_case_duplicate_paths_are_rejected(tmp_path: Path) -> None:
    source = _source(tmp_path)
    target = source / 'backend/app/execution/local/runner.py'
    target.write_text('KEY = "' + 'sk-' + 'x' * 40 + '"\n')
    with pytest.raises(ValueError, match='Secret-shaped'):
        CORE.source_files(source)
    target.write_text('VALUE = 1\n')
    manifest = source / 'scripts/release/v30_tree_allowlist.txt'
    with manifest.open('a') as stream:
        stream.write('UV.LOCK\n')
    with pytest.raises(ValueError, match='duplicate'):
        CORE.source_files(source)


def test_current_real_repository_manifest_accepts_legitimate_nested_state_names(tmp_path: Path) -> None:
    result = CORE.build_core(ROOT, tmp_path / 'actual-core.zip', version='actual-source-fixture')
    assert result['files'] > 700
    with zipfile.ZipFile(result['archive']) as z:
        assert 'backend/app/execution/local/runner.py' in z.namelist()
        assert any(name.startswith('backend/app/harness/tools/knowledge/') for name in z.namelist())
        assert not any(name.startswith(('dependencies/', 'runs/', 'workspace/')) for name in z.namelist())
