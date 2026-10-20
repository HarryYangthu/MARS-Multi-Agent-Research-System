"""Chat API — talk to the Commander (master Agent)."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.dependencies import get_orchestrator, get_run_store
from app.bridge.commander import Commander
from app.bridge.commander_errors import CommanderDecisionError, conversation_failure
from app.bridge.conversation_edit import MessageEditConflict, MessageEditRequest
from app.bridge.commander_session import (
    ChatMessage,
    CommanderSession,
    get_session_store,
)

router = APIRouter(prefix="/api/chat", tags=["chat"])


def preserve_conversation_on_response_error(session: CommanderSession, user_message_id: str | None = None) -> None:
    """Last-resort presentation failure stays in chat, never stops a run."""
    users = [(index, message) for index, message in enumerate(session.messages)
             if message.role == 'user' and (user_message_id is None or message.id == user_message_id)]
    index, user = users[-1] if users else (-1, None)
    boundary = (next((offset for offset in range(index + 1, len(session.messages))
                     if session.messages[offset].role == 'user'), len(session.messages)) if user is not None
                else len(session.messages))
    previous_cursor = session.edit_cursor
    session.edit_cursor = boundary
    try:
        session.add(ChatMessage(role='assistant', turn_id=(user.turn_id or user.id) if user is not None else None,
            content='当前回复没有可执行的新操作。你的请求、对话和已有任务均已保留，已有实验不会因此被停止。'))
    finally:
        session.edit_cursor = previous_cursor


class CreateConversationPayload(BaseModel):
    project: str = Field(default="pimc")
    experiment_id: str = Field(default="", max_length=64)


class MessagePayload(BaseModel):
    text: str = Field(..., min_length=1)


class EditMessagePayload(MessagePayload):
    expected_content: str
    expected_timestamp: str


class OpenRunConversationPayload(BaseModel):
    project: str = Field(..., min_length=1)
    experiment_id: str | None = Field(default=None, max_length=64)


class AutoModePayload(BaseModel):
    auto_mode: bool


class MessageView(BaseModel):
    id: str
    turn_id: str | None = None
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
    experiment_id: str = ""
    auto_mode: bool
    metric_targets: dict[str, float]
    messages: list[MessageView]
    processing: bool = False
    active_turn_id: str | None = None
    completion_state: dict[str, Any] = Field(default_factory=dict)
    activities: list[dict[str, Any]] = Field(default_factory=list)


def _msg_view(m: ChatMessage) -> MessageView:
    return MessageView(
        id=m.id,
        turn_id=m.turn_id,
        role=m.role,
        content=m.content,
        timestamp=m.timestamp,
        state=m.state,
        tool_name=m.tool_name,
        tool_args=m.tool_args,
        tool_result=m.tool_result,
    )


def _conv_view(s: CommanderSession) -> ConversationView:
    s.auto_mode = _managed_mode(s)
    return ConversationView(
        conv_id=s.conv_id,
        project=s.project,
        state=s.state.value,
        linked_run_id=s.linked_run_id,
        experiment_id=s.experiment_id,
        auto_mode=s.auto_mode,
        metric_targets=dict(s.metric_targets),
        messages=[_msg_view(m) for m in s.messages],
        processing=s.processing,
        active_turn_id=s.active_turn_id,
        completion_state=dict(s.completion_state),
        activities=[asdict(item) for item in s.activities],
    )


def _managed_mode(session: CommanderSession) -> bool:
    if session.linked_run_id:
        try:
            return get_orchestrator().session(session.linked_run_id).request.review_mode == "commander"
        except (KeyError, ValueError, OSError):
            return False
    return session.auto_mode


@router.post("/conversations", response_model=ConversationView)
async def create_conversation(payload: CreateConversationPayload) -> ConversationView:
    if payload.experiment_id:
        from app.bridge.experiment_service import require_experiment
        try:
            require_experiment(payload.project, payload.experiment_id)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    session = get_session_store().create(project=payload.project, experiment_id=payload.experiment_id)
    return _conv_view(session)


@router.get("/conversations", response_model=list[dict[str, Any]])
async def list_conversations() -> list[dict[str, Any]]:
    return [s.to_meta() for s in get_session_store().list()]


@router.post("/runs/{run_id}/conversation", response_model=ConversationView)
async def open_run_conversation(run_id: str, payload: OpenRunConversationPayload) -> ConversationView:
    from app.bridge.run_conversation import open_run_conversation as open_existing
    try:
        session = open_existing(
            run_id, project=payload.project, experiment_id=payload.experiment_id,
            sessions=get_session_store(), runs=get_run_store(),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _conv_view(session)


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
    if session.processing:
        raise HTTPException(status_code=409, detail="conversation is already processing a message")
    message_count = len(session.messages)
    try:
        commander = Commander(orchestrator=get_orchestrator(), run_store=get_run_store())
        await commander.handle_user_message(session, payload.text)
    except CommanderDecisionError:
        preserve_conversation_on_response_error(session)
    except Exception as exc:
        failure = conversation_failure(exc)
        if failure is None:
            raise
        code, message = failure
        if len(session.messages) == message_count:
            session.add(ChatMessage(role="user", content=payload.text))
        session.add(ChatMessage(role="assistant", content=message))
        session.completion_state.update(status="blocked", reason=message)
        raise HTTPException(status_code=409, detail={
            'code': code, 'error': message, 'message_saved': True,
        }) from exc
    finally:
        store.persist(session)
    return _conv_view(session)


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str) -> dict[str, Any]:
    store = get_session_store()
    session = store.get(conv_id)
    if session is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    run_store = get_run_store()
    run_id = session.linked_run_id
    run = run_store.get(run_id) if run_id else None
    if session.processing or (run_id and get_orchestrator().run_control(run_id).get("owned_task_active")):
        raise HTTPException(status_code=409, detail="研究正在运行，请先停止或等待完成后删除")
    if run is not None and run.project != session.project:
        raise HTTPException(status_code=409, detail="关联任务不属于当前项目，未删除")
    moved_run = False
    try:
        if run is not None and run_id:
            run_store.trash(run_id)
            moved_run = True
        store.trash(conv_id)
    except (OSError, ValueError) as error:
        if moved_run and run_id:
            run_store.restore(run_id)
        raise HTTPException(status_code=409, detail=str(error)) from error
    if run_id:
        get_orchestrator().discard_session(run_id)
    return {"ok": True, "conv_id": conv_id, "run_id": run_id, "recoverable": True}


@router.post("/conversations/{conv_id}/restore", response_model=ConversationView)
async def restore_conversation(conv_id: str) -> ConversationView:
    try:
        session = get_session_store().restore(conv_id)
        if session.linked_run_id and get_run_store().get_trashed(session.linked_run_id) is not None:
            get_run_store().restore(session.linked_run_id)
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _conv_view(session)


@router.put("/conversations/{conv_id}/messages/{message_id}", response_model=ConversationView)
async def edit_message(conv_id: str, message_id: str, payload: EditMessagePayload) -> ConversationView:
    store = get_session_store()
    session = store.get(conv_id)
    if session is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    if session.processing:
        raise HTTPException(status_code=409, detail="conversation is already processing a message")
    commander = Commander(orchestrator=get_orchestrator(), run_store=get_run_store())
    try:
        await commander.handle_user_message(session, payload.text, edit=MessageEditRequest(
            message_id=message_id, expected_content=payload.expected_content, expected_timestamp=payload.expected_timestamp,
        ))
    except MessageEditConflict as exc:
        raise HTTPException(status_code=409, detail={"error": str(exc), "message_saved": False}) from exc
    except CommanderDecisionError:
        preserve_conversation_on_response_error(session, message_id)
    except Exception as exc:
        failure = conversation_failure(exc)
        code, message = failure or ("regeneration_failed", "消息已替换，但重新生成回复失败。请重新读取对话核对进度；旧回复没有恢复，系统没有自动重试。")
        raise HTTPException(status_code=409, detail={"code": code, "error": message, "message_saved": True}) from exc
    finally:
        store.persist(session)
    return _conv_view(session)


@router.post("/conversations/{conv_id}/auto_mode", response_model=ConversationView)
async def set_auto_mode(conv_id: str, payload: AutoModePayload) -> ConversationView:
    store = get_session_store()
    session = store.get(conv_id)
    if session is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    if session.processing:
        raise HTTPException(status_code=409, detail="主控正在处理消息，请稍后切换审批方式。")
    if session.linked_run_id:
        try:
            get_orchestrator().set_review_mode(session.linked_run_id, project=session.project, enabled=payload.auto_mode)
        except (KeyError, ValueError, OSError, RuntimeError) as error:
            raise HTTPException(status_code=409, detail="审批方式未能保存，请重新读取对话核对。") from error
    session.auto_mode = payload.auto_mode
    store.persist(session)
    return _conv_view(session)
