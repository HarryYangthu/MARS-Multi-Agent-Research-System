"""Actual filesystem upgrades and archive assembly; no downloads or execution substitutes."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType
import zipfile

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[3]


def _load(name: str, file: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts/release' / file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


UPGRADE = _load('release_memory_upgrade', 'upgrade-research-memory.py')
OFFLINE = _load('release_offline_builder', 'build_windows_offline.py')


def _upgrade_tree(tmp_path: Path) -> tuple[Path, Path]:
    source, runtime = tmp_path / 'source', tmp_path / 'runtime'
    for root in (source, runtime):
        (root / 'configs').mkdir(parents=True)
    (source / 'scripts/release').mkdir(parents=True)
    (source / 'configs/tools.yaml').write_text(yaml.safe_dump({'tools': {
        key: {'enabled': True} for key in ('knowledge.research_memory', 'knowledge.research_memory_read')}}))
    (source / 'configs/agents.yaml').write_text('commander:\n  tools: []\n')
    (source / 'configs/required.yaml').write_text('policy: actual new runtime resource\n')
    (source / 'configs/existing.yaml').write_text('setting: source default\n')
    (source / 'scripts/release/runtime_assets.txt').write_text(
        'configs/agents.yaml\nconfigs/tools.yaml\nconfigs/required.yaml\nconfigs/existing.yaml\n')
    (runtime / '.desktop-seed.json').write_text('{"fingerprint":"previous"}')
    (runtime / 'configs/agents.yaml').write_text('commander:\n  model: custom-user-model\n  tools: [existing]\n')
    (runtime / 'configs/tools.yaml').write_text('tools:\n  existing:\n    enabled: false\n')
    (runtime / 'configs/existing.yaml').write_text('setting: preserved user override\n')
    (runtime / 'user-result.json').write_text('{"measured":true}')
    return source, runtime


def test_upgrade_copies_missing_declared_assets_and_preserves_user_files(tmp_path: Path) -> None:
    source, runtime = _upgrade_tree(tmp_path)
    old_agents = (runtime / 'configs/agents.yaml').read_bytes()
    result = UPGRADE.upgrade(runtime, source)
    assert result['copied_assets'] == ['configs/required.yaml']
    assert (runtime / 'configs/required.yaml').read_bytes() == (source / 'configs/required.yaml').read_bytes()
    assert (runtime / 'configs/existing.yaml').read_text() == 'setting: preserved user override\n'
    assert json.loads((runtime / 'user-result.json').read_text()) == {'measured': True}
    agents = yaml.safe_load((runtime / 'configs/agents.yaml').read_text())
    assert agents['commander']['model'] == 'custom-user-model'
    assert agents['commander']['tools'] == ['existing', 'knowledge.research_memory', 'knowledge.research_memory_read']
    tools = yaml.safe_load((runtime / 'configs/tools.yaml').read_text())
    assert tools['tools']['existing']['enabled'] is False
    assert (Path(result['backup']) / 'configs/agents.yaml').read_bytes() == old_agents
    marker = json.loads((runtime / '.desktop-seed.json').read_text())
    assert all((runtime / row['relative']).is_file() for row in marker['files'])
    assert marker['fingerprint'] != 'previous'


@pytest.mark.parametrize('kind', ['traversal', 'source-link', 'destination-link', 'parent-link'])
def test_upgrade_rejects_unsafe_assets_before_marking_complete(tmp_path: Path, kind: str) -> None:
    source, runtime = _upgrade_tree(tmp_path)
    marker = (runtime / '.desktop-seed.json').read_bytes()
    agents = (runtime / 'configs/agents.yaml').read_bytes()
    outside = tmp_path / 'outside'; outside.mkdir()
    (outside / 'required.yaml').write_text('outside\n')
    if kind == 'traversal':
        with (source / 'scripts/release/runtime_assets.txt').open('a') as stream:
            stream.write('../outside/required.yaml\n')
    elif kind == 'source-link':
        (source / 'configs/required.yaml').unlink()
        (source / 'configs/required.yaml').symlink_to(outside / 'required.yaml')
    elif kind == 'destination-link':
        (runtime / 'configs/required.yaml').symlink_to(outside / 'required.yaml')
    else:
        (source / 'scripts/release/runtime_assets.txt').write_text('linked/required.yaml\n')
        (source / 'linked').mkdir()
        (source / 'linked/required.yaml').write_text('inside\n')
        (runtime / 'linked').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        UPGRADE.upgrade(runtime, source)
    assert (runtime / '.desktop-seed.json').read_bytes() == marker
    assert (runtime / 'configs/agents.yaml').read_bytes() == agents
    assert not (runtime / 'upgrades').exists()
    assert (outside / 'required.yaml').read_text() == 'outside\n'


def _cache_tree(tmp_path: Path) -> tuple[Path, Path]:
    source, cache = tmp_path / 'source', tmp_path / 'prepared'
    (source / 'configs').mkdir(parents=True)
    cache.mkdir()
    for name in ('uv', 'node', 'git', 'python', 'zcode', 'npm-cache'):
        with zipfile.ZipFile(cache / (name + '.zip'), 'w') as archive:
            archive.writestr('checksum-fixture.txt', name)
    (cache / 'vc_redist.x64.exe').write_bytes(b'checksum fixture; not an executed installer')
    (cache / 'requirements.windows.txt').write_text('# checksum fixture; nothing installed\n')
    pins = {name + '_sha256': hashlib.sha256((cache / (name + '.zip')).read_bytes()).hexdigest()
            for name in ('uv', 'node', 'git')}
    pins['python_version'] = '3.11.14'
    (source / 'configs/windows_native.yaml').write_text(yaml.safe_dump(pins))
    (source / 'uv.lock').write_text('# Real archive-contract fixture; no installation executed\n')
    (source / 'frontend').mkdir(exist_ok=True)
    (source / 'frontend/package-lock.json').write_text('{}\n')
    (source / 'scripts/release').mkdir(parents=True)
    (source / 'scripts/release/v30_tree_allowlist.txt').write_text('configs/windows_native.yaml\nconfigs/execution.yaml\nuv.lock\nfrontend/package-lock.json\nscripts/release/v30_tree_allowlist.txt\n')
    (source / 'configs/execution.yaml').write_text('execution:\n  paper_static: {}\n')
    (source / '.env.local').write_text('PRIVATE_TOKEN=must-not-transfer\n')
    (source / 'frontend/.next').mkdir(parents=True)
    (source / 'frontend/.next/generated.js').write_text('generated build output')
    _cache_manifest(cache, source)
    return source, cache


def _cache_manifest(cache: Path, source: Path | None = None) -> None:
    files = [{'path': str(path.relative_to(cache)), 'bytes': path.stat().st_size,
              'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
             for path in sorted(cache.iterdir()) if path.name != 'manifest.json']
    fingerprint = OFFLINE.dependency_fingerprint(source) if source is not None else json.loads((cache / 'manifest.json').read_text())['dependency_fingerprint']
    (cache / 'manifest.json').write_text(json.dumps({'schema': 'mars.windows.dependencies.v1',
        'platform': 'windows-x64', 'python': '3.11.14', 'dependency_fingerprint': fingerprint, 'files': files}))


def test_original_cache_is_verified_and_actual_archive_excludes_private_generated_files(tmp_path: Path) -> None:
    source, cache = _cache_tree(tmp_path)
    original = (cache / 'manifest.json').read_bytes()
    OFFLINE.verify_prepared_dependencies(cache, source)
    output = tmp_path / 'package'
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/release/build_windows_offline.py'),
        '--source', str(source), '--source-profile', 'repository', '--output', str(output),
        '--prepared-dependencies', str(cache)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert (cache / 'manifest.json').read_bytes() == original
    with zipfile.ZipFile(output / 'MARS-Product-Windows-Offline.zip') as archive:
        assert 'MARS-Product/WINDOWS-OFFLINE.json' in archive.namelist()
        assert not any('.env.local' in name or '/.next/' in name for name in archive.namelist())


@pytest.mark.parametrize('kind', ['corrupt', 'resigned-pin', 'missing', 'traversal', 'duplicate', 'symlink', 'unlisted', 'unbound', 'lock-changed'])
def test_bad_prepared_cache_is_not_recertified(tmp_path: Path, kind: str) -> None:
    source, cache = _cache_tree(tmp_path)
    if kind in {'corrupt', 'resigned-pin'}:
        (cache / 'uv.zip').write_bytes(b'changed archive')
        if kind == 'resigned-pin':
            _cache_manifest(cache)
    elif kind == 'missing':
        (cache / 'node.zip').unlink()
    elif kind == 'symlink':
        original = cache / 'uv.zip'; outside = tmp_path / 'uv.zip'
        original.replace(outside); original.symlink_to(outside)
    elif kind == 'lock-changed':
        (source / 'uv.lock').write_text('# Changed actual lock input\n')
    elif kind == 'unbound':
        manifest = json.loads((cache / 'manifest.json').read_text())
        manifest.pop('dependency_fingerprint')
        (cache / 'manifest.json').write_text(json.dumps(manifest))
    elif kind == 'unlisted':
        (cache / 'unlisted').mkdir()
        (cache / 'unlisted/manifest.json').write_text('{}')
    else:
        manifest = json.loads((cache / 'manifest.json').read_text())
        if kind == 'traversal':
            manifest['files'][0]['path'] = '../outside.zip'
        else:
            manifest['files'].append(manifest['files'][0])
        (cache / 'manifest.json').write_text(json.dumps(manifest))
    before = (cache / 'manifest.json').read_bytes()
    with pytest.raises(ValueError):
        OFFLINE.verify_prepared_dependencies(cache, source)
    assert (cache / 'manifest.json').read_bytes() == before
    if kind == 'corrupt':
        output = tmp_path / 'rejected-package'
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/release/build_windows_offline.py'),
            '--source', str(source), '--source-profile', 'repository', '--output', str(output),
            '--prepared-dependencies', str(cache)], capture_output=True, text=True, timeout=30)
        assert result.returncode != 0
        assert not (output / 'MARS-Product/WINDOWS-OFFLINE.json').exists()
        assert not (output / 'MARS-Product-Windows-Offline.zip').exists()


@pytest.mark.parametrize('legacy_metadata', [False, True])
def test_actual_api_configuration_completes_without_packaged_project_or_keeps_legacy_intake(
    tmp_path: Path, legacy_metadata: bool,
) -> None:
    root = tmp_path / 'configuration'
    (root / 'configs').mkdir(parents=True)
    configs = {
        'models.yaml': {'providers': {'local_vllm': {
            'api_key_env': 'MARS_CONFIGURATION_TEST_KEY', 'base_url': 'http://127.0.0.1:8000/v1'}}},
        'windows_native.yaml': {'api_local_endpoint': 'http://127.0.0.1:8000/v1'},
        'agents.yaml': {'commander': {'model': {'model': 'previous-model'}, 'loop': {}}},
        'idea_focused.yaml': {'author': {}},
        'cli_research.yaml': {'generation': {}},
        'execution.yaml': {'execution': {'paper_static': {'enabled': False, 'data_path': 'old-data'}}},
    }
    for name, body in configs.items():
        (root / 'configs' / name).write_text(yaml.safe_dump(body), encoding='utf-8')
    answers = ['4', '', 'local-config-only', 'LOCAL-CONFIG-ONLY', 'n']
    code = tmp_path / 'scientific-source'
    code.mkdir()
    (code / 'train_static.py').write_text('# Actual intake fixture; never executed.\n', encoding='utf-8')
    link = root / 'projects/pimc/repo_link.yaml'
    if legacy_metadata:
        link.parent.mkdir(parents=True)
        link.write_text('repo_path: previous-source\n', encoding='utf-8')
        answers.extend([str(code), ''])
    launcher = (
        'import runpy, sys; from pathlib import Path; '
        'module = runpy.run_path(sys.argv[1]); module["main"](Path(sys.argv[2]))'
    )
    env = os.environ.copy()
    env['PYTHONPATH'] = str(ROOT / 'backend') + os.pathsep + env.get('PYTHONPATH', '')
    env['PYTHONIOENCODING'] = 'utf-8'
    result = subprocess.run(
        [sys.executable, '-c', launcher, str(ROOT / 'deploy/windows-native/configure_api.py'), str(root)],
        input='\n'.join(answers) + '\n', capture_output=True, text=True, encoding='utf-8',
        env=env, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads((root / 'local/windows/api-configured.json').read_text(encoding='utf-8'))
    assert receipt['provider'] == 'local_vllm'
    assert receipt['endpoint'] == 'http://127.0.0.1:8000/v1'
    agents = yaml.safe_load((root / 'configs/agents.yaml').read_text(encoding='utf-8'))
    assert agents['commander']['model']['model'] == 'local-config-only'
    assert 'LOCAL-CONFIG-ONLY' not in result.stdout + result.stderr
    assert 'MARS_CONFIGURATION_TEST_KEY="LOCAL-CONFIG-ONLY"' in (root / '.env.local').read_text(encoding='utf-8')
    if legacy_metadata:
        assert 'PIMC 源码目录' in result.stdout
        assert yaml.safe_load(link.read_text(encoding='utf-8'))['repo_path'] == code.resolve().as_posix()
        settings = yaml.safe_load((root / 'configs/execution.yaml').read_text(encoding='utf-8'))
        assert settings['execution']['paper_static']['repo_path'] == code.resolve().as_posix()
        assert settings['execution']['paper_static']['data_path'] == ''
        assert settings['execution']['paper_static']['enabled'] is False
    else:
        assert 'PIMC 源码目录' not in result.stdout
        assert '产品版不预置研究项目' in result.stderr
        assert not link.exists()
        assert yaml.safe_load((root / 'configs/execution.yaml').read_text(encoding='utf-8')) == configs['execution.yaml']
