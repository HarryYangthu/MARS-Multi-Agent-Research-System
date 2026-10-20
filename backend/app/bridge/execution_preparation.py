"""Owned, restart-visible no-training preparation, separate from confirmation."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
from typing import Any
import uuid

from filelock import FileLock, Timeout

from app.bridge.execution_batch_plan import prepare_execution
from app.bridge.execution_confirmation import execution_preview
from app.harness.persistence import atomic_write_json
from app.harness.tools.execution.command_preflight import bounded_error, run_command_preflight
from app.harness.tools.execution.local_command import LocalCommandJob
from app.settings import get_settings, repo_root
from app.storage.run_store import RunHandle


def _signature(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        target = path.resolve(strict=True)
        stat = path.lstat()
        return {'path': str(path), 'link': os.readlink(path), 'link_inode': stat.st_ino,
                'link_mtime_ns': stat.st_mtime_ns, 'link_ctime_ns': stat.st_ctime_ns,
                'target': _signature(target)}
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {'path': str(path), 'missing': True}
    return {'path': str(path), 'bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
            'ctime_ns': stat.st_ctime_ns, 'inode': stat.st_ino, 'device': stat.st_dev}


def _environment() -> str:
    # Credentials remain host-side; their digest, never their text, binds readiness.
    return hashlib.sha256(json.dumps(dict(os.environ), sort_keys=True).encode()).hexdigest()


def _guards(run: RunHandle, view: dict[str, Any]) -> list[dict[str, Any]]:
    paths = {run.root / name for name in ('experiment/experiment_plan.approved.md',
        'coding/code_spec.approved.md', 'execution/run_log.approved.md',
        'input/selected_data_source.json', 'input/run_request_options.v1.json')}
    paths.add(run.root / 'execution/confirmation_revisions' / (view['node'] + '.json'))
    for variable, name in (('MARS_EXECUTION_CONFIG_PATH', 'execution.yaml'), ('MARS_TOOLS_CONFIG_PATH', 'tools.yaml')):
        paths.add(Path(os.environ.get(variable) or repo_root() / 'configs' / name).absolute())
    paths.add(Path(os.environ.get('MARS_EXECUTION_REVIEW_CONFIG_PATH')
                   or repo_root() / 'configs/execution_review.yaml').absolute())
    repository = view.get('defaults', {}).get('repository')
    if repository:
        root = Path(repository)
        paths.update(root / row['path'] for row in view.get('source_configs', []))
        # Git metadata catches branch/index changes cheaply; final admission
        # still performs the actual Git/current-input authority check.
        git = root / '.git'
        if git.is_dir():
            paths.update((git / 'HEAD', git / 'index', git / 'packed-refs'))
            head = git / 'HEAD'
            if head.is_file():
                text = head.read_text(encoding='utf-8').strip()
                if text.startswith('ref: ') and '..' not in text[5:].split('/'):
                    paths.add(git / text[5:])
    for row in view.get('validation', []):
        paths.update(Path(item['path']) for item in row.get('input_files', []))
        for key in ('receipt_path', 'derivation_path'):
            if row.get(key):
                paths.add(Path(row[key]))
    for row in view.get('experiments', []):
        data = row.get('effective', {}).get('data_path') or row.get('config', {}).get('data_path')
        if data and Path(data).is_absolute():
            paths.add(Path(data))
    return [_signature(path.absolute()) for path in sorted(paths)]


def _unchanged(record: dict[str, Any]) -> bool:
    try:
        return (record.get('environment_sha256') == _environment()
                and all(_signature(Path(row['path'])) == row for row in record.get('guards', [])))
    except (OSError, ValueError, KeyError, TypeError):
        return False


class ExecutionPreparations:
    """One no-training owner per run, independent of a review coordinator."""

    def __init__(self) -> None:
        self.generation = uuid.uuid4().hex
        self.closing = False
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._confirming: dict[str, asyncio.Task[None]] = {}

    @staticmethod
    def _path(run: RunHandle) -> Path:
        return run.root / 'execution/preparations/current.json'

    def _save(self, run: RunHandle, record: dict[str, Any]) -> None:
        atomic_write_json(run.root / 'execution/preparations' / (record['operation_id'] + '.json'), record)
        current = self._load(run)
        if current is not None and current.get('operation_id') == record['operation_id']:
            atomic_write_json(self._path(run), record)

    def _load(self, run: RunHandle) -> dict[str, Any] | None:
        path = self._path(run)
        if not path.exists():
            return None
        if path.is_symlink() or path.stat().st_size > 16777216:
            raise ValueError('运行准备记录无效')
        raw = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(raw, dict) or raw.get('schema') != 'execution.preparation.v1'
                or raw.get('run_id') != run.run_id or raw.get('project') != run.project):
            raise ValueError('运行准备记录不属于当前任务')
        return raw

    def status(self, run: RunHandle, node: str, *, operation_id: str | None = None) -> dict[str, Any]:
        raw = self._load(run)
        if raw is None or raw.get('node') != node:
            if operation_id:
                raise ValueError('运行准备操作不属于当前阶段')
            return {'run_id': run.run_id, 'project': run.project, 'node': node, 'operation_id': '',
                    'status': 'unprepared', 'token': '', 'completed': 0, 'total': 0,
                    'validation': [], 'confirmed': False, 'confirmation_status': 'unconfirmed'}
        if operation_id is not None and operation_id != raw['operation_id']:
            raise ValueError('运行准备操作已更新，请读取当前状态')
        status = raw['status']
        if raw.get('host_generation') != self.generation and status in {'preparing', 'ready'}:
            status = 'interrupted'
        elif status == 'ready' and not _unchanged(raw):
            status = 'stale'
        public = {key: raw[key] for key in ('run_id', 'project', 'node', 'operation_id', 'token',
            'completed', 'total', 'validation')}
        confirmation_status = raw.get('confirmation_status', 'unconfirmed')
        if raw.get('host_generation') != self.generation and confirmation_status == 'confirming':
            confirmation_status = 'failed'
        public.update(status=status, confirmed=False, confirmation_status=confirmation_status)
        if raw.get('confirmation_error'):
            public['confirmation_error'] = raw['confirmation_error']
        if raw.get('error'):
            public['error'] = raw['error']
        if isinstance(raw.get('configuration'), dict):
            view = dict(raw['configuration'])
            view['can_confirm'] = status == 'ready' and not view.get('blockers')
            receipt = run.root / 'execution/confirmations' / (str(raw['token']) + '.json')
            confirmed = False
            if receipt.is_file() and not receipt.is_symlink():
                saved = json.loads(receipt.read_text(encoding='utf-8'))
                confirmed = (saved.get('schema') == 'execution.confirmation' and saved.get('token') == raw['token']
                    and saved.get('run_id') == run.run_id and saved.get('project') == run.project
                    and saved.get('node') == node and saved.get('actor') in {'user', 'commander', 'auto_approve'})
                if confirmed:
                    expected = {row['name']: row.get('receipt_sha256') for row in view.get('validation', [])
                                if row.get('configured')}
                    actual = {row.get('name'): row.get('receipt_sha256') for row in saved.get('validation', [])
                              if isinstance(row, dict)}
                    confirmed = not expected or expected == actual
            view['confirmed'] = confirmed
            public.update(configuration=view, confirmed=confirmed)
            if confirmed:
                public['confirmation_status'] = 'confirmed'
        return public

    async def start(self, run: RunHandle, node: str, *, token: str | None = None) -> dict[str, Any]:
        if self.closing:
            raise ValueError('服务正在关闭，尚未开始运行前检查')
        active = self._tasks.get(run.run_id)
        if active is not None and not active.done():
            owned_record = self._load(run)
            if owned_record is None or owned_record.get('node') != node:
                raise ValueError('当前任务的另一个执行阶段正在检查，请等待检查结束')
            current = self.status(run, node)
            if current['status'] == 'preparing':
                if token and current['token'] and token != current['token']:
                    raise ValueError('另一个配置版本正在检查，请等待当前检查结束')
                return current
            # The durable terminal write can beat the event-loop done callback.
            # Drain only that completed worker, never a preparing batch.
            await asyncio.shield(active)
        current = self.status(run, node)
        confirmation = self._confirming.get(run.run_id)
        if confirmation is not None and not confirmation.done():
            if token and current['token'] != token:
                raise ValueError('当前配置正在确认，请等待确认状态')
            return current
        if current['status'] == 'ready' and (not token or current['token'] == token):
            return current
        lock = FileLock(str(run.root / 'execution/preparations/.owner.lock'), preserve_lock_file=True)
        Path(lock.lock_file).parent.mkdir(parents=True, exist_ok=True)
        try:
            lock.acquire(timeout=0)
        except Timeout as exc:
            raise ValueError('当前任务已有运行前检查进程，请稍后读取状态') from exc
        operation = uuid.uuid4().hex
        record: dict[str, Any] = {'schema': 'execution.preparation.v1', 'run_id': run.run_id,
            'project': run.project, 'node': node, 'operation_id': operation, 'host_generation': self.generation,
            'host_pid': os.getpid(), 'status': 'preparing', 'token': token or '', 'completed': 0,
            'total': 0, 'validation': [], 'started_at': datetime.now(timezone.utc).isoformat()}
        atomic_write_json(self._path(run), record)
        cancel = threading.Event()
        self._cancel[run.run_id] = cancel

        async def owned() -> None:
            worker = asyncio.create_task(asyncio.to_thread(self._prepare, run, node, token, record, cancel))
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                cancel.set()
                # Cancellation of an HTTP or owner waiter never abandons a child.
                await asyncio.shield(worker)
                raise
            finally:
                lock.release()
                if self._cancel.get(run.run_id) is cancel:
                    self._cancel.pop(run.run_id, None)

        task = asyncio.create_task(owned(), name=f'execution_preparation:{run.run_id}:{operation}')
        self._tasks[run.run_id] = task
        return self.status(run, node)

    def _prepare(self, run: RunHandle, node: str, token: str | None, record: dict[str, Any],
                 cancel: threading.Event) -> None:
        def save() -> None:
            self._save(run, record)
        try:
            view = execution_preview(run, node)
            record.update(token=view['token'], configuration=view, total=len(view['experiments']),
                          validation=[{'name': row['name'], 'status': 'required'} for row in view['experiments']])
            save()
            if token is not None and token != view['token']:
                record.update(status='stale', error='配置或代码已变化，请核对新的配置后重新检查。')
                return
            if view['blockers']:
                raise ValueError('；'.join(view['blockers']))
            if get_settings().mars_execution_backend == 'local_command':
                prepared = prepare_execution(run, node)
                verified_inputs: dict[str, dict[str, Any]] = {}
                for index, spec in enumerate(prepared.specs):
                    if cancel.is_set():
                        raise ValueError('运行前检查已中止；尚未开始训练。')
                    row = record['validation'][index]
                    row['status'] = 'running'
                    save()
                    job = LocalCommandJob(run_id=run.run_id, experiment_id=spec.experiment_id,
                        project=run.project, run_root=run.root, config=spec.config, seed=spec.seed,
                        steps=prepared.batch_steps, command_id=str(spec.config.get('command_id') or ''))
                    state = run_command_preflight(job, view['token'], source_token=view['base_inputs_token'],
                                                  cancel_event=cancel, verified_inputs=verified_inputs)
                    row.update(state)
                    record['completed'] = index + 1
                    save()
                    if state['status'] not in {'passed', 'unverified'}:
                        raise ValueError(f"{spec.experiment_id}：{state.get('error', '运行前检查未通过')}")
            if cancel.is_set():
                raise ValueError('运行前检查已中止；尚未开始训练。')
            refreshed = execution_preview(run, node)
            if refreshed['token'] != view['token'] or refreshed['blockers']:
                record.update(status='stale', configuration=refreshed, token=refreshed['token'],
                              error='检查期间输入已变化，尚未确认或启动训练。')
                return
            if any(row.get('configured') and row['status'] != 'passed' for row in refreshed['validation']):
                record.update(status='stale', configuration=refreshed,
                              error='运行前检查回执已失效，请重新检查；尚未启动训练。')
                return
            record.update(status='ready', configuration=refreshed, validation=refreshed['validation'],
                          completed=record['total'], guards=_guards(run, refreshed), environment_sha256=_environment())
        except Exception as exc:
            record.update(status='interrupted' if cancel.is_set() else 'failed', error=bounded_error(str(exc)))
        finally:
            record['finished_at'] = datetime.now(timezone.utc).isoformat()
            save()

    def ready_view(self, run: RunHandle, node: str, token: str) -> dict[str, Any]:
        state = self.status(run, node)
        if state['status'] != 'ready' or state['token'] != token:
            raise ValueError('运行前检查尚未完成或输入已变化，请重新检查；尚未启动训练。')
        return dict(state['configuration'])

    async def wait(self, run: RunHandle, node: str, *, token: str | None = None) -> dict[str, Any]:
        await self.start(run, node, token=token)
        task = self._tasks.get(run.run_id)
        if task is not None:
            await asyncio.shield(task)
        return self.status(run, node)

    async def cancel(self, run_id: str) -> None:
        self.signal_cancel(run_id)
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            await asyncio.shield(task)
        confirmation = self._confirming.get(run_id)
        if confirmation is not None and not confirmation.done():
            await asyncio.shield(confirmation)

    def signal_cancel(self, run_id: str) -> None:
        event = self._cancel.get(run_id)
        if event is not None:
            event.set()

    async def confirm(self, run: RunHandle, node: str, token: str, *,
                      resume: Callable[[], Awaitable[dict[str, Any]]] | None = None,
                      actor: str = 'user') -> dict[str, Any]:
        if self.closing:
            raise ValueError('服务正在关闭，尚未启动新的训练')
        prepared = self.ready_view(run, node, token)
        preparing = self._tasks.get(run.run_id)
        if preparing is not None and not preparing.done():
            await asyncio.shield(preparing)  # ready_view already proved terminal, not preparing.
        existing = self._confirming.get(run.run_id)
        if existing is not None and not existing.done():
            return {'ok': True, 'confirmed': False, 'status': 'confirming', 'run_id': run.run_id,
                    'project': run.project, 'token': token}
        record = self._load(run)
        assert record is not None
        lock = FileLock(str(run.root / 'execution/preparations/.owner.lock'), preserve_lock_file=True)
        try:
            lock.acquire(timeout=0)
        except Timeout as exc:
            raise ValueError('当前任务已有运行前检查或确认进程，请读取现有状态') from exc
        record.update(confirmation_status='confirming')
        record.pop('confirmation_error', None)
        atomic_write_json(self._path(run), record)
        cancel = threading.Event()
        self._cancel[run.run_id] = cancel

        async def owned() -> None:
            from app.bridge.execution_confirmation import save_prepared_confirmation
            try:
                receipt = await asyncio.to_thread(save_prepared_confirmation, run, node, token, prepared,
                                                  cancel_event=cancel, actor=actor)
                record['confirmation_status'] = 'confirmed'
                record['configuration'] = receipt
                record['guards'] = _guards(run, receipt)
                record['environment_sha256'] = _environment()
                if resume is not None and not self.closing and not cancel.is_set():
                    result = await resume()
                    if not result.get('ok'):
                        record['confirmation_error'] = bounded_error(str(result.get('status', '当前任务尚未推进')))
            except Exception as exc:
                record.update(confirmation_status='failed', confirmation_error=bounded_error(str(exc)))
            finally:
                try:
                    self._save(run, record)
                finally:
                    lock.release()
                    if self._cancel.get(run.run_id) is cancel:
                        self._cancel.pop(run.run_id, None)

        self._confirming[run.run_id] = asyncio.create_task(owned(), name=f'execution_confirmation:{run.run_id}')
        return {'ok': True, 'confirmed': False, 'status': 'confirming', 'run_id': run.run_id,
                'project': run.project, 'token': token}

    async def wait_confirmation(self, run: RunHandle, node: str) -> dict[str, Any]:
        task = self._confirming.get(run.run_id)
        if task is not None:
            await asyncio.shield(task)
        return self.status(run, node)

    async def shutdown(self) -> None:
        self.closing = True
        for event in self._cancel.values():
            event.set()
        await asyncio.gather(*(asyncio.shield(task) for task in (*self._tasks.values(), *self._confirming.values())),
                             return_exceptions=True)
