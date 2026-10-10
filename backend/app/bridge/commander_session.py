"""Commander conversation session storage.

Holds the multi-turn dialogue, the conversation FSM state, the linked run (if
any), the auto/semi-auto intervention flag, and any user-set metric targets
(the "expectation" that the self-healing loop drives toward).

Persisted under ``conversations/<conv_id>/`` (sibling of ``runs/``):
    session.json    — metadata + current state + linked run + auto_mode
    messages.jsonl  — current dialogue + tool-call trace (edited turns replaced)
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.harness.runtime.conversation_state import ConversationState
from app.settings import repo_root

Role = Literal["user", "assistant", "system", "tool"]

_SLUG_RE = re.compile(r"[^a-z0-9_]+")
SUMMARY_TRIGGER_MESSAGES = 20
SUMMARY_RETAIN_MESSAGES = 12
SUMMARY_TRIGGER_TOKENS = 5600


def research_summary(content: str) -> str:
    """Use an explicit research question when present, without rewriting history."""
    goal = re.search(r"(?:研究目标|研究问题|研究内容|任务目标|目标)\s*[:：]\s*([^\n]+)", content)
    text = goal.group(1) if goal else content
    return " ".join(text.split())[:80]


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass
class ChatMessage:
    role: Role
    content: str
    timestamp: str = field(default_factory=_now)
    # Tool-call trace (assistant decided to call a tool / tool returned):
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_result: dict[str, Any] | None = None
    # Conversation FSM state at the moment this message was emitted:
    state: str | None = None
    id: str = field(default_factory=lambda: uuid4().hex)
    turn_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConversationActivity:
    """Public execution milestones, never model reasoning or raw tool arguments."""

    id: str
    kind: str
    title: str
    timestamp: str = field(default_factory=_now)
    status: str = "running"
    ended_at: str | None = None
    turn_id: str | None = None


@dataclass
class CommanderSession:
    conv_id: str
    project: str
    state: ConversationState = ConversationState.IDLE
    linked_run_id: str | None = None
    experiment_id: str = ""           # Project experiment this conversation belongs to
    auto_mode: bool = False           # Compatibility wire name: True = Commander reviews, False = human reviews.
    metric_targets: dict[str, float] = field(default_factory=dict)
    rolling_summary: str = ""
    summary_updated_at: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    messages: list[ChatMessage] = field(default_factory=list)
    context_version: int = 3
    context_compaction: dict[str, Any] = field(default_factory=dict)
    activities: list[ConversationActivity] = field(default_factory=list)
    processing: bool = False
    active_turn_id: str | None = None
    edit_cursor: int | None = field(default=None, repr=False)

    def begin_activity(self, kind: str, title: str) -> ConversationActivity:
        activity = ConversationActivity(id=uuid4().hex, kind=kind, title=title, turn_id=self.active_turn_id)
        self.activities.append(activity)
        return activity

    def finish_activity(self, activity: ConversationActivity, status: str = "completed") -> None:
        activity.status = status
        activity.ended_at = _now()

    def interrupt_activities(self) -> None:
        for activity in self.activities:
            if activity.status == "running":
                self.finish_activity(activity, "interrupted")
        self.processing = False

    def add(self, msg: ChatMessage) -> ChatMessage:
        msg.state = self.state.value
        msg.turn_id = msg.turn_id or self.active_turn_id
        if self.edit_cursor is None:
            self.messages.append(msg)
            self._maybe_rollup()
        else:
            self.messages.insert(self.edit_cursor, msg)
            self.edit_cursor += 1
        self.updated_at = _now()
        return msg

    def context_messages(self) -> list[ChatMessage]:
        return list(self.messages if self.edit_cursor is None else self.messages[:self.edit_cursor])

    def to_meta(self) -> dict[str, Any]:
        return {
            "context_version": self.context_version,
            "context_compaction": self.context_compaction,
            "conv_id": self.conv_id,
            "project": self.project,
            "state": self.state.value,
            "linked_run_id": self.linked_run_id,
            "experiment_id": self.experiment_id,
            "auto_mode": self.auto_mode,
            "metric_targets": dict(self.metric_targets),
            "rolling_summary": self.rolling_summary,
            "summary_updated_at": self.summary_updated_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": len(self.messages),
            "summary": next((research_summary(m.content) for m in self.messages if m.role == "user"), ""),
            "activities": [asdict(item) for item in self.activities],
            "processing": self.processing,
            "active_turn_id": self.active_turn_id,
        }

    def _maybe_rollup(self) -> None:
        if self.context_version >= 3:
            return  # v3 retains originals; pre-call packer owns reversible compaction.
        token_estimate = sum(max(1, len(message.content) // 4) for message in self.messages)
        if len(self.messages) <= SUMMARY_TRIGGER_MESSAGES and token_estimate <= SUMMARY_TRIGGER_TOKENS:
            return
        if len(self.messages) <= SUMMARY_RETAIN_MESSAGES:
            return
        older = self.messages[:-SUMMARY_RETAIN_MESSAGES]
        self.messages = self.messages[-SUMMARY_RETAIN_MESSAGES:]
        self.rolling_summary = _merge_summary(self.rolling_summary, older)
        self.summary_updated_at = _now()


def _conversations_root() -> Path:
    root = repo_root() / "conversations"
    root.mkdir(parents=True, exist_ok=True)
    return root


class CommanderSessionStore:
    """In-memory registry with best-effort disk persistence."""

    def __init__(self, root: Path | None = None) -> None:
        self._sessions: dict[str, CommanderSession] = {}
        self._root = root

    def _directory(self) -> Path:
        root = self._root if self._root is not None else _conversations_root()
        root.mkdir(parents=True, exist_ok=True)
        return root

    # ------------------------------------------------------------- create

    def create(self, *, project: str, experiment_id: str = "", now: datetime | None = None) -> CommanderSession:
        ts = (now or datetime.now(tz=timezone.utc)).strftime("%Y-%m-%dT%H%M%S")
        conv_id = f"conv_{ts}"
        # collision guard
        if conv_id in self._sessions or (self._directory() / conv_id).exists():
            conv_id = f"conv_{ts}_{len(self._sessions)}"
        session = CommanderSession(conv_id=conv_id, project=project, experiment_id=experiment_id)
        self._sessions[conv_id] = session
        self._persist(session)
        return session

    # --------------------------------------------------------------- get

    def get(self, conv_id: str) -> CommanderSession | None:
        if conv_id in self._sessions:
            return self._sessions[conv_id]
        recovered = self._load(conv_id)
        if recovered is not None:
            self._sessions[conv_id] = recovered
        return recovered

    def list(self) -> list[CommanderSession]:
        # Merge in-memory + on-disk (in-memory wins).
        out: dict[str, CommanderSession] = {}
        root = self._directory()
        if root.exists():
            for entry in sorted(root.iterdir()):
                if entry.is_dir() and (entry / "session.json").exists():
                    loaded = self._load(entry.name)
                    if loaded is not None:
                        out[entry.name] = loaded
        out.update(self._sessions)
        return sorted(out.values(), key=lambda s: s.created_at, reverse=True)

    # ------------------------------------------------------------- persist

    def persist(self, session: CommanderSession) -> None:
        self._persist(session)

    def _persist(self, session: CommanderSession) -> None:
        from app.harness.persistence import atomic_write_text
        d = self._directory() / session.conv_id
        d.mkdir(parents=True, exist_ok=True)
        atomic_write_text(d / "session.json",
            json.dumps(session.to_meta(), ensure_ascii=False, indent=2),
        )
        atomic_write_text(d / "messages.jsonl", "".join(json.dumps(m.to_dict(), ensure_ascii=False) + "\n" for m in session.messages))

    def _load(self, conv_id: str) -> CommanderSession | None:
        d = self._directory() / conv_id
        meta_path = d / "session.json"
        if not meta_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        messages: list[ChatMessage] = []
        msg_path = d / "messages.jsonl"
        if msg_path.exists():
            for index, line in enumerate(msg_path.read_text(encoding="utf-8").splitlines()):
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                messages.append(
                    ChatMessage(
                        role=raw.get("role", "assistant"),
                        content=raw.get("content", ""),
                        timestamp=raw.get("timestamp", _now()),
                        tool_name=raw.get("tool_name"),
                        tool_args=raw.get("tool_args"),
                        tool_result=raw.get("tool_result"),
                        state=raw.get("state"),
                        id=str(raw.get("id") or uuid5(NAMESPACE_URL, f"mars:{conv_id}:message:{index}:{raw.get('timestamp', '')}").hex),
                        turn_id=raw.get("turn_id"),
                    )
                )
        try:
            state = ConversationState(meta.get("state", "idle"))
        except ValueError:
            state = ConversationState.IDLE
        session = CommanderSession(
            conv_id=conv_id,
            project=str(meta.get("project", "pimc")),
            state=state,
            linked_run_id=meta.get("linked_run_id"),
            experiment_id=str(meta.get("experiment_id", "") or ""),
            auto_mode=bool(meta.get("auto_mode", False)),
            metric_targets={
                str(k): float(v) for k, v in (meta.get("metric_targets") or {}).items()
            },
            rolling_summary=str(meta.get("rolling_summary", "") or ""),
            summary_updated_at=(
                str(meta.get("summary_updated_at"))
                if meta.get("summary_updated_at") is not None
                else None
            ),
            created_at=str(meta.get("created_at", _now())),
            updated_at=str(meta.get("updated_at", _now())),
            messages=messages,
            context_version=int(meta.get("context_version", 2)),
            context_compaction=dict(meta.get("context_compaction", {})),
            activities=[ConversationActivity(**item) for item in meta.get("activities", [])],
        )
        # A process restart cannot imply that an old provider/tool call is still live.
        session.interrupt_activities()
        # Legacy records gain stable turn identities without changing their text.
        turn_id: str | None = None
        for message in session.messages:
            if message.role == "user":
                turn_id = message.id
            message.turn_id = message.turn_id or turn_id
        users = [message for message in session.messages if message.role == "user"]
        for activity in session.activities:
            if activity.turn_id is None:
                owner = next((message for message in reversed(users) if message.timestamp <= activity.timestamp), None)
                activity.turn_id = owner.id if owner else None
        return session


_store: CommanderSessionStore | None = None


def get_session_store() -> CommanderSessionStore:
    global _store
    if _store is None:
        _store = CommanderSessionStore()
    return _store


def reset_session_store_for_tests() -> None:
    global _store
    _store = None


def _merge_summary(previous: str, messages: list[ChatMessage]) -> str:
    lines: list[str] = []
    if previous:
        lines.append(previous)
    lines.append(f"Rolled-up dialogue batch ({len(messages)} messages):")
    for message in messages[-SUMMARY_TRIGGER_MESSAGES:]:
        label = str(message.role)
        if message.tool_name:
            label = f"{label}:{message.tool_name}"
        snippet = " ".join(message.content.strip().split())
        if len(snippet) > 180:
            snippet = snippet[:180].rstrip() + "..."
        if snippet:
            lines.append(f"- {label}: {snippet}")
    merged = "\n".join(lines)
    return merged[-4000:]
