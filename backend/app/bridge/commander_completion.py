"""Host completion obligations; model interpretation never certifies an effect."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from copy import deepcopy
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
import re
from typing import Any

from app.bridge.commander_session import ChatMessage, CommanderSession
from app.bridge.node_key import parse_node_key
from app.storage.run_store import RunStore

_EFFECTS = frozenset({'run_created', 'run_started', 'run_stopped', 'artifact_updated',
                      'exported', 'observed', 'tool_effect'})
_CREATORS = frozenset({'run.create', 'create_and_start_run', 'run.existing_model'})
_READERS = frozenset({'code.repo_list', 'code.repo_search', 'code.repo_read_lines',
    'routing.repository_check', 'context.read_material', 'run.status', 'get_run_status',
    'run.recovery_status', 'artifact.read', 'metrics.evaluate', 'diagnosis.failure_analysis',
    'get_diagnosis', 'query_kb', 'list_runs', 'discovery.status'})
_BLOCKED = frozenset({'blocked', 'waiting_approval', 'waiting_review'})
_CHOICES = frozenset({'needs_user', 'model_selection_required', 'entrypoint_selection_required',
                      'user_intent_confirmation_required'})


@dataclass(frozen=True)
class TurnGoal:
    intent: str
    required_effects: tuple[str, ...]
    run_target: str
    source_turn_id: str
    source_user_sha256: str
    expected_tools: tuple[str, ...] = ()
    project: str = ''
    target_run_id: str | None = None


@dataclass(frozen=True)
class CompletionVerdict:
    status: str
    reason: str
    evidence_ids: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    run_id: str | None = None


def _registered(name: str) -> bool:
    from app.bridge.commander_tools import TOOLS
    return name in TOOLS


def _source(session: CommanderSession, turn: str) -> ChatMessage | None:
    return next((m for m in session.context_messages() if m.role == 'user' and m.id == turn), None)


def _semantic_fields(raw: Any) -> tuple[str, tuple[str, ...], str, tuple[str, ...]] | None:
    if not isinstance(raw, dict):
        return None
    intent, target = raw.get('intent'), raw.get('run_target')
    effects, tools = raw.get('required_effects', []), raw.get('expected_tools', [])
    if (not isinstance(intent, str) or intent not in {'answer', 'observe', 'action', 'unknown'}
            or not isinstance(target, str) or target not in {'new', 'existing', 'none'}
            or not isinstance(effects, (list, tuple)) or not isinstance(tools, (list, tuple))):
        return None
    # For an explanatory answer, "observed" is optional semantic annotation,
    # not a request to run a reader. Actual observe/action obligations remain.
    if intent == 'answer' and all(effect == 'observed' for effect in effects):
        effects = ()
    if (any(not isinstance(x, str) or x not in _EFFECTS for x in effects)
            or any(not isinstance(x, str) or not _registered(x) for x in tools)
            or intent == 'answer' and bool(effects)
            or any(x.startswith('run_') for x in effects) and target == 'none'
            or intent == 'observe' and any(x != 'observed' for x in effects)):
        return None
    return intent, tuple(dict.fromkeys(effects)), target, tuple(dict.fromkeys(tools))


def parse_turn_goal(raw: Any, session: CommanderSession) -> TurnGoal | None:
    """Bind only semantic fields to the actual current user, never model IDs."""
    if not isinstance(raw, dict) or not session.active_turn_id:
        return None
    fields = _semantic_fields(raw)
    user = _source(session, session.active_turn_id)
    if fields is None or user is None:
        return None
    initial_run = session.completion_state.get('initial_linked_run_id', session.linked_run_id)
    if initial_run is not None and not isinstance(initial_run, str):
        return None
    intent, effects, target, tools = fields
    return TurnGoal(intent, effects, target, user.id, hashlib.sha256(user.content.encode()).hexdigest(),
                    tools, session.project, initial_run)


def admitted_existing_model_goal(session: CommanderSession, action: Any) -> TurnGoal | None:
    """Convert a trusted pending-admission action, without executing or resolving it.

    The caller must pass the result of the host pending-admission policy, never
    an unexecuted model action as an authorization. Scientific values remain
    solely in that original action and are revalidated by its real tool.
    """
    if not isinstance(action, dict) or action.get('tool') != 'run.existing_model':
        return None
    args = action.get('args')
    if not isinstance(args, dict) or type(args.get('start', True)) is not bool:
        return None
    return parse_turn_goal({'intent': 'action', 'run_target': 'new',
        'required_effects': ['run_started' if args.get('start', True) else 'run_created'],
        'expected_tools': ['run.existing_model']}, session)


def proposed_action_goal(session: CommanderSession, actions: Any) -> TurnGoal | None:
    """Describe known proposed operations before execution, never authorize them.

    Only the fixed registered tool semantics determine these obligations. The
    original user/source binding and admission checks remain unchanged; no
    scientific values or execution arguments are inferred or rewritten.
    """
    if not isinstance(actions, list) or not actions:
        return None
    names: list[str] = []
    for action in actions:
        if (not isinstance(action, dict) or not isinstance(action.get('tool'), str)
                or not _registered(action['tool']) or not isinstance(action.get('args', {}), dict)):
            return None
        names.append(action['tool'])
    if names == ['run.create'] or names == ['run.create', 'run.start']:
        effects = ['run_created', 'run_started'] if len(names) == 2 else ['run_created']
        target = 'new'
    elif names == ['create_and_start_run']:
        effects, target = ['run_created', 'run_started'], 'new'
    elif names == ['run.existing_model']:
        start = actions[0].get('args', {}).get('start', True)
        if type(start) is not bool:
            return None
        effects = ['run_created', 'run_started'] if start else ['run_created']
        target = 'new'
    elif names in (['run.start'], ['run.recover']):
        effects, target = ['run_started'], 'existing'
    else:
        return None
    return parse_turn_goal({'intent': 'action', 'required_effects': effects,
        'run_target': target, 'expected_tools': names}, session)


def goal_state(goal: TurnGoal) -> dict[str, Any]:
    return {'schema': 'commander.turn_goal.v1', **asdict(goal),
            'required_effects': list(goal.required_effects), 'expected_tools': list(goal.expected_tools)}


def restore_goal(raw: Any) -> TurnGoal | None:
    """Old sessions without a contract remain readable, without inventing one."""
    if isinstance(raw, dict) and isinstance(raw.get('goal'), dict):
        raw = raw['goal']
    if not isinstance(raw, dict) or raw.get('schema') != 'commander.turn_goal.v1':
        return None
    fields = _semantic_fields(raw)
    turn, checksum = raw.get('source_turn_id'), raw.get('source_user_sha256')
    project, run_id = raw.get('project', ''), raw.get('target_run_id')
    if (fields is None or not isinstance(turn, str) or not turn
            or not isinstance(checksum, str) or re.fullmatch(r'[0-9a-f]{64}', checksum) is None
            or not isinstance(project, str) or run_id is not None and not isinstance(run_id, str)):
        return None
    intent, effects, target, tools = fields
    return TurnGoal(intent, effects, target, turn, checksum, tools, project, run_id)


def _run_id(message: ChatMessage) -> str | None:
    value = (message.tool_result or {}).get('run_id')
    return value if isinstance(value, str) and value else None


def _run_valid(message: ChatMessage, session: CommanderSession, store: RunStore | None) -> bool:
    result, args = message.tool_result or {}, message.tool_args or {}
    run_id = _run_id(message)
    if (not run_id or run_id != session.linked_run_id
            or result.get('project', session.project) != session.project
            or args.get('project', session.project) != session.project
            or args.get('run_id') not in (None, '', run_id)):
        return False
    if store is not None:
        try:
            run = store.get(run_id)
            return run is not None and run.project == session.project
        except (OSError, ValueError):
            return False
    return True  # Same-turn, host-authored tool receipt bound to the session link.


def _created(message: ChatMessage) -> bool:
    result = message.tool_result or {}
    status = result.get('status') if isinstance(result.get('status'), str) else ''
    return (message.tool_name in _CREATORS and result.get('ok') is True
            and isinstance(result.get('entrypoint'), str) and isinstance(result.get('states'), dict)
            and (message.tool_name == 'run.create'
                 or status in {'started', 'preparation_started', 'prepared', 'created'}))


def current_turn_ledger(session: CommanderSession) -> list[dict[str, Any]]:
    """Expose actual current-turn observations; no inferred or replayed results."""
    return [{'id': m.id, 'tool': m.tool_name, 'args': deepcopy(m.tool_args or {}),
             'result': deepcopy(m.tool_result or {})} for m in session.context_messages()
            if session.active_turn_id is not None and m.turn_id == session.active_turn_id
            and m.role == 'tool' and isinstance(m.tool_result, dict)]


def receipt_intervention(result: dict[str, Any]) -> str | None:
    """Stop the authored action batch at an actual approval or choice boundary."""
    status = result.get('status') if isinstance(result.get('status'), str) else ''
    if result.get('requires_approval') is True or result.get('blocked_by_gate') or status in _BLOCKED:
        return 'blocked'
    if status in _CHOICES:
        return 'needs_user'
    return None


def _file_effect(result: dict[str, Any]) -> bool:
    """A path claim alone is not an exported or updated artifact."""
    path, checksum = result.get('path'), result.get('sha256')
    if not isinstance(path, str) or not isinstance(checksum, str):
        return False
    try:
        target = Path(path)
        return (target.is_absolute() and target.is_file() and not target.is_symlink()
                and target.stat().st_size <= 64 * 1024 * 1024
                and hashlib.sha256(target.read_bytes()).hexdigest() == checksum.removeprefix('sha256:'))
    except OSError:
        return False


def _metric_effect(message: ChatMessage, session: CommanderSession) -> bool:
    requested = (message.tool_args or {}).get('targets')
    if not isinstance(requested, dict) or not requested:
        return False
    try:
        targets = {str(key): float(value) for key, value in requested.items()}
    except (TypeError, ValueError, OverflowError):
        return False
    return ((message.tool_result or {}).get('metric_targets') == session.metric_targets
            and all(math.isfinite(value) and session.metric_targets.get(key) == value
                    for key, value in targets.items()))


def _review_bytes(path: Path, root: Path) -> bytes | None:
    """Inspect durable review evidence without ArtifactStore's recovery writes."""
    try:
        relative = path.relative_to(root)
        if (any((root / Path(*relative.parts[:i])).is_symlink()
                for i in range(1, len(relative.parts) + 1))
                or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024):
            return None
        return path.read_bytes()
    except (OSError, ValueError):
        return None


def _approved_review(root: Path, run_id: str, agent: str, version: Any) -> bool:
    from app.storage.artifact_store import SCHEMA_TO_AGENT
    if not isinstance(version, str) or re.fullmatch(r'v[1-9]\d*', version) is None:
        return False
    for directory, stem in set(SCHEMA_TO_AGENT.values()):
        if directory != agent:
            continue
        source = _review_bytes(root / agent / f'{stem}.{version}.md', root)
        approved = _review_bytes(root / agent / f'{stem}.approved.md', root)
        if source is None or source != approved:
            continue
        records = root / agent / '.approvals' / stem
        for path in records.glob('*.json'):
            raw = _review_bytes(path, root)
            if raw is None:
                continue
            try:
                record = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                continue
            if (isinstance(record, dict) and record.get('schema') == 'artifact_approval.v1'
                    and record.get('run_id') == run_id and record.get('agent_dir') == agent
                    and record.get('stem') == stem and record.get('source_version') == version
                    and record.get('source_sha256') == hashlib.sha256(source).hexdigest()):
                return True
    return False


def _review_effect(message: ChatMessage, session: CommanderSession, store: RunStore | None) -> bool:
    """A review receipt needs the matching real decision/audit, and approval bytes."""
    from app.hitl.review_session import get_registry
    if message.tool_name not in {'approve_node', 'reject_node', 'artifact.review', 'user.approval'}:
        return False
    args, result = message.tool_args or {}, message.tool_result or {}
    action = {'approve_node': 'approve', 'reject_node': 'reject'}.get(
        message.tool_name, str(args.get('action') or 'approve'))
    agent, run_id = result.get('agent'), _run_id(message)
    if (action not in {'approve', 'reject', 'comment'} or not isinstance(agent, str)
            or re.fullmatch(r'[a-z][a-z0-9_]*', agent) is None or args.get('agent') != agent
            or result.get('action') != {'approve': 'approved', 'reject': 'rejected', 'comment': 'commented'}[action]
            or not _run_valid(message, session, store) or run_id is None):
        return False
    review = get_registry().get(run_id, agent)
    run = store.get(run_id) if store is not None else review.run if review is not None else None
    user = _source(session, message.turn_id or '')
    if run is None or run.project != session.project or user is None:
        return False
    raw = _review_bytes(run.root / 'hitl' / 'review_log.jsonl', run.root)
    if raw is None:
        return False
    try:
        begin, end = datetime.fromisoformat(user.timestamp), datetime.fromisoformat(message.timestamp)
        entries = [json.loads(line) for line in raw.splitlines() if line.strip()]
        for entry in entries:
            if not isinstance(entry, dict) or (entry.get('run_id'), entry.get('agent'), entry.get('action')) != (
                    run_id, agent, action):
                continue
            timestamp, detail = entry.get('timestamp'), entry.get('detail')
            if (not isinstance(timestamp, str) or not isinstance(detail, dict)
                    or not begin <= datetime.fromisoformat(timestamp) <= end):
                continue
            if action == 'approve' and _approved_review(run.root, run_id, agent, detail.get('version')):
                return True
            if action == 'reject' and detail.get('reason') == str(args.get('reason', 'rejected via Commander')):
                if review is None or (review.decision == 'reject' and review.rejection_event.is_set()):
                    return True
            if action == 'comment' and detail.get('text') == str(args.get('text') or args.get('comment') or ''):
                return bool(detail.get('text'))
    except (OSError, ValueError, TypeError, UnicodeDecodeError):
        return False
    return False


def assess_completion(session: CommanderSession, goal: TurnGoal, reply: str = '',
                      semantic_status: str | None = None, evidence_ids: tuple[str, ...] = (),
                      run_store: RunStore | None = None) -> CompletionVerdict:
    user = _source(session, goal.source_turn_id)
    if (user is None or hashlib.sha256(user.content.encode()).hexdigest() != goal.source_user_sha256
            or goal.project and goal.project != session.project):
        return CompletionVerdict('unknown', 'goal_source_binding_changed', missing=goal.required_effects)
    if (session.active_turn_id and session.active_turn_id != goal.source_turn_id
            and restore_goal(session.completion_state) != goal):
        return CompletionVerdict('unknown', 'previous_goal_not_explicitly_restored', missing=goal.required_effects)
    turns = {goal.source_turn_id, session.active_turn_id}
    observations = [m for m in session.context_messages() if m.role == 'tool'
                    and m.turn_id in turns and isinstance(m.tool_result, dict)]
    valid_runs = [m for m in observations if _run_valid(m, session, run_store)]
    allocated = next((_run_id(m) for m in reversed(valid_runs) if m.tool_name in _CREATORS), None)
    # Even a failed start with an allocated run locks the task identity.
    bound = goal.target_run_id if goal.run_target == 'existing' else allocated
    blockers: list[CompletionVerdict] = []
    for m in observations:
        result = m.tool_result or {}
        intervention = receipt_intervention(result)
        if intervention == 'blocked':
            blockers.append(CompletionVerdict('blocked', 'real_tool_requires_intervention', (m.id,),
                                             goal.required_effects, bound))
        elif intervention == 'needs_user':
            blockers.append(CompletionVerdict('needs_user', 'real_tool_requires_choice', (m.id,),
                                             goal.required_effects, bound))
    facts: dict[str, list[str]] = {}
    # Semantic evidence IDs are annotations, never a filter or proof source.
    # All facts are independently reduced from the actual bound tool ledger.
    for m in observations:
        result, tool = m.tool_result or {}, m.tool_name or ''
        status = result.get('status') if isinstance(result.get('status'), str) else ''
        if result.get('ok') is not True or not _registered(tool):
            continue
        effects: list[str] = []
        if m in valid_runs and (goal.run_target != 'new' or _run_id(m) != goal.target_run_id):
            rid = _run_id(m)
            if _created(m) and (goal.run_target != 'existing' or rid == bound):
                effects.append('run_created')
            if (rid == bound and (tool in {'run.start', 'create_and_start_run', 'run.recover'}
                    and status in {'started', 'already_running', 'resumed', 'recovered'}
                    or tool == 'run.recover' and status in {'resuming', 'revision_started'}
                    or tool == 'run.existing_model' and status == 'preparation_started'
                    or goal.run_target == 'existing' and tool == 'run.recovery_status'
                    and status in {'running', 'already_running'})):
                effects.append('run_started')
            if (goal.run_target == 'existing' and rid == bound and status == 'stopped'
                    and isinstance(result.get('termination'), dict)
                    and result['termination'].get('cleanup_complete') is True):
                effects.append('run_stopped')
        if (tool in _READERS and len(set(result) - {'ok', 'error'}) > 0
                and (not _run_id(m) or m in valid_runs)):
            if (not result.get('project') or result['project'] == session.project) and (
                    not goal.expected_tools or tool in goal.expected_tools):
                effects.append('observed')
        if tool in goal.expected_tools:
            if tool == 'set_metric_targets' and _metric_effect(m, session):
                effects.append('tool_effect')
            if _review_effect(m, session, run_store):
                effects.append('tool_effect')
            if _file_effect(result):
                if status in {'updated', 'written'}:
                    effects.extend(('artifact_updated', 'tool_effect'))
                if status == 'exported':
                    effects.extend(('exported', 'tool_effect'))
        for effect in effects:
            facts.setdefault(effect, []).append(m.id)
    missing = tuple(effect for effect in goal.required_effects if effect not in facts)
    evidence = tuple(dict.fromkeys(mid for effect in goal.required_effects for mid in facts.get(effect, [])))
    if goal.intent == 'unknown':
        return CompletionVerdict('unknown', 'user_intent_unresolved', evidence, missing, bound)
    if goal.required_effects and not missing:
        if goal.intent == 'observe' and (semantic_status != 'answer_sufficient' or not reply.strip()):
            return CompletionVerdict('pending', 'observation_answer_not_established', evidence,
                                     ('semantic_answer',), bound)
        return CompletionVerdict('fulfilled', 'required_host_effects_observed', evidence, (), bound)
    if blockers:
        return blockers[-1]
    if (goal.intent == 'action' and goal.run_target == 'new' and run_store is not None
            and {'run_created', 'run_started'}.issubset(goal.required_effects)
            and missing == ('artifact_updated',)
            and facts.get('run_created') and facts.get('run_started')):
        coding_started = any(m.id in facts['run_started']
            and isinstance((m.tool_result or {}).get('states'), dict)
            and any(isinstance(key, str) and parse_node_key(key).stage == 'coding'
                    and isinstance(value, str) and value in {'pending', 'running', 'waiting_review', 'approved', 'done'}
                    for key, value in (m.tool_result or {})['states'].items()) for m in valid_runs)
        if coding_started:
            # This completes only the controller handoff. The original goal
            # and missing child outcome remain durable and are never fulfilled
            # merely because Coding has accepted the work.
            return CompletionVerdict('delegated', 'workflow_handoff_accepted_child_outcomes_pending',
                                     evidence, missing, bound)
    if goal.intent == 'answer' and semantic_status == 'answer_sufficient' and reply.strip():
        return CompletionVerdict('fulfilled', 'semantic_answer_sufficient', evidence, (), bound)
    if semantic_status == 'needs_user' and reply.strip():
        return CompletionVerdict('needs_user', 'semantic_question_requires_user', evidence, missing, bound)
    return CompletionVerdict('pending', 'required_effects_not_observed', evidence,
                             missing or ('semantic_answer' if goal.intent == 'answer' else 'declared_effect',), bound)


def pending_continuation_prompt(verdict: CompletionVerdict) -> str:
    missing = ', '.join(verdict.missing) or verdict.reason
    retained = f' 已分配任务 {verdict.run_id}，必须保留，不得重复创建。' if verdict.run_id else ''
    return ('MARS 尚未取得本轮目标的完成证据。缺少：' + missing + '。'
            '依据原始用户请求和已有真实回执继续未完成部分；不能声称未发生的操作已完成，'
            '不能补造参数、绕过审批或重放已经成功的副作用。' + retained)
