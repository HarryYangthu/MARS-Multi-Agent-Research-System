"""Same-origin TensorBoard UI and data proxy; callers select projects/runs, never hosts."""
from __future__ import annotations

import time

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from app.api.dependencies import get_run_store
from app.bridge.tensorboard_service import get_tensorboard_manager, project_logdirs

router = APIRouter(prefix="/api/tensorboard", tags=["tensorboard"])


class OpenTensorBoard(BaseModel):
    project: str
    run_id: str | None = None


@router.post("/sessions")
async def open_session(body: OpenTensorBoard) -> dict[str, object]:
    try:
        if body.run_id:
            run = get_run_store().get(body.run_id)
            if run is None or run.project != body.project:
                raise HTTPException(status_code=404, detail="运行不属于当前项目或已删除")
            directories = [run.root.resolve()]
        else:
            directories = project_logdirs(body.project)
        session = await get_tensorboard_manager().ensure(body.project, body.run_id, directories)
        return session.view()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (RuntimeError, OSError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/status")
async def execution_display_status(project: str) -> dict[str, object]:
    active = get_tensorboard_manager().activations.get(project)
    return {"active": active}


@router.api_route("/view/{key}", methods=["GET", "HEAD", "POST"])
@router.api_route("/view/{key}/{path:path}", methods=["GET", "HEAD", "POST"])
async def proxy_tensorboard(key: str, request: Request, path: str = "") -> Response:
    manager = get_tensorboard_manager()
    session = manager.sessions.get(key)
    if session is None or session.process.returncode is not None:
        raise HTTPException(status_code=503, detail="TensorBoard 会话已结束，请重新打开实验台")
    if any(part in {".", ".."} for part in path.split("/")) or "\\" in path:
        raise HTTPException(status_code=400, detail="invalid TensorBoard path")
    session.last_used = time.monotonic()
    upstream = f"http://127.0.0.1:{session.port}{session.prefix}/{path}"
    headers = {name: request.headers[name] for name in ("content-type", "accept") if name in request.headers}
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=30, follow_redirects=False) as client:
            response = await client.request(request.method, upstream, params=request.url.query,
                                            content=await request.body(), headers=headers)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="TensorBoard 暂时不可用，请重新打开实验台") from exc
    # httpx has decompressed the body; never forward its old encoding or length.
    outgoing = {name: response.headers[name] for name in ("content-type", "location") if name in response.headers}
    if "location" in outgoing:
        outgoing["location"] = outgoing["location"].removeprefix(f"http://127.0.0.1:{session.port}")
    outgoing["Cache-Control"] = "no-store"
    return Response(response.content, status_code=response.status_code, headers=outgoing)
