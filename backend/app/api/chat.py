"""Chat API — talk to the Commander (master Agent)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.dependencies import get_orchestrator, get_run_store
from app.bridge.commander import Commander
from app.bridge.commander_session import (
    ChatMessage,
    CommanderSession,
    get_session_store,
)

router = APIRouter(prefix="/api/chat", tags=["chat"])


class CreateConversationPayload(BaseModel):
    project: str = Field(default="moe-pimc")


class MessagePayload(BaseModel):
    text: str = Field(..., min_length=1)


class AutoModePayload(BaseModel):
    auto_mode: bool


class MessageView(BaseModel):
    role: str
    content: str
    timestamp: str
    state: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_result: dict[str, Any] | None = None


class ConversationView(BaseModel):
    conv_id: str
    project: str
    state: str
    linked_run_id: str | None
    auto_mode: bool
    metric_targets: dict[str, float]
    messages: list[MessageView]


def _msg_view(m: ChatMessage) -> MessageView:
    return MessageView(
        role=m.role,
        content=m.content,
        timestamp=m.timestamp,
        state=m.state,
        tool_name=m.tool_name,
        tool_args=m.tool_args,
        tool_result=m.tool_result,
    )


def _conv_view(s: CommanderSession) -> ConversationView:
    return ConversationView(
        conv_id=s.conv_id,
        project=s.project,
        state=s.state.value,
        linked_run_id=s.linked_run_id,
        auto_mode=s.auto_mode,
        metric_targets=dict(s.metric_targets),
        messages=[_msg_view(m) for m in s.messages],
    )


@router.post("/conversations", response_model=ConversationView)
async def create_conversation(payload: CreateConversationPayload) -> ConversationView:
    session = get_session_store().create(project=payload.project)
    return _conv_view(session)


@router.get("/conversations", response_model=list[dict[str, Any]])
async def list_conversations() -> list[dict[str, Any]]:
    return [s.to_meta() for s in get_session_store().list()]


@router.get("/conversations/{conv_id}", response_model=ConversationView)
async def get_conversation(conv_id: str) -> ConversationView:
    session = get_session_store().get(conv_id)
    if session is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return _conv_view(session)


@router.post("/conversations/{conv_id}/message", response_model=ConversationView)
async def post_message(conv_id: str, payload: MessagePayload) -> ConversationView:
    store = get_session_store()
    session = store.get(conv_id)
    if session is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    commander = Commander(orchestrator=get_orchestrator(), run_store=get_run_store())
    await commander.handle_user_message(session, payload.text)
    store.persist(session)
    return _conv_view(session)


@router.post("/conversations/{conv_id}/auto_mode", response_model=ConversationView)
async def set_auto_mode(conv_id: str, payload: AutoModePayload) -> ConversationView:
    store = get_session_store()
    session = store.get(conv_id)
    if session is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    session.auto_mode = payload.auto_mode
    store.persist(session)
    return _conv_view(session)
