"""REST endpoints for the Run lifecycle."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.api.dependencies import existing_orchestrator, get_orchestrator, get_run_store
from app.bridge.orchestrator import RunRequest, RunSession
from app.bridge.idea_input_context import IdeaRequirements, validate_idea_context
from app.bridge.run_observability import build_run_observability
from app.bridge.research_activity import build_research_activity
from app.bridge.research_contract_service import ResearchContractIntegrityError
from app.bridge.research_run_service import (
    CONTRACT_HASH_KEY, ResearchExecutionAdmission, check_research_run_storage_paths, research_execution_admission,
)
from app.harness.runtime.readiness import ProductionReadinessError, assert_ready_for_run
from app.storage.data_source_store import DataSourceStore
from app.storage.run_state_store import RunStateIntegrityError, RunStateStore

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.get('/{run_id}/code-repository')
def get_code_directory(run_id: str, project: str, path: str = '', offset: int = 0,
                       repository_token: str = '') -> dict[str, Any]:
    return _code_repository(run_id, project, path, offset, repository_token, directory=True)


@router.get('/{run_id}/code-repository/file')
def get_code_file(run_id: str, project: str, path: str, start: int = 0,
                  repository_token: str = '', version: str = '') -> dict[str, Any]:
    return _code_repository(run_id, project, path, start, repository_token, directory=False, version=version)


def _code_repository(run_id: str, project: str, path: str, offset: int, token: str,
                     *, directory: bool, version: str = '') -> dict[str, Any]:
    from app.bridge.code_repository import run_code_repository
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail='任务不存在')
    try:
        browser = run_code_repository(run, project=project)
        result = (browser.directory(path, offset=offset, token=token) if directory
                  else browser.file(path, start=offset, version=version, token=token))
        return {'run_id': run_id, 'project': project, **result}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=409, detail='代码文件不可读取，请刷新并核对项目配置') from exc


@router.get('/{run_id}/code-changes')
def get_code_changes(run_id: str, project: str) -> dict[str, Any]:
    return _code_changes(run_id, project)


@router.get('/{run_id}/code-changes/{change_id}')
def get_code_change(run_id: str, change_id: str, project: str) -> dict[str, Any]:
    return _code_changes(run_id, project, change_id)


def _code_changes(run_id: str, project: str, change_id: str | None = None) -> dict[str, Any]:
    from app.bridge.completed_code_changes import completed_code_changes as code_changes
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail='任务不存在')
    try:
        return code_changes(run, project=project, change_id=change_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='代码改动记录不存在或暂不可读取') from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail='代码改动记录无法校验，请核对项目及记录。') from exc


class RecoveryPayload(BaseModel):
    project: str
    action: Literal['resume', 'retry']
    node: str
    token: str = Field(min_length=1)


class ExecutionConfirmationPayload(BaseModel):
    project: str
    token: str = Field(min_length=64, max_length=64, pattern='^[0-9a-f]+$')


def _execution_configuration(run_id: str, project: str, *, include_hidden: bool = False) -> tuple[RunSession, dict[str, Any]]:
    from app.bridge.execution_confirmation import execution_preview
    from app.bridge.node_key import parse_node_key
    from app.harness.runtime.state_machine import NodeState
    session = _execution_session(run_id)
    if session.run.project != project:
        raise HTTPException(status_code=409, detail='任务不属于当前项目，请重新打开对应对话。')
    nodes = [key for key in session.graph.nodes if parse_node_key(key).stage == 'execution']
    if not nodes:
        return session, {'visible': False}
    node = max(nodes, key=lambda key: parse_node_key(key).attempt)
    state = session.graph.state(node)
    coding = [key for key in session.graph.nodes if parse_node_key(key).stage == 'coding']
    visible = not coding or session.graph.state(max(coding, key=lambda key: parse_node_key(key).attempt)) in {
        NodeState.DONE, NodeState.SKIPPED}
    visible = visible and state != NodeState.SKIPPED
    if not visible and not include_hidden:
        return session, {'visible': False}
    if state == NodeState.DONE:
        from app.execution.job_journal import job_states
        return session, {'visible': visible, 'state': state.value, 'run_id': run_id, 'project': project,
            'node': node, 'runtime_mode': 'deterministic', 'token': '', 'launch_ready': False,
            'can_confirm': False, 'confirmed': True, 'jobs': job_states(session.run.root)}
    try:
        view = execution_preview(session.run, node)
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail='仿真配置无法核验，请检查项目配置与任务记录。') from exc
    return session, {**view, 'visible': visible,
        'state': state.value, 'launch_ready': state == NodeState.APPROVED,
        'can_confirm': state == NodeState.APPROVED and not view['blockers'] and not session.read_only}


@router.get('/{run_id}/execution-configuration')
def get_execution_configuration(run_id: str, project: str) -> dict[str, Any]:
    return _execution_configuration(run_id, project)[1]


class ExecutionBoundaryPayload(BaseModel):
    project: str
    stop_after_execution: bool


@router.post('/{run_id}/execution-boundary')
def set_execution_boundary(run_id: str, payload: ExecutionBoundaryPayload) -> dict[str, Any]:
    session = _execution_session(run_id)
    if session.run.project != payload.project:
        raise HTTPException(status_code=409, detail='任务不属于当前项目。')
    try:
        get_orchestrator().set_stage_limit(run_id, stop_after='execution' if payload.stop_after_execution else None)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _execution_configuration(run_id, payload.project)[1]


@router.post('/{run_id}/execution-configuration/confirm')
async def confirm_execution_configuration(run_id: str, payload: ExecutionConfirmationPayload) -> dict[str, Any]:
    import asyncio
    from app.bridge.execution_confirmation import save_confirmation
    session, view = await asyncio.to_thread(_execution_configuration, run_id, payload.project, include_hidden=True)
    if view.get('token') != payload.token:
        raise HTTPException(status_code=409, detail='配置或代码已变化，请重新核对；尚未启动新的仿真。')
    if view.get('confirmed') and view.get('state') in {'running', 'done'}:
        return {'ok': True, 'confirmed': True, 'status': 'already_started', 'run_id': run_id}
    if not view.get('can_confirm'):
        raise HTTPException(status_code=409, detail='当前配置还不可启动：' + '；'.join(view.get('blockers', [])))
    try:
        receipt = await asyncio.to_thread(save_confirmation, session.run, view['node'], payload.token)
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail='配置未确认，请刷新并核对配置变化。') from exc
    if not receipt['created']:
        return {'ok': True, 'confirmed': True, 'status': 'already_confirmed', 'run_id': run_id}
    result = await get_orchestrator().resume_after_artifact_approval(run_id=run_id, agent='execution')
    return {**result, 'confirmed': True}


@router.get('/{run_id}/recovery')
async def get_recovery(run_id: str, project: str) -> dict[str, Any]:
    from app.bridge.run_recovery import recovery_status
    try:
        return recovery_status(get_orchestrator(), run_id, project=project)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='任务或执行记录不存在，请打开任务详情核对。') from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail='恢复记录无法校验，请打开任务详情核对。') from exc


@router.post('/{run_id}/recovery')
async def post_recovery(run_id: str, payload: RecoveryPayload) -> dict[str, Any]:
    from app.bridge.run_recovery import recover_run
    try:
        result = await recover_run(get_orchestrator(), run_id, project=payload.project,
                                  action=payload.action, node=payload.node, token=payload.token)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='任务或执行记录不存在。') from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail='恢复记录无法校验，请核对任务详情；没有自动重试。') from exc
    if not result.get('ok'):
        raise HTTPException(status_code=409, detail=result)
    return result


class CreateRunPayload(BaseModel):
    task: str = Field(..., min_length=1)
    project: str = Field(..., min_length=1)
    entrypoint: str = Field(default="pipeline")
    standalone: bool = False
    user_request: str = ""
    # When true, every agent auto-approves and the Commander self-heal loop
    # auto-appends repair attempts — the run executes end-to-end without HITL
    # clicks. Used for demos / autonomous runs; the UI default stays human-gated.
    auto_approve: bool = False
    # Optional: pre-written markdown for the entrypoint Agent's first artifact.
    # When provided, the API validates it against the matching schema, drops
    # it as <agent>/<stem>.v1.md, and the orchestrator skips the LLM draft for
    # that node — the run goes straight into HITL review.
    seed_artifact: str | None = None
    data_source: "DataSourceSelection | None" = None
    idea_mode: Literal["fast"] | None = Field(default=None, description="Only fast is available; omit to use the default.")
    idea_budget_profile: Literal["fast", "balanced", "thorough"] | None = None
    project_inputs: dict[str, Any] = Field(default_factory=dict)
    idea_context: dict[str, str] | None = None
    idea_scope: Literal["method_proposal", "project_proposal"] | None = None
    idea_requirements: IdeaRequirements | None = None
    execution_context: dict[str, str] = Field(default_factory=dict)
    evaluation_policy: dict[str, Any] | None = None
    selected_skills_by_agent: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("idea_mode", mode="before")
    @classmethod
    def validate_idea_mode(cls, value: Any) -> Any:
        if isinstance(value, str) and value in {"auto", "deep"}:
            raise ValueError(f"idea_mode={value} is not available; use fast or omit idea_mode")
        return value

    @field_validator("idea_context", mode="before")
    @classmethod
    def validate_context(cls, value: Any) -> dict[str, str] | None:
        return None if value is None else validate_idea_context(value)

    def idea_request_extra(self) -> dict[str, Any]:
        """Preserve labeled inputs and only explicitly requested overrides."""
        extra: dict[str, Any] = {}
        if self.idea_context is not None:
            extra["idea_context"] = dict(self.idea_context)
        if self.idea_scope is not None:
            extra["scope"] = self.idea_scope
        if self.idea_requirements is not None:
            extra["idea_requirements"] = self.idea_requirements.model_dump(exclude_none=True)
        return extra


class DataSourceSelection(BaseModel):
    id: str = Field(..., min_length=1)
    fs_mhz: float | None = None
    kind: str | None = None
    channel_count: int | None = None
    description: str | None = None


class RunSummary(BaseModel):
    run_id: str
    project: str
    task: str
    entrypoint: str
    created_at: str


class TrashRunSummary(RunSummary):
    deleted_at: str
    expires_at: str
    days_remaining: int


class RunDetail(RunSummary):
    states: dict[str, str]
    graph: dict[str, Any]
    status: str | None = None
    termination: dict[str, Any] | None = None
    read_only: bool = False
    read_only_reason: str | None = None
    available_actions: list[str] = Field(default_factory=list)
    research_task_sha256: str | None = None
    execution_admission: ResearchExecutionAdmission | None = None


class RetryAgentPayload(BaseModel):
    reason: str = ""
    restart_stopped: bool = False


def _ensure_active_run(run_id: str) -> None:
    try:
        run = get_run_store().get(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")


def _execution_session(run_id: str) -> RunSession:
    try:
        session = get_orchestrator().session(run_id)
        check_research_run_storage_paths(session.run)
        return session
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    except (RunStateIntegrityError, ResearchContractIntegrityError) as exc:
        raise HTTPException(status_code=409, detail={"status": "run_state_unavailable",
            "research_started": False, "error_type": type(exc).__name__, "control": _run_control(run_id)}) from exc


def _run_control(run_id: str) -> dict[str, Any]:
    owner = existing_orchestrator()
    if owner is not None:
        return owner.run_control(run_id)
    return {"run_id": run_id, "owned_task_active": False, "stopping": False, "owned_task_done": None,
            "cleanup_complete": None, "state_persisted": None, "state_persistence_error": None,
            "available_actions": []}


@router.post("", response_model=RunDetail)
async def create_run(payload: CreateRunPayload) -> RunDetail:
    try:
        assert_ready_for_run(project=payload.project)
    except ProductionReadinessError as exc:
        raise HTTPException(
            status_code=503,
            detail=exc.report.to_dict(),
        ) from exc
    data_source = _resolve_data_source_selection(
        selection=payload.data_source,
        project=payload.project,
    )
    # Project execution target: selecting GPU must never silently fall back to
    # local simulation. Unmet prerequisites or a non-remote runtime block the
    # run here with the exact reasons instead of starting a local replacement.
    from app.bridge.project_execution_config import execution_config_status
    try:
        execution_status = execution_config_status(payload.project)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="项目执行配置无法核对，请检查项目配置；尚未创建研究任务。") from exc
    if execution_status["device"] == "remote_gpu":
        blockers = [*execution_status["missing"], *execution_status["findings"]]
        if blockers:
            raise HTTPException(status_code=422, detail={
                "code": "gpu_execution_not_ready",
                "message": "项目已配置使用 GPU，但远端执行前置条件未满足；未发起本地仿真。",
                "missing": execution_status["missing"],
                "findings": execution_status["findings"]})
        if not execution_status["remote_capable_runtime"]:
            raise HTTPException(status_code=422, detail={
                "code": "gpu_runtime_not_selected",
                "message": "项目已配置使用 GPU，但当前执行后端未启用 remote_gpu；未发起本地仿真。请先启用远端 GPU 执行后端。",
                "runtime_backend": execution_status["runtime_backend"]})
        remote = execution_status.get("remote_gpu", {})
        context = dict(payload.execution_context or {})
        context["execution.target"] = f"remote_gpu@{remote.get('host', '')}"
        payload = payload.model_copy(update={"execution_context": context})
    orch = get_orchestrator()
    request_extra: dict[str, Any] = payload.idea_request_extra()
    if payload.idea_mode is not None:
        request_extra["idea_mode"] = payload.idea_mode
    if payload.idea_budget_profile is not None:
        request_extra["idea_budget_profile"] = payload.idea_budget_profile
    if payload.project_inputs:
        request_extra["project_inputs"] = dict(payload.project_inputs)
    if payload.execution_context:
        request_extra["execution_context"] = dict(payload.execution_context)
    if payload.evaluation_policy is not None:
        request_extra["evaluation_policy"] = dict(payload.evaluation_policy)
    if payload.selected_skills_by_agent:
        request_extra["selected_skills_by_agent"] = dict(payload.selected_skills_by_agent)
    request = RunRequest(
        task=payload.task,
        project=payload.project,
        entrypoint=payload.entrypoint,  # type: ignore[arg-type]
        standalone=payload.standalone,
        user_request=payload.user_request,
        auto_approve=payload.auto_approve,
        data_source=data_source,
        extra=request_extra,
    )
    try:
        session = orch.create_session(request)
    except ProductionReadinessError as exc:
        raise HTTPException(status_code=503, detail=exc.report.to_dict()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # If the caller supplied a seed artifact, validate + persist it under the
    # entrypoint Agent's directory as v1.md. The orchestrator's agent_runner
    # later detects the existing v1 and skips the LLM draft for that node.
    if payload.seed_artifact and payload.entrypoint != "pipeline":
        from app.api.templates import SCHEMA_TO_AGENT_AND_STEM
        from app.harness.schema.validator import validate_document
        from app.storage.artifact_store import ArtifactStore

        # Reverse-lookup: agent name -> (schema, stem)
        schema_for: dict[str, tuple[str, str]] = {
            agent: (sid, stem) for sid, (agent, stem) in SCHEMA_TO_AGENT_AND_STEM.items()
        }
        if payload.entrypoint in schema_for:
            schema_id, stem = schema_for[payload.entrypoint]
            result = validate_document(
                payload.seed_artifact, expected_schema=schema_id
            )
            if not result.valid:
                # Detailed schema error so the form can highlight problems.
                raise HTTPException(
                    status_code=422,
                    detail={
                        "schema": schema_id,
                        "errors": [
                            {"path": e.path, "message": e.message}
                            for e in result.errors
                        ],
                    },
                )
            store = ArtifactStore(session.run)
            store.write(text=payload.seed_artifact, expected_schema=schema_id)

    return RunDetail(
        run_id=session.run.run_id,
        project=session.run.project,
        task=session.run.task,
        entrypoint=session.run.entrypoint,
        created_at=session.run.created_at,
        states={k: s.value for k, s in session.graph.all_states().items()},
        graph=session.graph.to_dict(),
    )


def _resolve_data_source_selection(
    selection: DataSourceSelection | None,
    *,
    project: str,
) -> dict[str, Any] | None:
    store = DataSourceStore()
    if selection is None:
        return store.default_profile(project)
    try:
        profile = store.load(selection.id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=422, detail="selected data source not found") from exc
    if selection.fs_mhz is not None:
        profile["fs_mhz"] = selection.fs_mhz
        profile["sample_rate_hz"] = selection.fs_mhz * 1_000_000.0
    if selection.kind is not None:
        profile["kind"] = selection.kind
    if selection.channel_count is not None:
        profile["channel_count"] = selection.channel_count
    if selection.description is not None:
        profile["description"] = selection.description
    return profile


@router.get("", response_model=list[RunSummary])
async def list_runs(project: str = "") -> list[RunSummary]:
    store = get_run_store()
    project_filter = project.strip()
    return [
        RunSummary(
            run_id=r.run_id,
            project=r.project,
            task=r.task,
            entrypoint=r.entrypoint,
            created_at=r.created_at,
        )
        for r in store.list()
        if not project_filter or r.project == project_filter
    ]


@router.get("/trash", response_model=list[TrashRunSummary])
async def list_trashed_runs(project: str = "") -> list[TrashRunSummary]:
    store = get_run_store()
    project_filter = project.strip()
    return [
        TrashRunSummary(
            run_id=r.run_id,
            project=r.project,
            task=r.task,
            entrypoint=r.entrypoint,
            created_at=r.created_at,
            deleted_at=r.deleted_at,
            expires_at=r.expires_at,
            days_remaining=r.days_remaining,
        )
        for r in store.list_trashed()
        if not project_filter or r.project == project_filter
    ]


@router.get("/{run_id}", response_model=RunDetail)
async def get_run(run_id: str) -> RunDetail:
    _ensure_active_run(run_id)
    session = _execution_session(run_id)
    try:
        snapshot = RunStateStore(session.run).load()
    except RunStateIntegrityError as exc:
        raise HTTPException(status_code=409, detail={"status": "run_state_unavailable",
            "research_started": False, "error_type": type(exc).__name__, "control": _run_control(run_id)}) from exc
    return RunDetail(
        run_id=session.run.run_id,
        project=session.run.project,
        task=session.run.task,
        entrypoint=session.run.entrypoint,
        created_at=session.run.created_at,
        states={k: s.value for k, s in session.graph.all_states().items()},
        graph=session.graph.to_dict(),
        status=snapshot.status if snapshot else None,
        termination=session.termination,
        read_only=session.read_only,
        read_only_reason=session.read_only_reason,
        available_actions=(["migrate_state"] if session.read_only_reason == "legacy_state_migration_required" else [])
                          + _run_control(run_id)["available_actions"],
        research_task_sha256=session.request.extra.get(CONTRACT_HASH_KEY),
        execution_admission=research_execution_admission(session.run, session.request.extra),
    )


@router.get("/{run_id}/control")
async def get_run_control(run_id: str) -> dict[str, Any]:
    # Even a removed/corrupt metadata file must not hide a live cancellation
    # handle. Absence of process ownership is not evidence of remote completion.
    return _run_control(run_id)


@router.delete("/{run_id}", response_model=TrashRunSummary)
async def delete_run(run_id: str) -> TrashRunSummary:
    store = get_run_store()
    try:
        trashed = store.trash(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    get_orchestrator().discard_session(run_id)
    return TrashRunSummary(
        run_id=trashed.run_id,
        project=trashed.project,
        task=trashed.task,
        entrypoint=trashed.entrypoint,
        created_at=trashed.created_at,
        deleted_at=trashed.deleted_at,
        expires_at=trashed.expires_at,
        days_remaining=trashed.days_remaining,
    )


@router.post("/{run_id}/restore", response_model=RunSummary)
async def restore_run(run_id: str) -> RunSummary:
    store = get_run_store()
    try:
        restored = store.restore(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="run not found in trash") from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RunSummary(
        run_id=restored.run_id,
        project=restored.project,
        task=restored.task,
        entrypoint=restored.entrypoint,
        created_at=restored.created_at,
    )


@router.delete("/trash/{run_id}", status_code=204)
async def permanently_delete_run(run_id: str) -> None:
    store = get_run_store()
    try:
        store.delete_trashed(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="run not found in trash") from exc


@router.get("/{run_id}/activity")
def get_research_activity(run_id: str, limit: int = 500) -> dict[str, Any]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return build_research_activity(run, limit=max(1, min(limit, 500)))


@router.get("/{run_id}/observability")
async def get_run_observability(run_id: str, limit: int = 200) -> dict[str, Any]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return build_run_observability(run, limit=max(1, min(limit, 500)))


@router.get("/{run_id}/health")
async def get_run_health(run_id: str) -> dict[str, Any]:
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    view = build_run_observability(run, limit=20)
    return {
        "run_id": run_id,
        "status": view["status"],
        "health": view["health"],
        "latest_event_at": view["latest_event_at"],
    }


@router.post("/{run_id}/start", status_code=202)
async def start_run(run_id: str) -> dict[str, str]:
    _ensure_active_run(run_id)
    orch = get_orchestrator()
    session = _execution_session(run_id)
    if research_execution_admission(session.run, session.request.extra) is not None:
        raise HTTPException(status_code=409, detail=orch.start_owned_run(run_id))
    if session.read_only:
        raise HTTPException(
            status_code=409,
            detail=f"run is read-only ({session.read_only_reason}); historical artifacts cannot resume execution; service-owned runs use their dedicated service API",
        )
    try:
        assert_ready_for_run(project=session.run.project)
    except ProductionReadinessError as exc:
        raise HTTPException(status_code=503, detail=exc.report.to_dict()) from exc
    result = orch.start_owned_run(run_id)
    if not result["ok"]:
        raise HTTPException(status_code=409, detail=result)
    return {"status": str(result["status"]), "run_id": run_id}


@router.post("/{run_id}/migrate-state")
async def migrate_run_state(run_id: str) -> dict[str, Any]:
    _ensure_active_run(run_id)
    try:
        result = get_orchestrator().migrate_run_state(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail={"status": "migration_blocked", "error": str(exc)}) from exc
    if not result["ok"]:
        raise HTTPException(status_code=409, detail=result)
    return result


@router.post("/{run_id}/replay-state-events")
async def replay_state_events(run_id: str) -> dict[str, Any]:
    _ensure_active_run(run_id)
    run = get_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    store = RunStateStore(run)
    # Reject artifact-only/legacy history before acquiring locks or recovering
    # sessions. Replay requires committed journal events, never an inferred graph.
    if not store.authority_path.is_file():
        raise HTTPException(status_code=409, detail={"status": "state_replay_unavailable",
                                                    "reason": "missing_state_authority"})
    try:
        snapshot = store.load()
        if snapshot is None or snapshot.migration_required:
            raise ValueError("committed run state authority required")
        delivered = await get_orchestrator().replay_state_events(run_id)
        pending = len(store.pending_events())
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail={"status": "state_replay_unavailable",
                                                    "error_type": type(exc).__name__}) from exc
    return {"run_id": run_id, "status": "pending" if pending else "replayed",
            "delivered_count": delivered, "pending_count": pending, "research_started": False}


@router.post("/{run_id}/resume", status_code=202)
async def resume_run(run_id: str) -> dict[str, Any]:
    _ensure_active_run(run_id)
    orch = get_orchestrator()
    session = _execution_session(run_id)
    if research_execution_admission(session.run, session.request.extra) is not None:
        raise HTTPException(status_code=409, detail=orch.resume_owned_run(run_id))
    if session.read_only:
        raise HTTPException(status_code=409, detail=f"run is read-only ({session.read_only_reason}); no trusted execution state is available to this API")
    try:
        assert_ready_for_run(project=session.run.project)
    except ProductionReadinessError as exc:
        raise HTTPException(status_code=503, detail=exc.report.to_dict()) from exc
    result = orch.resume_owned_run(run_id)
    if not result["ok"]:
        raise HTTPException(status_code=409, detail=result)
    return result


@router.post("/{run_id}/agents/{agent}/retry", status_code=202)
async def retry_agent(
    run_id: str,
    agent: str,
    payload: RetryAgentPayload,
) -> dict[str, str]:
    _ensure_active_run(run_id)
    orch = get_orchestrator()
    reason = payload.reason.strip() or "人工请求重试失败 Agent。"
    try:
        result = await orch.request_artifact_revision(
            run_id=run_id,
            agent=agent,
            reason=reason,
            restart_stopped=payload.restart_stopped,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result)
    return {
        "status": str(result.get("status") or "revision_started"),
        "run_id": run_id,
        "agent": agent,
        "node": str(result.get("node") or agent),
    }


@router.post("/{run_id}/stop", status_code=202)
async def stop_run(run_id: str) -> dict[str, Any]:
    owner = existing_orchestrator()
    if owner is None or not owner.run_control(run_id)["owned_task_active"]:
        _ensure_active_run(run_id)
        if owner is None:
            run = get_run_store().get(run_id)
            if run is not None and run.meta.get("research_task_sha256") is not None:
                # Contract jobs retain durable ownership across app restarts.
                # This creates only the controller, never starts research.
                owner = get_orchestrator()
            else:
                raise HTTPException(status_code=409, detail={"ok": False, "status": "not_owned", "run_id": run_id,
                    "error": "no live task owned by this process; historical execution was not changed"})
    result = await owner.stop_owned_run(run_id)
    if not result["ok"]:
        raise HTTPException(status_code=409, detail=result)
    return result
