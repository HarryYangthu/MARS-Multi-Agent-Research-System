"""Commander conversation session storage.

Holds the multi-turn dialogue, the conversation FSM state, the linked run (if
any), the auto/semi-auto intervention flag, and any user-set metric targets
(the "expectation" that the self-healing loop drives toward).

Persisted under ``conversations/<conv_id>/`` (sibling of ``runs/``):
    session.json    — metadata + current state + linked run + auto_mode
    messages.jsonl  — append-only dialogue + tool-call trace
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from app.harness.runtime.conversation_state import ConversationState
from app.settings import repo_root

Role = Literal["user", "assistant", "system", "tool"]

_SLUG_RE = re.compile(r"[^a-z0-9_]+")
SUMMARY_TRIGGER_MESSAGES = 20
SUMMARY_RETAIN_MESSAGES = 12
SUMMARY_TRIGGER_TOKENS = 5600


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CommanderSession:
    conv_id: str
    project: str
    state: ConversationState = ConversationState.IDLE
    linked_run_id: str | None = None
    auto_mode: bool = False           # False = semi-auto (ask before each pull-back)
    metric_targets: dict[str, float] = field(default_factory=dict)
    rolling_summary: str = ""
    summary_updated_at: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    messages: list[ChatMessage] = field(default_factory=list)

    def add(self, msg: ChatMessage) -> ChatMessage:
        msg.state = self.state.value
        self.messages.append(msg)
        self._maybe_rollup()
        self.updated_at = _now()
        return msg

    def context_messages(self) -> list[ChatMessage]:
        return list(self.messages)

    def to_meta(self) -> dict[str, Any]:
        return {
            "conv_id": self.conv_id,
            "project": self.project,
            "state": self.state.value,
            "linked_run_id": self.linked_run_id,
            "auto_mode": self.auto_mode,
            "metric_targets": dict(self.metric_targets),
            "rolling_summary": self.rolling_summary,
            "summary_updated_at": self.summary_updated_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": len(self.messages),
        }

    def _maybe_rollup(self) -> None:
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

    def __init__(self) -> None:
        self._sessions: dict[str, CommanderSession] = {}

    # ------------------------------------------------------------- create

    def create(self, *, project: str, now: datetime | None = None) -> CommanderSession:
        ts = (now or datetime.now(tz=timezone.utc)).strftime("%Y-%m-%dT%H%M%S")
        conv_id = f"conv_{ts}"
        # collision guard
        if conv_id in self._sessions or (_conversations_root() / conv_id).exists():
            conv_id = f"conv_{ts}_{len(self._sessions)}"
        session = CommanderSession(conv_id=conv_id, project=project)
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
        root = _conversations_root()
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
        d = _conversations_root() / session.conv_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "session.json").write_text(
            json.dumps(session.to_meta(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        with (d / "messages.jsonl").open("w", encoding="utf-8") as fh:
            for m in session.messages:
                fh.write(json.dumps(m.to_dict(), ensure_ascii=False) + "\n")

    def _load(self, conv_id: str) -> CommanderSession | None:
        d = _conversations_root() / conv_id
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
            for line in msg_path.read_text(encoding="utf-8").splitlines():
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
                    )
                )
        try:
            state = ConversationState(meta.get("state", "idle"))
        except ValueError:
            state = ConversationState.IDLE
        return CommanderSession(
            conv_id=conv_id,
            project=str(meta.get("project", "moe-pimc")),
            state=state,
            linked_run_id=meta.get("linked_run_id"),
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
        )


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
