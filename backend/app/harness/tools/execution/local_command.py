"""Host-configured real command execution with request-bound measurement receipts.

Commands read MARS_JOB_REQUEST and write MARS_RESULT_PATH plus actual evidence
files. Stdout, a zero exit code, or old metrics alone cannot establish success.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
import hashlib
import json
import math
import asyncio
from pathlib import Path, PureWindowsPath
from fnmatch import fnmatchcase
import time
import shutil
import tempfile
from typing import Any
import uuid

from jsonschema import validate

from app.harness.persistence import atomic_write_json, atomic_write_text
from app.harness.tools.config import load_execution_config, tool_config
from app.harness.tools.process_runtime import CapturedProcessStream, ProcessOutputCapture, start_process, stream_process_output

LOCAL_COMMAND_CONTROL_FILES = frozenset({
    "result.json", "job.json", "execution_receipt.json", "stdout.log", "stderr.log",
    "submission.json", "state.json", "stop.json", "worker.lock", "control.lock",
})

@dataclass(frozen=True)
class LocalCommandJob:
    run_id: str
    experiment_id: str
    project: str
    run_root: Path
    config: dict[str, Any] = field(default_factory=dict)
    seed: int | None = None
    steps: int = 1
    command_id: str = ""


@dataclass(frozen=True)
class LocalCommandResult:
    status: str
    duration_seconds: float
    metrics: dict[str, float]
    loss_curve: list[float]
    fingerprint_hash: str
    artifacts: list[dict[str, Any]]
    error: str = ""


_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["schema", "invocation_id", "run_id", "experiment_id", "status", "metrics", "evidence_paths"],
    "properties": {
        "schema": {"const": "local_command_result.v1"},
        "invocation_id": {"type": "string"}, "run_id": {"type": "string"},
        "experiment_id": {"type": "string"}, "status": {"const": "completed"},
        "metrics": {"type": "object", "minProperties": 1, "additionalProperties": {"type": "number"}},
        "evidence_paths": {"type": "array", "minItems": 1, "maxItems": 32,
                           "uniqueItems": True, "items": {"type": "string", "minLength": 1}},
        "loss_curve": {"type": "array", "items": {"type": "number"}},
    },
}


def local_command_protocol() -> dict[str, Any]:
    """Document the actual file envelope and result validator for code delivery."""
    return {
        'request': {
            'delivery': 'MARS_JOB_REQUEST 是 UTF-8 JSON 文件的绝对路径，不是 JSON 文本；先读取文件再解析。',
            'schema': 'local_command_request.v1',
            'fields': ['schema', 'invocation_id', 'run_id', 'experiment_id', 'project',
                       'config', 'seed', 'steps', 'output_dir'],
            'config_location': 'execution_jobs.config 在 request["config"] 中，不在请求顶层。',
            'data_binding': '声明 consumes_data_path 的命令须消费 request.config.data_path 或 MARS_DATA_PATH；'
                            '该值由宿主解析并核验，不能忽略后沿用另一份数据。',
        },
        'result_schema': deepcopy(_RESULT_SCHEMA),
        'result_delivery': (
            'MARS_RESULT_PATH 是结果文件路径；成功结果严格符合上述 schema，三个标识原样复制请求。'
            'metrics 仅放真实有限数值，布尔门控转换为 0/1。evidence_paths 是相对 request.output_dir '
            '的实际证据路径，不得使用控制文件、绝对路径或路径逃逸。附加说明保存到独立证据 JSON，'
            '不能塞入受 schema 限定的结果顶层。失败应返回非零退出码，不补造 completed 结果。'
        ),
    }


def _file_hash(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return "sha256:" + checksum.hexdigest()


def _relative_capability(value: Any, name: str) -> str:
    if (not isinstance(value, str) or not value or Path(value).is_absolute()
            or PureWindowsPath(value).drive or '..' in Path(value.replace('\\', '/')).parts):
        raise ValueError(f'{name} must be a repository-relative path')
    return value.replace('\\', '/')


def _registered_command(job: LocalCommandJob) -> dict[str, Any]:
    cfg = load_execution_config()["execution"]
    rows = cfg.get("local_commands", [])
    declared = job.config.get('command_id')
    if job.command_id and declared and job.command_id != declared:
        raise ValueError('command_id disagrees with the approved job configuration')
    command_id = job.command_id or declared
    if not isinstance(command_id, str) or not command_id:
        raise ValueError('local_command requires an explicit registered command_id; no default command is selected')
    matching = [row for row in rows if isinstance(row, dict) and row.get('id') == command_id] if isinstance(rows, list) else []
    if len(matching) != 1:
        raise ValueError('registered command_id is unavailable or ambiguous: ' + command_id)
    selected: dict[str, Any] = matching[0]
    for field, value in (('project_ids', job.project), ('entrypoints', job.config.get('entrypoint')),
                         ('config_paths', job.config.get('config_path') or job.config.get('cfg'))):
        patterns = selected.get(field)
        if patterns is None:
            continue  # Existing explicit registrations have no new capability restriction.
        if not isinstance(patterns, list) or not patterns or any(not isinstance(p, str) or not p for p in patterns):
            raise ValueError('registered command ' + field + ' must be a nonempty string list')
        if field == 'project_ids':
            if value not in patterns:
                raise ValueError('registered command does not support this project')
        else:
            name = _relative_capability(value, field)
            if not any(fnmatchcase(name, _relative_capability(pattern, field)) for pattern in patterns):
                raise ValueError('registered command does not support this ' + field + ': ' + name)
    return selected


def _bound_workspace(job: LocalCommandJob) -> tuple[Path | None, Path | None]:
    from app.harness.tools.git_branch import current_git_branch, receipt_path, saved_branch
    from app.harness.runtime.project_scope import current_project_scope
    branch = current_git_branch(job.project, job.run_id)
    if branch is None and receipt_path(job.run_root).is_file():
        from app.harness.tools.project_repo import load_project_repo
        branch = saved_branch(load_project_repo(job.project).root, job.run_root, job.project, job.run_id)
    scope = current_project_scope(job.project, job.run_id)
    code_root = branch.root if branch is not None else scope.candidate_root if scope is not None else None
    from app.harness.project_workspace import folder_project, project_root
    folder = folder_project(job.project)
    project_path = folder.root if folder is not None else scope.run_root if scope is not None else project_root(job.project)
    return code_root, project_path.resolve() if project_path.is_dir() else None


def _command(job: LocalCommandJob, tool_name: str, *, validation: bool = False) -> tuple[tuple[str, ...], tuple[str, ...], float]:
    cfg = load_execution_config()['execution']
    selected = _registered_command(job)
    from app.harness.tools.execution.command_preflight import validation_configuration
    checker = validation_configuration(selected) if validation else None
    execution_argv = selected.get('argv')
    if not isinstance(execution_argv, list) or not execution_argv or any(not isinstance(part, str) or not part for part in execution_argv):
        raise ValueError('configured local_command argv must be a nonempty string list')
    raw = checker.get('argv', execution_argv) if checker is not None else execution_argv
    if not isinstance(raw, list) or not raw or any(not isinstance(part, str) or not part for part in raw):
        raise ValueError("configured local_command argv must be a nonempty string list")
    if checker is not None and raw[0] != execution_argv[0]:
        raise ValueError('command validation must use the registered execution interpreter')
    argv = tuple(raw)
    allowlist = tool_config(tool_name).command_allowlist
    if not any(len(argv) >= len(prefix) and argv[:len(prefix)] == prefix for prefix in allowlist if prefix):
        raise ValueError("configured local_command is not allowlisted for " + tool_name)
    from app.harness.tools.git_branch import current_git_branch, receipt_path, saved_branch
    from app.harness.runtime.project_scope import current_project_scope, safe_scope_path
    branch = current_git_branch(job.project, job.run_id)
    if branch is None and receipt_path(job.run_root).exists():
        from app.harness.tools.project_repo import load_project_repo
        branch = saved_branch(load_project_repo(job.project).root, job.run_root, job.project, job.run_id)
    scope = current_project_scope(job.project, job.run_id)
    code_root = branch.root if branch is not None else scope.candidate_root if scope is not None else None
    routed = []
    for index, argument in enumerate(argv):
        if "{code_root}" in argument:
            if code_root is None:
                raise ValueError("registered command requires a bound research checkout")
            argument = argument.replace("{code_root}", str(code_root))
        path = Path(argument)
        if index and branch is not None and branch.source_repo_path:
            if (path.is_absolute() and path.is_relative_to(branch.root)
                    and str(branch.source_root) in argument):
                # Isolated directories normally include the source name as a
                # prefix. Verify the actual path before scanning inline text
                # for shared-source references; a prefix is not ownership.
                path = safe_scope_path(branch.root, path.relative_to(branch.root).as_posix(), must_exist=True)
                argument = str(path)
            elif path.is_absolute() and path.is_relative_to(branch.source_root):
                path = safe_scope_path(branch.root, path.relative_to(branch.source_root).as_posix(), must_exist=True)
                argument = str(path)
            elif str(branch.source_root) in argument:
                raise ValueError("启动命令包含共享代码仓路径，请改用 MARS_CODE_ROOT 或 {code_root} 绑定本研究代码。")
        routed.append(argument)
    from app.harness.tools.check_interpreter import check_argv
    argv = check_argv(tuple(routed))
    required = selected.get("required_metrics", [])
    if not isinstance(required, list) or any(not isinstance(name, str) or not name for name in required):
        raise ValueError("required_metrics must be a list of metric names")
    timeout = float(checker['timeout_seconds'] if checker is not None else cfg.get("command_timeout_seconds", 60))
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("local_command timeout must be positive and finite")
    return argv, tuple(required), timeout


def command_request(job: LocalCommandJob, invocation_id: str, output_dir: Path) -> dict[str, Any]:
    """Bind the same approved job to preview validation and actual execution."""
    from app.harness.schema.experiment_contract import budget
    steps = budget(job.config)[1] if any(key in job.config for key in ('budget_steps', 'budget_unit', 'max_iters')) else job.steps
    code_root, project_path = _bound_workspace(job)
    config = deepcopy(job.config)
    selected = _registered_command(job)
    if selected.get('consumes_data_path') is True:
        path_text = config.get('data_path')
        chosen = job.run_root / 'input/selected_data_source.json'
        if not path_text and chosen.is_file():
            raw = json.loads(chosen.read_text(encoding='utf-8'))
            path_text = raw.get('stored_path') if isinstance(raw, dict) else None
        if path_text:
            if not isinstance(path_text, str):
                raise ValueError('job data_path must be a string')
            path = Path(path_text).expanduser()
            if not path.is_absolute():
                if code_root is None:
                    raise ValueError('relative job data requires a bound code workspace')
                path = code_root / path
            resolved = path.resolve(strict=True)
            roots = [root for root in (code_root, project_path) if root is not None]
            if roots and not any(resolved.is_relative_to(root.resolve()) for root in roots):
                raise ValueError('job data path is outside the host-bound project and code workspace')
            if not resolved.is_file():
                raise ValueError('job data path must identify a real file')
            config['data_path'] = str(resolved)
    request: dict[str, Any] = {
        'schema': 'local_command_request.v1', 'invocation_id': invocation_id,
        'run_id': job.run_id, 'experiment_id': job.experiment_id, 'project': job.project,
        'config': config, 'seed': job.seed, 'steps': steps, 'output_dir': str(output_dir),
    }
    if code_root is not None:
        request['code_workspace'] = str(code_root)
        if (code_root / '.git').exists():
            from app.harness.tools.git_branch import git
            request['code_commit'] = git(code_root, 'rev-parse', 'HEAD')
    if project_path is not None:
        request['project_workspace'] = str(project_path)
    if selected.get('consumes_data_path') is True and config.get('data_path'):
        path = Path(config['data_path'])
        stat = path.stat()
        binding = {'path': str(path), 'bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
        request['data_binding'] = {**binding, 'binding_sha256': 'sha256:' + hashlib.sha256(
            json.dumps(binding, sort_keys=True).encode()).hexdigest()}
    return request


def command_environment(job: LocalCommandJob, request_path: Path, result_path: Path, output_dir: Path) -> dict[str, str]:
    request = json.loads(request_path.read_text(encoding='utf-8'))
    env = {'MARS_RUN_ID': job.run_id, 'MARS_EXPERIMENT_ID': job.experiment_id,
           'MARS_PROJECT': job.project, 'MARS_RUN_ROOT': str(job.run_root.resolve()),
           'MARS_JOB_REQUEST': str(request_path), 'MARS_RESULT_PATH': str(result_path),
           'MARS_TENSORBOARD_ENABLED': '1', 'MARS_VISUALIZATIONS_DIR': str(output_dir / 'visualizations')}
    for field, name in (('code_workspace', 'MARS_CODE_ROOT'), ('project_workspace', 'MARS_PROJECT_ROOT')):
        if request.get(field):
            env[name] = str(request[field])
    if request.get('data_binding'):
        env['MARS_DATA_PATH'] = str(request['data_binding']['path'])
    return env


def _read_result(path: Path, request: dict[str, Any], required_metrics: tuple[str, ...]) -> tuple[dict[str, float], list[float], list[dict[str, Any]]]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("command did not create a bounded result.json in its attempt directory")
    raw = json.loads(path.read_text(encoding="utf-8"))
    validate(instance=raw, schema=_RESULT_SCHEMA)
    for key in ("invocation_id", "run_id", "experiment_id"):
        if raw[key] != request[key]:
            raise ValueError("local_command result belongs to a different " + key)
    metrics = {name: float(value) for name, value in raw["metrics"].items()}
    if not all(math.isfinite(value) for value in metrics.values()):
        raise ValueError("command measurements must all be finite")
    if not set(required_metrics).issubset(metrics):
        raise ValueError("command omitted required measurements: " + ", ".join(sorted(set(required_metrics) - metrics.keys())))
    if not set(metrics) - {"returncode", "dry_run", "max_iters"}:
        raise ValueError("process metadata cannot substitute for research measurements")
    curve = [float(value) for value in raw.get("loss_curve", [])]
    if not all(math.isfinite(value) for value in curve):
        raise ValueError("loss_curve must contain finite measurements")
    artifacts: list[dict[str, Any]] = []
    root = path.parent.resolve()
    for name in raw["evidence_paths"]:
        relative = Path(name)
        source = root / relative
        if (relative.is_absolute() or ".." in relative.parts or source.is_symlink()
                or not source.resolve().is_relative_to(root) or not source.is_file()
                or source.name in LOCAL_COMMAND_CONTROL_FILES):
            raise ValueError("evidence must identify an actual measurement file inside this attempt")
        artifacts.append({"kind": "measurement_evidence", "path": str(source), "sha256": _file_hash(source),
                          "bytes": source.stat().st_size})
    return metrics, curve, artifacts


def _process_output_limits() -> tuple[int, int]:
    config = load_execution_config()['execution']
    log_bytes = config.get('command_log_max_bytes', 1048576)
    tail_bytes = config.get('command_output_tail_bytes', 8192)
    if (type(log_bytes) is not int or not 1024 <= log_bytes <= 16777216
            or type(tail_bytes) is not int or not 0 < tail_bytes <= min(log_bytes, 65536)):
        raise ValueError('command_log_max_bytes must be 1024-16777216 and command_output_tail_bytes '
                         'must fit inside it (max 65536)')
    return int(log_bytes), int(tail_bytes)


def _safe_output_tail(stream: CapturedProcessStream) -> str:
    from app.harness.tools.execution.command_preflight import bounded_error
    tail = bytes(stream.tail)
    if stream.observed_bytes > len(tail):
        # A tail beginning inside a long credential has lost its key/header.
        # Publish only complete trailing lines, which can be redacted reliably.
        _, separator, tail = tail.partition(b'\n')
        if not separator:
            return '[output tail omitted: no complete line within configured limit]'
    return bounded_error(tail)


def _publish_process_log(stream: CapturedProcessStream, path: Path) -> None:
    from app.harness.tools.execution.command_preflight import bounded_error
    raw = b''
    if stream.path.is_file():
        with stream.path.open('rb') as source:
            raw = source.read(stream.max_log_bytes)
            if source.read(1):
                stream.truncated = True
    # The private spool is byte-capped. Redact the whole retained prefix before
    # publishing it, then cap UTF-8 bytes even if decoding/redaction expands it.
    safe = bounded_error(raw, max_chars=stream.max_log_bytes).encode('utf-8')
    if len(safe) > stream.max_log_bytes:
        stream.truncated = True
        marker = b'\n[MARS: log truncated at configured byte limit; see execution_receipt.json]\n'
        safe = safe[:stream.max_log_bytes - len(marker)].decode('utf-8', errors='ignore').encode('utf-8') + marker
    atomic_write_text(path, safe.decode('utf-8'))
    stream.written_bytes = len(safe)
    stream.path = path


async def run_local_command(job: LocalCommandJob, *, tool_name: str = "execution.simulation_runner") -> LocalCommandResult:
    from app.harness.tools.execution.command_preflight import run_command_preflight, bounded_error, preflight_state, validation_configuration
    approved: dict[str, Any] | None = None
    token = job.config.get('confirmation_token')
    if isinstance(token, str) and token and validation_configuration(_registered_command(job)) is not None:
        approved = preflight_state(job, token)
        if approved['status'] != 'passed':
            raise ValueError('confirmed command validation is missing or stale; training was not started')
    preflight = await asyncio.to_thread(run_command_preflight, job, 'execution-admission')
    if approved is not None and preflight['status'] == 'passed':
        expected = {row['path']: row['sha256'] for row in approved.get('input_files', [])}
        actual = {row['path']: row['sha256'] for row in preflight.get('input_files', [])}
        if expected != actual:
            raise ValueError('validated input hashes changed after confirmation; training was not started')
    if preflight['status'] == 'failed':
        return LocalCommandResult(status='failed', duration_seconds=0, metrics={}, loss_curve=[],
            fingerprint_hash=str(preflight['receipt_sha256']), error=str(preflight['error']),
            artifacts=[{'kind': 'local_command_validation_receipt', 'path': preflight['receipt_path']}])
    argv, required, timeout = _command(job, tool_name)
    log_bytes, tail_bytes = _process_output_limits()
    invocation_id = uuid.uuid4().hex
    safe_name = "".join(character if character.isalnum() or character in "-_" else "_" for character in job.experiment_id) or "experiment"
    output_dir = job.run_root.resolve() / "execution/local_commands" / safe_name / invocation_id
    output_dir.mkdir(parents=True, exist_ok=False)
    request_path = output_dir / "job.json"
    result_path = output_dir / "result.json"
    request = command_request(job, invocation_id, output_dir)
    atomic_write_json(request_path, request)
    request_sha256 = _file_hash(request_path)
    command_files = []
    for index, argument in enumerate(argv):
        resolved = shutil.which(argument) if index == 0 else argument
        if resolved is not None and Path(resolved).is_file():
            source = Path(resolved).resolve()
            command_files.append({"path": str(source), "sha256": _file_hash(source)})
    started = time.monotonic()
    code: int | None = None
    metrics: dict[str, float] = {}
    curve: list[float] = []
    evidence: list[dict[str, Any]] = []
    error = ""
    status = "failed"
    policy = tool_config(tool_name)
    interruption: BaseException | None = None
    process: asyncio.subprocess.Process | None = None
    with tempfile.TemporaryDirectory(prefix='mars-command-log-') as private_logs:
        capture = ProcessOutputCapture(
            CapturedProcessStream(Path(private_logs) / 'stdout.raw', log_bytes, tail_bytes),
            CapturedProcessStream(Path(private_logs) / 'stderr.raw', log_bytes, tail_bytes),
        )
        try:
            process = await start_process(argv, cwd=output_dir, backend=policy.process_backend,
                require_isolation=policy.require_isolation, env=command_environment(job, request_path, result_path, output_dir))
            await stream_process_output(process, capture=capture, timeout=timeout)
            code = process.returncode
            if code != 0:
                raise ValueError(f"local_command exited with status {code}: "
                                 + (_safe_output_tail(capture.stderr) or _safe_output_tail(capture.stdout)))
            if _file_hash(request_path) != request_sha256:
                raise ValueError("command modified its host-owned job request")
            metrics, curve, evidence = _read_result(result_path, request, required)
            status = "completed"
        except asyncio.TimeoutError:
            error = bounded_error(f'TimeoutError: local_command exceeded {timeout:g} seconds: '
                                  + (_safe_output_tail(capture.stderr) or _safe_output_tail(capture.stdout)))
        except Exception as exc:
            error = bounded_error(f"{type(exc).__name__}: {exc}")
        except BaseException as exc:
            status, error, interruption = "cancelled", type(exc).__name__, exc
        finally:
            _publish_process_log(capture.stdout, output_dir / 'stdout.log')
            _publish_process_log(capture.stderr, output_dir / 'stderr.log')
    duration = time.monotonic() - started
    if process is not None:
        code = process.returncode
    receipt: dict[str, Any] = {
        "schema": "local_command_receipt.v1", "invocation_id": invocation_id,
        "run_id": job.run_id, "experiment_id": job.experiment_id,
        "status": status, "returncode": code, "duration_seconds": duration,
        "pid": process.pid if process is not None else None,
        "argv": list(argv), "command_files": command_files, "execution_backend": "local_process", "os_isolated": False,
        "request_sha256": request_sha256,
        "result_sha256": _file_hash(result_path) if result_path.is_file() and not result_path.is_symlink() else None,
        "evidence": evidence, "error": error, "log_capture": capture.metadata(),
    }
    receipt_path = output_dir / "execution_receipt.json"
    atomic_write_json(receipt_path, receipt)
    if interruption is not None:
        raise interruption
    return LocalCommandResult(status=status, duration_seconds=duration, metrics=metrics, loss_curve=curve,
        fingerprint_hash=_file_hash(receipt_path), error=error,
        artifacts=[{"kind": "local_command_receipt", "path": str(receipt_path)},
                   {"kind": "local_command_request", "path": str(request_path)},
                   {"kind": "execution_log", "path": str(output_dir / 'stdout.log')},
                   {"kind": "execution_log", "path": str(output_dir / 'stderr.log')}, *evidence])
