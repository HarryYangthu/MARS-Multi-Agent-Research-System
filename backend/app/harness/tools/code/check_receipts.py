"""Reuse genuine successful Coding checks within one run and host process.

The source boundary is Git-visible tracked and untracked files. Git-ignored
generated files are excluded; selected external data is separately hashed.
Unknown, oversized or symbolic inputs conservatively run the check again.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import time
from typing import Any
import uuid

from app.harness.persistence import atomic_write_json
from app.harness.tools.config import CommandSpec, ToolConfig
from app.harness.tools.git_branch import git
from app.harness.tools.registry import ToolContext
from app.settings import repo_root

_SESSION = uuid.uuid4().hex
_MAX_SOURCE_BYTES = 64 * 1024 * 1024
_MAX_DATA_BYTES = 1024 * 1024 * 1024


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _file(path: Path, maximum: int) -> dict[str, Any]:
    before = path.stat()
    if path.is_symlink() or not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
        raise ValueError('check input is not a bounded regular file')
    with path.open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError('check input changed while hashing')
    return {'path': str(path.resolve()), 'bytes': before.st_size, 'mode': stat.S_IMODE(before.st_mode),
            'sha256': checksum}


def _source(root: Path) -> dict[str, Any]:
    if Path(git(root, 'rev-parse', '--show-toplevel')).resolve() != root:
        raise ValueError('check root must be its own Git checkout')
    names = sorted(set(git(root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard').split('\0')) - {''})
    if len(names) > 10000:
        raise ValueError('check source snapshot exceeds its file budget')
    files: list[dict[str, Any]] = []
    remaining = _MAX_SOURCE_BYTES
    for name in names:
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('check source path escapes its checkout')
        path = root / relative
        if any(part.is_symlink() for part in (path, *path.parents) if part.is_relative_to(root)):
            raise ValueError('symbolic source cannot be reused')
        if not path.exists():
            files.append({'relative_path': name, 'state': 'deleted'})
            continue
        row = _file(path, remaining)
        remaining -= row['bytes']
        files.append({'relative_path': name, **row})
    return {'head': git(root, 'rev-parse', 'HEAD'), 'files': files,
            'ignored_files': 'Git-ignored generated/cache files are outside this snapshot'}


def _dependencies(executable: str) -> dict[str, Any]:
    """Bind the venv and installed distribution records, not an entire library tree."""
    prefix = Path(executable).absolute().parent.parent
    config = prefix / 'pyvenv.cfg'
    if not config.is_file() or 'include-system-site-packages = false' not in config.read_text().casefold():
        raise ValueError('check reuse requires a self-contained registered Python venv')
    sites = sorted(prefix.glob('lib/python*/site-packages'))
    if (prefix / 'Lib/site-packages').is_dir():
        sites.append(prefix / 'Lib/site-packages')
    if not sites or any(site.is_symlink() for site in sites):
        raise ValueError('check dependency environment cannot be bound')
    records = []
    remaining = 16 * 1024 * 1024
    for site in sites:
        if list(site.glob('*.egg-link')):
            raise ValueError('editable check dependencies cannot be reused safely')
        distributions = sorted(site.glob('*.dist-info'))
        for distribution in distributions:
            direct = distribution / 'direct_url.json'
            if direct.is_file() and json.loads(direct.read_text()).get('dir_info', {}).get('editable'):
                raise ValueError('editable check dependencies cannot be reused safely')
            if not (distribution / 'METADATA').is_file() or not (distribution / 'RECORD').is_file():
                raise ValueError('installed check distribution has incomplete metadata')
            for name in ('METADATA', 'RECORD', 'INSTALLER', 'direct_url.json'):
                path = distribution / name
                if path.is_file():
                    record = _file(path, remaining); remaining -= record['bytes']; records.append(record)
        for path in sorted(site.glob('*.pth')):
            record = _file(path, remaining); remaining -= record['bytes']; records.append(record)
    if len(records) > 4000:
        raise ValueError('check dependency metadata exceeds its file budget')
    return {'venv': _file(config, 65536), 'distribution_records': records,
            'scope': 'Installed distribution METADATA/RECORD, installer/direct-url records and .pth; not library source contents'}


def _command_inputs(root: Path, argv: tuple[str, ...]) -> list[dict[str, Any]]:
    if '-c' in argv or '-e' in argv:
        raise ValueError('inline command imports cannot be bound for reuse')
    files = []
    scripts = {'.py', '.pyw', '.sh', '.bash', '.zsh', '.js', '.mjs', '.cjs', '.ps1', '.cmd', '.bat'}
    for argument in argv[1:]:
        if argument.startswith('-'):
            continue
        path = Path(argument)
        path = path if path.is_absolute() else root / path
        if path.is_dir() and not path.resolve().is_relative_to(root):
            raise ValueError('external command directories have unbound check inputs')
        if path.is_file():
            files.append(_file(path, _MAX_SOURCE_BYTES))
            if path.suffix.casefold() in scripts and not path.resolve().is_relative_to(root):
                # Hash the identifiable file, but its sibling imports are not
                # covered by this checkout. Run external scripts every time.
                raise ValueError('external check script dependencies are outside the source snapshot')
        elif path.suffix.casefold() in scripts:
            raise ValueError('registered check script is unavailable')
    return files


@dataclass(frozen=True)
class CheckIdentity:
    fingerprint: str
    document: dict[str, Any]
    ctx: ToolContext
    root: Path
    kind: str
    command: CommandSpec
    argv: tuple[str, ...]
    environment: dict[str, str]
    policy: ToolConfig
    timeout: float
    run_root: Path


def build_check_identity(ctx: ToolContext, root: Path, kind: str, command: CommandSpec,
                         argv: tuple[str, ...], environment: dict[str, str],
                         policy: ToolConfig, timeout: float) -> CheckIdentity | None:
    """No evidence or uncertain inputs means a real check, never a fake pass."""
    try:
        root = root.resolve(strict=True)
        run_root = Path(str(ctx.extra.get('run_root') or repo_root() / 'runs' / ctx.run_id)).resolve(strict=True)
        if (not ctx.run_id or not ctx.project or kind not in {'lint', 'test'} or not argv
                or environment.get('MARS_CODE_ROOT') != str(root)
                or environment.get('MARS_RUN_ROOT') != str(run_root)
                or environment.get('MARS_RUN_ID') != ctx.run_id
                or environment.get('MARS_PROJECT') != ctx.project):
            return None
        selected = run_root / 'input/selected_data_source.json'
        selection: dict[str, Any] | None = None
        if selected.exists() or selected.is_symlink():
            selection = _file(selected, 65536)
            raw = json.loads(selected.read_text())
            stored = Path(raw['stored_path'])
            stored = (stored if stored.is_absolute() else root / stored).resolve(strict=True)
            bound = environment.get('MARS_TEST_DATA_PATH') or environment.get('MARS_DATA_PATH')
            project = environment.get('MARS_TEST_PROJECT_ROOT') or environment.get('MARS_PROJECT_ROOT')
            if bound != str(stored) or not project or not stored.is_relative_to(Path(project).resolve()):
                return None
            selection = {'selection': selection, 'data': _file(stored, _MAX_DATA_BYTES)}
        elif any(environment.get(key) for key in ('MARS_TEST_PROJECT_ROOT', 'MARS_PROJECT_ROOT',
                                                 'MARS_TEST_DATA_PATH', 'MARS_DATA_PATH')):
            return None  # A check could otherwise read changing, unbound project data.
        host_configs = []
        for variable, name in (('MARS_EXECUTION_CONFIG_PATH', 'execution.yaml'), ('MARS_TOOLS_CONFIG_PATH', 'tools.yaml')):
            path = Path(os.environ.get(variable) or repo_root() / 'configs' / name)
            if path.is_file():
                host_configs.append(_file(path, 2 * 1024 * 1024))
        interpreter = Path(argv[0]).resolve(strict=True)
        document = {'schema': 'coding.check_identity.v1', 'project': ctx.project, 'run_id': ctx.run_id,
            'run_root': str(run_root), 'root': str(root), 'kind': kind, 'command': asdict(command),
            'argv': list(argv), 'interpreter': _file(interpreter, _MAX_SOURCE_BYTES),
            'command_files': _command_inputs(root, argv),
            'dependencies': _dependencies(argv[0]),
            'environment_sha256': _digest(environment), 'policy': asdict(policy), 'timeout': timeout,
            'host_configs': host_configs, 'source': _source(root), 'selected_data': selection}
        document = json.loads(json.dumps(document, sort_keys=True, allow_nan=False))
        return CheckIdentity(_digest(document), document, ctx, root, kind, command, argv,
                             dict(environment), policy, timeout, run_root)
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _index(identity: CheckIdentity) -> Path:
    path = identity.run_root / 'coding/checks/index' / (identity.fingerprint + '.json')
    if any(p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(identity.run_root)):
        raise ValueError('check receipt index cannot be symbolic')
    return path


def reuse_check(identity: CheckIdentity | None) -> dict[str, Any] | None:
    if identity is None:
        return None
    try:
        index = _index(identity)
        if index.is_symlink() or not index.is_file() or index.stat().st_size > 65536:
            return None
        raw = json.loads(index.read_text())
        path = Path(raw['receipt_path'])
        base = (identity.run_root / 'coding/checks').resolve()
        if (not path.is_absolute() or path.is_symlink() or not path.resolve().is_relative_to(base)
                or any(p.is_symlink() for p in path.parents if p.is_relative_to(base))):
            return None
        proof = _file(path, 2 * 1024 * 1024)
        if proof['sha256'] != raw['receipt_sha256']:
            return None
        receipt = json.loads(path.read_text())
        if (receipt.get('schema') != 'coding.check_receipt.v1' or receipt.get('host_session') != _SESSION
                or receipt.get('host_pid') != os.getpid() or receipt.get('identity') != identity.document
                or receipt.get('fingerprint') != identity.fingerprint or receipt.get('purpose') != 'configured_code_check'
                or type(receipt.get('pid')) is not int or receipt['pid'] <= 0
                or type(receipt['result'].get('returncode')) is not int or receipt['result']['returncode'] != 0):
            return None
        return {**receipt['result'], 'reused': True, 'receipt_path': str(path),
                'receipt_sha256': proof['sha256'], 'check_started': False}
    except (OSError, ValueError, TypeError, KeyError):
        return None


def record_check(identity: CheckIdentity | None, result: dict[str, Any], *, pid: int,
                 duration_seconds: float) -> dict[str, Any]:
    if identity is None:
        return result
    try:
        if type(result.get('returncode')) is not int or result['returncode'] != 0 or pid <= 0:
            _index(identity).unlink(missing_ok=True)
            return result
        current = build_check_identity(identity.ctx, identity.root, identity.kind, identity.command,
                                       identity.argv, identity.environment, identity.policy, identity.timeout)
        if current is None or current.fingerprint != identity.fingerprint:
            _index(identity).unlink(missing_ok=True)
            return result
        path = identity.run_root / 'coding/checks' / uuid.uuid4().hex / 'receipt.json'
        if any(p.is_symlink() for p in path.parents if p.is_relative_to(identity.run_root)):
            return result
        atomic_write_json(path, {'schema': 'coding.check_receipt.v1', 'fingerprint': identity.fingerprint,
            'identity': identity.document, 'host_session': _SESSION, 'host_pid': os.getpid(),
            'pid': pid, 'duration_seconds': duration_seconds, 'purpose': 'configured_code_check',
            'result': result, 'recorded_at_unix': time.time()})
        checksum = _file(path, 2 * 1024 * 1024)['sha256']
        atomic_write_json(_index(identity), {'receipt_path': str(path), 'receipt_sha256': checksum})
        return {**result, 'reused': False, 'receipt_path': str(path), 'receipt_sha256': checksum,
                'check_started': True}
    except (OSError, ValueError, TypeError, KeyError):
        return result  # A cache problem must not turn a genuine check into failure.
