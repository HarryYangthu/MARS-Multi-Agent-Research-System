"""Add research-memory tool grants while preserving user settings and data."""
from __future__ import annotations
import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import yaml


def _asset_path(root: Path, name: str, *, required: bool = True) -> Path:
    relative = Path(name)
    if (not name or relative.is_absolute() or '\\' in name or ':' in name
            or any(part in {'.', '..', ''} for part in name.split('/'))):
        raise ValueError('Runtime asset must be a safe relative file path')
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Runtime asset root is unavailable or symbolic')
    base = root.resolve(strict=True)
    target = base
    for part in relative.parts:
        target /= part
        if target.is_symlink():
            raise ValueError('Runtime assets cannot traverse symbolic links')
        if target != base / relative and target.exists() and not target.is_dir():
            raise ValueError('Runtime asset parent is not a directory')
    if not target.resolve().is_relative_to(base):
        raise ValueError('Runtime asset escapes its root')
    if target.exists() and not target.is_file() or required and not target.is_file():
        raise ValueError('Runtime asset is not an available regular file: ' + name)
    return target


def _mapping(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Runtime configuration must be a mapping: ' + path.name)
    return value


def upgrade(runtime: Path, source: Path) -> dict[str, object]:
    marker = _asset_path(runtime, '.desktop-seed.json')
    manifest = _asset_path(source, 'scripts/release/runtime_assets.txt')
    paths = [line.strip() for line in manifest.read_text(encoding='utf-8').splitlines()
             if line.strip() and not line.lstrip().startswith('#')]
    if not paths or len({name.casefold() for name in paths}) != len(paths):
        raise ValueError('Runtime asset manifest is empty or contains duplicate paths')
    # Validate every declared source and destination before touching user files.
    assets = [(name, _asset_path(source, name), _asset_path(runtime, name, required=False)) for name in paths]
    files = [{'relative': name, 'sha256': hashlib.sha256(origin.read_bytes()).hexdigest()}
             for name, origin, _ in assets]
    agent_path = _asset_path(runtime, 'configs/agents.yaml')
    tool_path = _asset_path(runtime, 'configs/tools.yaml')
    agents = _mapping(agent_path)
    tools = _mapping(tool_path)
    defaults = _mapping(_asset_path(source, 'configs/tools.yaml'))
    target_tools = tools.get('tools', tools)
    default_tools = defaults.get('tools', defaults)
    if not isinstance(target_tools, dict) or not isinstance(default_tools, dict):
        raise ValueError('Runtime tools must be a mapping')
    names = ('knowledge.research_memory', 'knowledge.research_memory_read')
    if any(not isinstance(default_tools.get(name), dict) for name in names):
        raise ValueError('Source research-memory tool configuration is unavailable')
    for role in ('commander', 'idea', 'idea_research', 'experiment', 'coding', 'execution', 'writing'):
        if role not in agents:
            continue
        if not isinstance(agents[role], dict) or not isinstance(agents[role].get('tools', []), list):
            raise ValueError('Agent tool grants must be lists')
        granted = agents[role].setdefault('tools', [])
        for name in names:
            if name not in granted:
                granted.append(name)
    for name in names:
        if name not in target_tools:
            target_tools[name] = default_tools[name]
    stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup=runtime/"upgrades"/(stamp+"-research-memory")
    _asset_path(runtime, backup.relative_to(runtime).as_posix() + '/receipt.json', required=False)
    backup.mkdir(parents=True,exist_ok=False)
    for name in ("configs/agents.yaml","configs/tools.yaml",".desktop-seed.json"):
        target=backup/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(runtime/name,target)
    from app.harness.persistence import atomic_write_text,atomic_write_json
    copied = []
    for name, origin, destination in assets:
        _asset_path(source, name)
        destination = _asset_path(runtime, name, required=False)
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, destination)
            copied.append(name)
    atomic_write_text(_asset_path(runtime, 'configs/agents.yaml'),yaml.safe_dump(agents,allow_unicode=True,sort_keys=False))
    atomic_write_text(_asset_path(runtime, 'configs/tools.yaml'),yaml.safe_dump(tools,allow_unicode=True,sort_keys=False))
    for row in files:
        if hashlib.sha256(_asset_path(source, row['relative']).read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('Source runtime assets changed during upgrade')
        destination = _asset_path(runtime, row['relative'])
        if row['relative'] in copied and hashlib.sha256(destination.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('Copied runtime asset checksum mismatch')
    # Same source-default fingerprint convention as runtime-assets.cjs;
    # user settings are deliberately maintained as overlays, never replaced.
    fingerprint=hashlib.sha256(json.dumps(files,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
    atomic_write_json(_asset_path(runtime, '.desktop-seed.json'),{"fingerprint":fingerprint,"files":files})
    receipt: dict[str, object]={"feature":"research-memory","backup":str(backup),"tools":list(names),"copied_assets":copied,"preserved":"models, credentials, bindings, runs, conversations, user tool settings"}
    atomic_write_json(backup/"receipt.json",receipt)
    return receipt


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("runtime",type=Path)
    parser.add_argument("--source",type=Path,default=Path(__file__).resolve().parents[2])
    args=parser.parse_args()
    print(json.dumps(upgrade(args.runtime.resolve(),args.source.resolve()),ensure_ascii=False))
