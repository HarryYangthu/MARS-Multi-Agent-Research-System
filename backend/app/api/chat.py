"""Chat API — talk to the Commander (master Agent)."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.dependencies import get_orchestrator, get_run_store
from app.bridge.commander import Commander
from app.bridge.commander_errors import conversation_failure
from app.bridge.conversation_edit import MessageEditConflict, MessageEditRequest
from app.bridge.commander_session import (
    ChatMessage,
    CommanderSession,
    get_session_store,
)

router = APIRouter(prefix="/api/chat", tags=["chat"])


class CreateConversationPayload(BaseModel):
    project: str = Field(default="pimc")
    experiment_id: str = Field(default="", max_length=64)


class MessagePayload(BaseModel):
    text: str = Field(..., min_length=1)
    # Inline base64 images (data:image/png;base64,...), bounded by count and size.
    images: list[str] = Field(default_factory=list, max_length=4)


_IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
_MAX_IMAGE_BYTES = 4 * 1024 * 1024


def _store_images(conv_id: str, images: list[str]) -> list[str]:
    import base64 as b64
    import binascii
    import uuid as uuid_lib

    from app.bridge.commander_session import _conversations_root

    saved: list[str] = []
    for data_url in images:
        header, _, payload = data_url.partition(",")
        mime = header[5:].split(";")[0] if header.startswith("data:") else ""
        suffix = _IMAGE_TYPES.get(mime)
        if not suffix or not payload:
            raise HTTPException(422, "仅支持 PNG/JPEG/WebP 图片附件")
        try:
            blob = b64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(422, "图片附件编码无效") from None
        if len(blob) > _MAX_IMAGE_BYTES:
            raise HTTPException(422, "图片附件超过 4 MB 限制")
        name = f"{uuid_lib.uuid4().hex}{suffix}"
        target = _conversations_root() / conv_id / "attachments" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
        saved.append(f"attachments/{name}")
    return saved


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
    attachments: list[str] = Field(default_factory=list)


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
    activities: list[dict[str, Any]] = Field(default_factory=list)


def _msg_view(m: ChatMessage) -> MessageView:
    return MessageView(
        id=m.id,
        turn_id=m.turn_id,
        role=m.role,
        content=m.content,
        timestamp=m.timestamp,
        state=m.state,
        attachments=list(m.attachments),
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


@router.get("/conversations/{conv_id}/attachments/{name}")
def get_attachment(conv_id: str, name: str) -> FileResponse:
    from app.bridge.commander_session import _conversations_root

    if not re.fullmatch(r"[0-9a-f]{32}\.(png|jpg|webp)", name):
        raise HTTPException(404, "附件不存在")
    path = _conversations_root() / conv_id / "attachments" / name
    if not path.is_file():
        raise HTTPException(404, "附件不存在")
    media = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}[name.rsplit(".", 1)[1]]
    return FileResponse(path, media_type=media)


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
    saved = _store_images(conv_id, payload.images) if payload.images else []
    if saved:
        payload.text = (payload.text.rstrip() + "\n\n[用户附图 " + str(len(saved)) + " 张："
                        + "、".join(saved) + "；当前对话模型为文本通道，图片已存档待视觉模型接入]")
    commander = Commander(orchestrator=get_orchestrator(), run_store=get_run_store())
    try:
        await commander.handle_user_message(session, payload.text)
        if saved:
            for chat_message in reversed(session.messages):
                if chat_message.role == "user":
                    chat_message.attachments = saved
                    break
    except Exception as exc:
        failure = conversation_failure(exc)
        if failure is None:
            raise
        code, message = failure
        raise HTTPException(status_code=409, detail={
            'code': code, 'error': message, 'message_saved': True,
        }) from exc
    finally:
        store.persist(session)
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
