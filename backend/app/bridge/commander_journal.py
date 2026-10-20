"""Durable dispatch receipts. Unknown writes are never automatically replayed."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.bridge.commander_session import ChatMessage, CommanderSession
from app.harness.persistence import atomic_write_json, path_lock


def action_key(tool: str, args: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps([tool, args], sort_keys=True, ensure_ascii=False,
        allow_nan=False).encode()).hexdigest()


def _load(root: Path) -> dict[str, Any]:
    path = root / 'action_journal.json'
    value = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {
        'schema': 'commander.action_journal.v1', 'entries': []}
    if value.get('schema') != 'commander.action_journal.v1' or not isinstance(value.get('entries'), list):
        raise ValueError('Commander action journal is invalid; retained for inspection')
    return dict(value)


def begin_action(root: Path, session: CommanderSession, tool: str, args: dict[str, Any],
                 *, mutation: bool) -> str:
    with path_lock(root / '.action_journal.lock'):
        journal = _load(root)
        identity = uuid4().hex
        user = next((message for message in session.messages if message.role == 'user'
                     and message.id == session.active_turn_id), None)
        journal['entries'].append({'id': identity, 'tool': tool, 'args': args,
            'turn_id': session.active_turn_id, 'mutation': mutation, 'state': 'running',
            'project': session.project, 'effective_run_id': args.get('run_id') or session.linked_run_id,
            'source_user_sha256': hashlib.sha256(user.content.encode()).hexdigest() if user else None,
            'key': action_key(tool, args), 'started_at': datetime.now(timezone.utc).isoformat()})
        atomic_write_json(root / 'action_journal.json', journal)
    return identity


def complete_action(root: Path, identity: str, result: dict[str, Any]) -> None:
    with path_lock(root / '.action_journal.lock'):
        journal = _load(root)
        row = next(entry for entry in journal['entries'] if entry['id'] == identity)
        row.update(state='completed', result=result, completed_at=datetime.now(timezone.utc).isoformat())
        atomic_write_json(root / 'action_journal.json', journal)


def restore_action_receipts(root: Path, session: CommanderSession) -> int:
    """Repair a crash between durable tool receipt and conversation persistence."""
    restored = 0
    for row in _load(root)['entries']:
        user = next((message for message in session.messages if message.role == 'user'
                     and message.id == row['turn_id']), None)
        if (user is None or row.get('source_user_sha256') != hashlib.sha256(user.content.encode()).hexdigest()
                or row.get('project') != session.project):
            continue
        if row['state'] != 'completed' or any(message.id == row['id'] for message in session.messages):
            continue
        # Old sessions may already have the same receipt under a legacy ID.
        if any(message.role == 'tool' and message.turn_id == row['turn_id']
               and message.tool_name == row['tool'] and message.tool_args == row['args']
               and message.tool_result == row['result'] for message in session.messages):
            continue
        session.add(ChatMessage(id=row['id'], role='tool', turn_id=row['turn_id'], tool_name=row['tool'],
            tool_args=row['args'], tool_result=row['result'], content=json.dumps(row['result'], ensure_ascii=False)))
        run_id = row['result'].get('run_id')
        if run_id and row['tool'] in {'run.create', 'run.existing_model', 'create_and_start_run', 'run.auto_research'}:
            session.linked_run_id = str(run_id)
        restored += 1
    return restored


def unresolved_write(root: Path, tool: str, args: dict[str, Any],
                     session: CommanderSession | None = None) -> dict[str, Any] | None:
    key = action_key(tool, args)
    return next((dict(entry) for entry in reversed(_load(root)['entries'])
        if entry['state'] == 'running' and entry['mutation'] and entry['key'] == key
        and (session is None or (entry.get('project') == session.project and
            entry.get('effective_run_id') == (args.get('run_id') or session.linked_run_id)))), None)


def repeated_failure(session: CommanderSession, tool: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """No identical retry after a permanent failure or without new evidence."""
    current = [message for message in session.context_messages() if message.turn_id == session.active_turn_id]
    for index in range(len(current) - 1, -1, -1):
        message = current[index]
        result = message.tool_result or {}
        if (message.role != 'tool' or message.tool_name != tool or message.tool_args != args
                or result.get('ok') is not False):
            continue
        new_evidence = any(item.role == 'tool' and (item.tool_result or {}).get('ok') is True
                           for item in current[index + 1:])
        if result.get('retryable') is False or not new_evidence:
            return dict(result)
        break
    return None
