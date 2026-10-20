"""Commander tool set — the master Agent's hands.

Thin wrappers over the EXISTING engine (orchestrator / hitl / kb / bridge_agent).
Nothing here re-implements the self-healing loop; it just exposes callable
verbs the Commander LLM can pick from. Dependencies are injected via
``ToolContext`` so this stays in the bridge layer (no import of api/).
"""
from __future__ import annotations

import time
import json
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any

from loguru import logger

from app.bridge.orchestrator import Orchestrator, RunRequest
from app.bridge.discovery_commander_tools import DiscoveryCommanderTools
from app.bridge.discovery_service import DiscoveryService
from app.bridge.discovery_types import DiscoveryRunSpec
from app.bridge.commander_code import execute_code_inspection, routing_repository_check
from app.bridge.commander_existing_sources import existing_retry, existing_user_sources
from app.harness.observability.events import write_event
from app.harness.schema.validator import validate_document
from app.harness.tools.config import load_tool_configs, tool_config
from app.harness.tools.registry import (
    ToolContext as HarnessToolContext,
    ToolResult as HarnessToolResult,
    ToolSpec as HarnessToolSpec,
    ToolPolicy as HarnessToolPolicy,
    get_registry as get_tool_registry,
)
from app.storage.artifact_store import SCHEMA_TO_AGENT, ArtifactStore
from app.storage.run_store import RunStore

if TYPE_CHECKING:
    from app.bridge.commander_session import CommanderSession

# agent -> (schema_id, stem)
_AGENT_TO_SCHEMA: dict[str, tuple[str, str]] = {
    agent: (sid, stem) for sid, (agent, stem) in SCHEMA_TO_AGENT.items()
}


@dataclass
class ToolContext:
    orchestrator: Orchestrator
    session: "CommanderSession"
    run_store: RunStore


ToolHandler = Callable[[dict[str, Any], ToolContext], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]   # JSON-schema-ish, shown to the LLM
    handler: ToolHandler
    mutation_level: str = "write"


_COMMANDER_CONTEXTS: dict[str, ToolContext] = {}
_REGISTRY_ADAPTERS_INSTALLED = False
_DISCOVERY_COMMANDER_TOOLS: DiscoveryCommanderTools | None = None


def configure_discovery_commander_tools(service: DiscoveryService | None) -> None:
    """Bind Commander verbs to the active app-local Discovery Service."""

    global _DISCOVERY_COMMANDER_TOOLS
    _DISCOVERY_COMMANDER_TOOLS = (
        DiscoveryCommanderTools(service) if service is not None else None
    )


# --------------------------------------------------------------------- tools


def existing_execution_job_for_route(
    args: dict[str, Any], routing: dict[str, Any],
) -> dict[str, Any] | None:
    """Normalize explicit no-code intent; source admission remains host-owned.

    This is a request to inspect a reusable delivery, never evidence that one
    exists or has been approved. Missing exact configuration reads retain the
    ordinary Coding path instead of adding another creation blocker.
    """
    raw = args.get("existing_execution_job")
    if raw is None:
        return None
    fields = {"config_path", "entrypoint", "max_iters", "budget_unit", "seed"}
    # This optional shortcut must not turn a valid Coding request into a
    # creation failure. In particular an unseeded source configuration cannot
    # supply a seed merely to satisfy a model-output shape requirement. Let
    # ordinary Coding inspect that entrypoint and preserve its actual defaults.
    if not isinstance(raw, dict) or set(raw) < fields:
        return None
    if set(raw) != fields:
        raise ValueError("existing_execution_job 必须只包含 config_path、entrypoint、max_iters、budget_unit、seed。")
    normalized: dict[str, Any] = {}
    for key in ("config_path", "entrypoint"):
        value = raw[key]
        if not isinstance(value, str) or not value or value != value.strip() or "\x00" in value:
            raise ValueError(f"existing_execution_job.{key} 必须是非空的代码仓相对文件路径。")
        name = value.replace("\\", "/")
        path = PurePosixPath(name)
        if (path.is_absolute() or PureWindowsPath(value).drive or ".." in path.parts
                or name in {".", ".."} or name.endswith("/")):
            raise ValueError(f"existing_execution_job.{key} 必须是代码仓内的相对文件路径，不能逃逸目录。")
        normalized[key] = path.as_posix()
    if not isinstance(raw["budget_unit"], str) or raw["budget_unit"] not in {"steps", "epochs"}:
        raise ValueError("existing_execution_job.budget_unit 必须为 steps 或 epochs。")
    if type(raw["max_iters"]) is not int or raw["max_iters"] < 1:
        raise ValueError("existing_execution_job.max_iters 必须是正整数，按本次用户预算填写。")
    if type(raw["seed"]) is not int or raw["seed"] < 0:
        raise ValueError("existing_execution_job.seed 必须是非负整数。")
    normalized.update({key: raw[key] for key in ("max_iters", "budget_unit", "seed")})
    if (args.get("entrypoint", "pipeline") != "coding" or args.get("standalone")
            or args.get("seed_artifact") or routing.get("ok") is not True
            or routing.get("repository_scope") != "required"):
        return None
    evidence = routing.get("evidence", [])
    if not isinstance(evidence, list) or not any(
        isinstance(row, dict) and row.get("path") == normalized["config_path"] for row in evidence
    ):
        return None
    return normalized


async def _create_and_start_run(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    created = await _create_run(args, ctx)
    if not created.get("ok"):
        return created
    return await _start_run({"run_id": created["run_id"]}, ctx)


async def _auto_research(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from app.harness.evolution import EvolutionContract, bind_explicit_metric_deltas
    contract = EvolutionContract.model_validate(args.get('evolution'))
    from app.bridge.evolution_service import bind_user_protocol
    user_texts = [row['content'] for row in _stage_limit_user_sources(ctx.session)]
    contract = bind_explicit_metric_deltas(contract, user_texts)
    protocol = bind_user_protocol(contract.protocol, user_texts)
    contract = contract.model_copy(update={'protocol': protocol})
    return await _create_and_start_run({**args, 'entrypoint': 'pipeline', 'standalone': False,
        'user_request': '\n\n'.join(user_texts) or str(args.get('user_request', '')),
        'evolution': contract.model_dump(mode='json')}, ctx)


async def _available_models(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from app.bridge.global_model_profiles import GlobalModelProfiles
    from app.harness.llm.model_registry import get_agent_config
    default = get_agent_config('idea')
    return {'ok': True, 'global_models': GlobalModelProfiles().public(),
        'default_idea_model': {'provider': default.model_provider, 'model': default.model_name},
        'instruction': 'Use actual configured profile IDs for multi-model research, or omit IDs for independent sessions of the default Idea model.'}


async def _create_run(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    routing = routing_repository_check(args, ctx.session)
    if not routing['ok']:
        return routing
    return await _create_run_with_routing(args, ctx, routing)


def _project_default_data_source(project: str) -> dict[str, Any] | None:
    """Resolve the project's actual selection once, before allocating the run."""
    from app.storage.data_source_store import DataSourceStore

    store = DataSourceStore()
    source_id = store.default_id(project)
    if not source_id:
        return None
    profile = store.default_profile(project)
    if profile is None:
        raise ValueError("项目默认数据档案已丢失，请重新选择数据；不能悄悄改用配置中的另一份数据。")
    if profile.get("id") != source_id or profile.get("project") != project:
        raise ValueError("默认数据身份或项目归属不一致，不能使用其他项目的数据。")
    stored_path = profile.get("stored_path")
    if not isinstance(stored_path, str) or not stored_path or not Path(stored_path).is_absolute():
        raise ValueError("默认数据缺少有效的绝对存储路径，请重新选择数据。")
    path = Path(stored_path).resolve(strict=True)
    if not path.is_file():
        raise ValueError("默认数据必须指向真实文件。")
    with path.open("rb"):
        pass  # Basic readability only; the real preflight binds content hashes.
    return {**profile, "stored_path": str(path)}


async def _create_run_with_routing(
    args: dict[str, Any], ctx: ToolContext, routing: dict[str, Any], *,
    additional_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Shared mutation after a real public reader or bounded host admission.

    This private boundary accepts host-created evidence, never a model's claim
    that inspection occurred. The ordinary creation tools still use the public
    current-turn reader check above.
    """
    entrypoint = str(args.get("entrypoint", "pipeline"))
    if entrypoint == 'execution':
        return {'ok': False, 'status': 'execution_delivery_required',
                'error': '新任务尚未绑定编码交付与 execution_jobs 运行清单，任务未创建。已有模型/训练 YAML 不等于系统可消费的执行交接。请从 coding 复用现有模型、只补必要运行交付；需设计比较协议才从 experiment 开始。已有任务的执行清单通过 run.recovery_status/run.recover 复用，不要重新研究模型。'}
    try:
        existing_job = existing_execution_job_for_route(args, routing)
    except ValueError as exc:
        return {"ok": False, "status": "existing_execution_job_invalid", "error": str(exc)}
    try:
        data_source = _project_default_data_source(ctx.session.project)
    except (OSError, ValueError) as exc:
        return {"ok": False, "status": "selected_data_source_invalid", "error": str(exc)}
    user_request = str(args.get("user_request", ""))
    seed_artifact = args.get("seed_artifact")
    task = str(args.get("task") or user_request[:60] or "commander_run")
    from app.bridge.commander_existing import existing_execution_stage_limit
    # Stage limits come from actual user messages, not the model's rewritten
    # task or optional declaration. Trusted specialised admission may already
    # have frozen this boundary; an absent ordinary source does not erase it.
    user_sources = _stage_limit_user_sources(ctx.session)
    stop_at_execution = existing_execution_stage_limit({}, [row['content'] for row in user_sources])

    request = RunRequest(
        task=task,
        project=ctx.session.project,
        entrypoint=entrypoint,  # type: ignore[arg-type]
        standalone=bool(args.get("standalone", False)),
        user_request=user_request,
        data_source=data_source,
        review_mode="commander" if ctx.session.auto_mode else "manual",
        extra={"routing": routing,
               **({'evolution': args['evolution']} if args.get('evolution') is not None else {}),
               **({"existing_execution_job": existing_job} if existing_job is not None else {}),
               **(additional_extra or {}),
               **({"stop_after": "execution"} if stop_at_execution else {}),
               **({"experiment_id": ctx.session.experiment_id} if ctx.session.experiment_id else {})},
    )
    rsession = ctx.orchestrator.create_session(request)
    run_id = rsession.run.run_id

    # Optional: seed the entrypoint agent's first artifact (skip its LLM draft).
    if seed_artifact and entrypoint in _AGENT_TO_SCHEMA:
        schema_id, _stem = _AGENT_TO_SCHEMA[entrypoint]
        result = validate_document(str(seed_artifact), expected_schema=schema_id)
        if not result.valid:
            return {
                "ok": False,
                "error": "seed_artifact failed schema validation",
                "schema": schema_id,
                "errors": [{"path": e.path, "message": e.message} for e in result.errors],
            }
        ArtifactStore(rsession.run).write(text=str(seed_artifact), expected_schema=schema_id)

    ctx.session.linked_run_id = run_id
    return {
        "ok": True,
        "run_id": run_id,
        "entrypoint": entrypoint,
        "auto_approve": False,
        "review_mode": request.review_mode,
        "states": {k: s.value for k, s in rsession.graph.all_states().items()},
    }


def _existing_retry(text: str) -> bool:
    return existing_retry(text)


def _existing_user_sources(session: CommanderSession) -> list[dict[str, str]]:
    return existing_user_sources(session)


def _stage_limit_user_sources(session: CommanderSession) -> list[dict[str, str]]:
    """Retain an unallocated pending user's boundary across pure continuations."""
    ordinary = existing_user_sources(session)
    messages = session.context_messages()
    users = [message for message in messages if message.role == 'user']
    if (not users or session.linked_run_id or users[-1].id != session.active_turn_id
            or not existing_retry(users[-1].content)):
        return ordinary
    state = session.completion_state
    previous = state.get('previous_goal')
    if (state.get('previous_status') != 'pending' or not isinstance(previous, dict)
            or previous.get('schema') != 'commander.turn_goal.v1'
            or previous.get('intent') != 'action' or previous.get('run_target') != 'new'
            or previous.get('project') != session.project):
        return ordinary
    index = next((i for i, user in enumerate(users) if user.id == previous.get('source_turn_id')), None)
    if (index is None or hashlib.sha256(users[index].content.encode()).hexdigest()
            != previous.get('source_user_sha256')
            or any(not existing_retry(user.content) for user in users[index + 1:])):
        return ordinary
    # A pending goal may itself be bound to a preceding "continue" turn.
    # Walk only its contiguous actual user chain, never model summaries.
    while index > 0 and existing_retry(users[index].content):
        index -= 1
    source = users[index]
    start = next(i for i, message in enumerate(messages) if message.id == source.id)
    if any(message.role == 'tool' and message.tool_name in {
            'run.create', 'create_and_start_run', 'run.existing_model'}
            and isinstance(message.tool_result, dict) and message.tool_result.get('run_id')
            for message in messages[start:]):
        return ordinary  # Even a failed start with an allocated run is not new admission.
    return [{'id': user.id, 'content': user.content} for user in users[index:]]


async def _run_existing_model(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from app.bridge.commander_existing import (
        ExistingIntentValidationError,
        existing_execution_stage_limit, materialize_existing_seed, resolve_existing_request,
        user_requests_additional_changes, validate_existing_user_intent,
    )
    from app.harness.persistence import atomic_write_json
    from app.harness.tools.config import load_execution_config
    from app.harness.tools.project_repo import load_project_repo

    source_messages = _existing_user_sources(ctx.session)
    if (ctx.session.linked_run_id and source_messages
            and _existing_retry(source_messages[-1]["content"])):
        return {"ok": False, "status": "existing_run_recovery_required", "run_id": ctx.session.linked_run_id,
                "error": "当前对话已有任务；继续或重试应核对并恢复原任务，不重复创建研究。"}
    try:
        resolved = resolve_existing_request(load_project_repo(ctx.session.project),
            load_execution_config()["execution"].get("local_commands", []), args,
            user_texts=[m["content"] for m in source_messages])
    except (OSError, UnicodeError, ValueError) as exc:
        return {"ok": False, "status": "existing_model_request_invalid", "error": str(exc)}
    if not resolved["ok"]:
        return resolved
    try:
        if not source_messages:
            raise ValueError("已有模型运行必须绑定实际用户消息。")
        validate_existing_user_intent(resolved, [m["content"] for m in source_messages])
        stop_after_execution = existing_execution_stage_limit(args, [m["content"] for m in source_messages])
    except ValueError as exc:
        fields = exc.structured_fields() if isinstance(exc, ExistingIntentValidationError) else {}
        return {"ok": False, "status": "user_intent_confirmation_required", "error": str(exc), **fields}
    if user_requests_additional_changes([m["content"] for m in source_messages]):
        resolved["ordinary_coding"] = True
    resolved = materialize_existing_seed(resolved)
    requested = resolved["requested"]
    resolved["stop_after_execution"] = stop_after_execution
    original = "\n\n".join(f'[用户消息 {m["id"]}]\n{m["content"]}' for m in source_messages)
    original += "\n\n[MARS 核实的当前配置、预算和种子]\n" + json.dumps(requested, ensure_ascii=False)
    if resolved["seed_source"] == "generated_for_run":
        original += f'\n当前配置及登记默认文件未固定seed；MARS为本次新任务生成并冻结seed={requested["seed"]}。'
    elif resolved["ordinary_coding_reason"]:
        original += "\n当前配置的种子默认值尚不能完整确认；由普通Coding按真实入口核对，不借用历史种子。"
    if resolved["parameter_overrides"]:
        original += "\n用户明确的参数调整（由普通Coding核对并实现，不改变其意义）：" + json.dumps(
            resolved["parameter_overrides"], ensure_ascii=False)
    routing = {"ok": True, "repository_scope": "required", "verification": "host_registered_current_source",
               "reason": "已有登记模型的运行请求；MARS读取当前配置及入口，直接从Coding准备执行。",
               "evidence": resolved["evidence"]}
    create_args: dict[str, Any] = {"entrypoint": "coding", "user_request": original,
                                  "task": f'existing_{Path(requested["config_path"]).stem}'}
    if not resolved["ordinary_coding"]:
        create_args["existing_execution_job"] = requested
    record = {"schema": "commander.existing_model_request.v1", "conversation_id": ctx.session.conv_id,
              "turn_id": ctx.session.active_turn_id, "source_messages": source_messages, **resolved}
    created = await _create_run_with_routing(create_args, ctx, routing,
        additional_extra={"existing_model_request": record,
                          **({"stop_after": "execution"} if stop_after_execution else {})})
    if not created.get("ok"):
        return created
    run = ctx.run_store.get(str(created["run_id"]))
    if run is None:
        raise ValueError("Created existing-model task is unavailable")
    atomic_write_json(run.root / "context/existing_model_request.json", record)
    run.write_event("agent_events", {"event": "commander.existing_model_admitted",
        "entrypoint": "coding", "configuration": requested["config_path"],
        "ordinary_coding": resolved["ordinary_coding"], "evidence": resolved["evidence"]})
    result = {**created, "status": "created", "requested": requested,
              "seed_source": resolved["seed_source"],
              "ordinary_coding": resolved["ordinary_coding"], "training_started": False,
              "stop_after_execution": stop_after_execution,
              "inspection_ref": "context/existing_model_request.json"}
    if args.get("start", True):
        started = await _start_run({"run_id": created["run_id"]}, ctx)
        if not started.get("ok"):
            return {**started, "run_id": created["run_id"], "entrypoint": "coding"}
        result.update({"status": "preparation_started", "states": started.get("states", {})})
    return result


async def _start_run(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    run_id = str(args.get("run_id") or ctx.session.linked_run_id or "")
    if not run_id:
        return {"ok": False, "error": "run_id required"}
    try:
        rsession = ctx.orchestrator.session(run_id)
    except KeyError:
        return {"ok": False, "error": f"run {run_id} not found"}
    ctx.session.linked_run_id = run_id
    started = ctx.orchestrator.start_owned_run(run_id)
    if not started["ok"]:
        return started
    return {
        "ok": True,
        "status": started["status"],
        "run_id": run_id,
        "entrypoint": rsession.request.extra.get('scope_entrypoint', rsession.request.entrypoint),
        "auto_approve": rsession.request.auto_approve,
        "review_mode": rsession.request.review_mode,
        "states": {k: s.value for k, s in rsession.graph.all_states().items()},
    }


async def _get_run_status(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    run_id = str(args.get("run_id") or ctx.session.linked_run_id or "")
    if not run_id:
        return {"ok": False, "error": "no run_id and no linked run"}
    try:
        rsession = ctx.orchestrator.session(run_id)
    except KeyError:
        return {"ok": False, "error": f"run {run_id} not found"}
    states = {k: s.value for k, s in rsession.graph.all_states().items()}
    waiting = [k for k, v in states.items() if v == "waiting_review"]
    from app.bridge.managed_review import review_state
    return {"ok": True, "run_id": run_id, "states": states, "waiting_review": waiting,
            "entrypoint": rsession.request.extra.get('scope_entrypoint', rsession.request.entrypoint),
            "initial_entrypoint": rsession.request.entrypoint, "task_request": rsession.request.user_request,
            "routing": rsession.request.extra.get('routing'),
            "routing_note": '运行状态不证明旧任务的目标或入口正确。用户纠正需求时须重新核对代码；空字面搜索不证明没有实现。',
            "review_mode": rsession.request.review_mode, "managed_review": review_state(rsession.run)}


async def _routing_check(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    result = routing_repository_check(args, ctx.session)
    turn = ctx.session.active_turn_id
    result['original_request'] = next((m.content for m in reversed(ctx.session.context_messages())
                                      if m.role == 'user' and m.turn_id == turn), '')
    result['attempted_queries'] = [m.tool_args.get('query') for m in ctx.session.context_messages()
        if m.turn_id == turn and m.tool_name == 'code.repo_search' and m.tool_args]
    return result


async def _correct_scope(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    routing = routing_repository_check(args, ctx.session)
    if not routing['ok']:
        return routing
    run_id = str(args.get('run_id') or ctx.session.linked_run_id or '')
    if not run_id:
        return {'ok': False, 'error': 'No linked run to correct.'}
    result = ctx.orchestrator.correct_initial_scope(run_id, project=ctx.session.project,
        entrypoint=str(args.get('entrypoint', '')), user_request=str(args.get('user_request', '')),
        reason=str(args.get('reason', '')), routing=routing)
    if result['ok'] and args.get('start', True) is True:
        return {**result, 'start': await _start_run({'run_id': run_id}, ctx)}
    return result


async def _recovery_status(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from app.bridge.run_recovery import recovery_status
    run_id = str(args.get('run_id') or ctx.session.linked_run_id or '')
    return {'ok': True, **recovery_status(ctx.orchestrator, run_id, project=ctx.session.project)}


async def _recover_run(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from app.bridge.run_recovery import recover_run
    run_id = str(args.get('run_id') or ctx.session.linked_run_id or '')
    result = await recover_run(ctx.orchestrator, run_id, project=ctx.session.project,
        action=str(args.get('action', '')), node=str(args.get('node', '')), token=str(args.get('token', '')))
    if result.get('ok'):
        ctx.session.linked_run_id = run_id
    return result


async def _feedback_loop(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    run_id = str(args.get("run_id") or ctx.session.linked_run_id or "")
    diagnosis_version = str(args.get("diagnosis_version") or args.get("version") or "v1")
    if not run_id:
        return {"ok": False, "error": "run_id required"}
    try:
        return await ctx.orchestrator.start_feedback_loop(
            run_id=run_id,
            diagnosis_version=diagnosis_version,
        )
    except KeyError:
        return {"ok": False, "error": f"run {run_id} not found"}


async def _get_diagnosis(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    run_id = str(args.get("run_id") or ctx.session.linked_run_id or "")
    run = ctx.run_store.get(run_id) if run_id else None
    if run is None:
        return {"ok": False, "error": f"run {run_id} not found"}
    from app.harness.schema.frontmatter_parser import parse as parse_fm

    versions = sorted(run.subdir("diagnosis").glob("diagnosis.v*.md"))
    if not versions:
        return {"ok": True, "run_id": run_id, "exists": False}
    meta = parse_fm(versions[-1].read_text(encoding="utf-8")).metadata
    return {
        "ok": True,
        "run_id": run_id,
        "exists": True,
        "passed": meta.get("passed"),
        "recommended_target": meta.get("recommended_target"),
        "attempt": meta.get("attempt"),
        "failed_metrics": meta.get("failed_metrics"),
        "suspected_causes": meta.get("suspected_causes"),
        "budget_status": meta.get("budget_status"),
    }


async def _read_artifact(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    run_id = str(args.get("run_id") or ctx.session.linked_run_id or "")
    agent = str(args.get("agent") or args.get("agent_dir") or "")
    version = str(args.get("version") or "approved")
    if not run_id:
        return {"ok": False, "error": "run_id required"}
    run = ctx.run_store.get(run_id)
    if run is None:
        return {"ok": False, "error": f"run {run_id} not found"}
    if not agent:
        return {"ok": False, "error": "agent required"}
    stem = str(args.get("stem") or "")
    if not stem:
        for _schema, (dir_name, candidate) in SCHEMA_TO_AGENT.items():
            if dir_name == agent:
                stem = candidate
                break
    if not stem:
        return {"ok": False, "error": f"unknown artifact stem for agent {agent}"}
    path = run.subdir(agent) / f"{stem}.{version}.md"
    if not path.exists():
        return {"ok": False, "error": f"artifact not found: {agent}/{stem}.{version}.md"}
    from app.harness.schema.frontmatter_parser import parse as parse_fm

    text = path.read_text(encoding="utf-8")
    parsed = parse_fm(text)
    return {
        "ok": True,
        "run_id": run_id,
        "agent": agent,
        "stem": stem,
        "version": version,
        "path": str(path),
        "metadata": parsed.metadata,
        "body": parsed.body,
    }


async def _review_artifact(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    action = str(args.get("action") or "approve")
    if action == "approve":
        return await _approve_node(args, ctx)
    if action == "reject":
        return await _reject_node(args, ctx)
    if action == "comment":
        run_id = str(args.get("run_id") or ctx.session.linked_run_id or "")
        agent = str(args.get("agent", ""))
        text = str(args.get("text") or args.get("comment") or "")
        if not (run_id and agent and text):
            return {"ok": False, "error": "run_id, agent, and text required"}
        from app.hitl.review_session import get_registry as get_review_registry

        review = get_review_registry().get(run_id, agent)
        if review is None:
            return {"ok": False, "error": f"no pending review for {agent} on {run_id}"}
        review.comment(text, actor="commander")
        return {"ok": True, "run_id": run_id, "agent": agent, "action": "commented"}
    return {"ok": False, "error": f"unknown review action '{action}'"}


async def _evaluate_metrics(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    run_id = str(args.get("run_id") or ctx.session.linked_run_id or "")
    run = ctx.run_store.get(run_id) if run_id else None
    if run is None:
        return {"ok": False, "error": f"run {run_id} not found"}
    path = run.subdir("execution") / "metrics.json"
    if not path.exists():
        return {"ok": True, "run_id": run_id, "exists": False, "passed": False}
    import json

    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"metrics.json invalid: {exc}"}
    targets = dict(ctx.session.metric_targets)
    targets_raw = args.get("targets", {})
    if isinstance(targets_raw, dict):
        for key, value in targets_raw.items():
            try:
                targets[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
    observed: dict[str, float] = {}
    if isinstance(rows, list):
        for key in targets:
            values: list[float] = []
            for row in rows:
                metrics = row.get("metrics", {}) if isinstance(row, dict) else {}
                if isinstance(metrics, dict) and key in metrics:
                    try:
                        values.append(float(metrics[key]))
                    except (TypeError, ValueError):
                        continue
            if values:
                observed[key] = sum(values) / len(values)
    comparisons = {
        key: {
            "target": target,
            "observed": observed.get(key),
            "passed": observed.get(key, float("-inf")) >= target,
        }
        for key, target in targets.items()
    }
    return {
        "ok": True,
        "run_id": run_id,
        "exists": True,
        "passed": all(item["passed"] for item in comparisons.values()) if comparisons else True,
        "comparisons": comparisons,
    }


async def _approve_node(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    run_id = str(args.get("run_id") or ctx.session.linked_run_id or "")
    agent = str(args.get("agent", ""))
    if not (run_id and agent):
        return {"ok": False, "error": "run_id and agent required"}
    if ctx.orchestrator.session(run_id).request.review_mode == "commander":
        return {"ok": False, "error": "托管模式由独立主控审核流程审批；需要人工接管时请在审核界面处理。"}
    from app.hitl.review_session import get_registry as get_review_registry

    review = get_review_registry().get(run_id, agent)
    if review is None:
        return {"ok": False, "error": f"no pending review for {agent} on {run_id}"}
    from app.hitl.approval import approve as approve_review
    if agent == "coding":
        from app.bridge.coding_approval import verify_written_code
        from app.bridge.research_branch import research_branch_scope
        with research_branch_scope(review.run, "coding"):
            if not verify_written_code(review.run, review.artifact_ref.path.read_text(encoding="utf-8")):
                return {"ok": False, "error": "真实代码核对未通过，请在审核界面处理。"}
    await approve_review(session=review, bus=ctx.orchestrator.bus, actor="commander")
    return {"ok": True, "run_id": run_id, "agent": agent, "action": "approved"}


async def _reject_node(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    run_id = str(args.get("run_id") or ctx.session.linked_run_id or "")
    agent = str(args.get("agent", ""))
    reason = str(args.get("reason", "rejected via Commander"))
    if not (run_id and agent):
        return {"ok": False, "error": "run_id and agent required"}
    from app.hitl.review_session import get_registry as get_review_registry

    review = get_review_registry().get(run_id, agent)
    if review is None:
        return {"ok": False, "error": f"no pending review for {agent} on {run_id}"}
    review.reject(reason=reason)
    return {"ok": True, "run_id": run_id, "agent": agent, "action": "rejected"}


async def _set_metric_targets(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    targets_raw = args.get("targets", {})
    targets: dict[str, float] = {}
    if isinstance(targets_raw, dict):
        for k, v in targets_raw.items():
            try:
                targets[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
    ctx.session.metric_targets.update(targets)
    return {"ok": True, "metric_targets": dict(ctx.session.metric_targets)}


async def _query_kb(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    zone = str(args.get("zone", "literature"))
    q = str(args.get("q", ""))
    top_k = int(args.get("top_k", 3) or 3)
    if not q:
        return {"ok": False, "error": "q required"}
    from app.harness.kb.retriever import query as kb_query

    try:
        hits = kb_query(query=q, zones=[zone], top_k=top_k)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "zone": zone,
        "hits": [
            {"score": round(h.score, 4), "excerpt": h.record.text[:240],
             "meta": h.record.metadata}
            for h in hits
        ],
    }


async def _list_runs(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    runs = ctx.run_store.list()
    return {
        "ok": True,
        "runs": [
            {"run_id": r.run_id, "task": r.task, "project": r.project,
             "entrypoint": r.entrypoint}
            for r in runs[-15:]
        ],
    }


async def _discovery_create(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    tools = _DISCOVERY_COMMANDER_TOOLS
    if tools is None:
        return {"ok": False, "error": "Discovery Service is not configured"}
    raw_spec = args.get("spec")
    if not isinstance(raw_spec, dict):
        return {"ok": False, "error": "spec object is required"}
    spec = DiscoveryRunSpec.model_validate(raw_spec)
    idempotency_key = str(args.get("idempotency_key") or "").strip()
    if not idempotency_key:
        return {"ok": False, "error": "idempotency_key is required"}
    payload = await tools.create(spec, idempotency_key=idempotency_key)
    ctx.session.linked_run_id = str(payload["run_id"])
    return {"ok": True, **payload}


def _discovery_run_id(args: dict[str, Any], ctx: ToolContext) -> str:
    return str(args.get("run_id") or ctx.session.linked_run_id or "").strip()


async def _discovery_start(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    tools = _DISCOVERY_COMMANDER_TOOLS
    run_id = _discovery_run_id(args, ctx)
    if tools is None or not run_id:
        return {"ok": False, "error": "Discovery Service and run_id are required"}
    payload = await tools.start(run_id, wait=bool(args.get("wait", False)))
    ctx.session.linked_run_id = run_id
    return {"ok": True, **payload}


async def _discovery_status(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    tools = _DISCOVERY_COMMANDER_TOOLS
    run_id = _discovery_run_id(args, ctx)
    if tools is None or not run_id:
        return {"ok": False, "error": "Discovery Service and run_id are required"}
    return {"ok": True, **tools.status(run_id)}


async def _discovery_pause(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    tools = _DISCOVERY_COMMANDER_TOOLS
    run_id = _discovery_run_id(args, ctx)
    if tools is None or not run_id:
        return {"ok": False, "error": "Discovery Service and run_id are required"}
    payload = await tools.pause(
        run_id,
        reason=str(args.get("reason") or "commander_requested"),
    )
    return {"ok": True, **payload}


async def _discovery_resume(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    tools = _DISCOVERY_COMMANDER_TOOLS
    run_id = _discovery_run_id(args, ctx)
    if tools is None or not run_id:
        return {"ok": False, "error": "Discovery Service and run_id are required"}
    payload = await tools.resume(run_id, wait=bool(args.get("wait", False)))
    return {"ok": True, **payload}


async def _discovery_stop(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    tools = _DISCOVERY_COMMANDER_TOOLS
    run_id = _discovery_run_id(args, ctx)
    if tools is None or not run_id:
        return {"ok": False, "error": "Discovery Service and run_id are required"}
    payload = await tools.stop(
        run_id,
        reason=str(args.get("reason") or "commander_requested"),
    )
    return {"ok": True, **payload}


async def _discovery_replay(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    tools = _DISCOVERY_COMMANDER_TOOLS
    run_id = _discovery_run_id(args, ctx)
    if tools is None or not run_id:
        return {"ok": False, "error": "Discovery Service and run_id are required"}
    return {"ok": True, **tools.replay(run_id)}


async def _read_context_material(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from app.bridge.commander_session import get_session_store
    from app.harness.tools.context_material import read_material_tool
    result = await read_material_tool(args, HarnessToolContext(ctx.session.conv_id, ctx.session.project, "commander",
        extra={"run_root": str(get_session_store().session_directory(ctx.session))}))
    return {"ok": result.ok, "output": result.output, "error": result.error}


async def _repo_list(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return await execute_code_inspection("code.repo_list", args, ctx)


async def _repo_search(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    return await execute_code_inspection("code.repo_search", args, ctx)


async def _repo_read(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    if set(args) - {"path", "start_line", "end_line"}:
        return {"ok": False, "error": "Code reads accept only path and start_line/end_line; use the connected project."}
    return await execute_code_inspection("code.repo_read_lines", args, ctx)


async def _research_memory(args: dict[str, Any], ctx: ToolContext, read: bool) -> dict[str, Any]:
    from app.harness.tools.knowledge import research_memory_tool, research_memory_read_tool
    fn = research_memory_read_tool if read else research_memory_tool
    result = await fn(args, HarnessToolContext(ctx.session.conv_id, ctx.session.project, "commander"))
    return {"ok": result.ok, "output": result.output, "error": result.error}


TOOLS: dict[str, ToolSpec] = {
    "knowledge.research_memory": ToolSpec(name="knowledge.research_memory",
        description="Search this project's baseline capability and experiment history for the current task. Returns provenance, missing conditions and strict protocol compatibility. Failed execution is not a rejected hypothesis.",
        parameters={"query": "current research task", "protocol": "optional exact dataset hash, split, preprocessing and evaluator object", "limit": "1 to 10, default 4"},
        handler=lambda args, ctx: _research_memory(args, ctx, False)),
    "knowledge.research_memory_read": ToolSpec(name="knowledge.research_memory_read",
        description="Read a retained research record including architecture, full config, training recipe, measured results and preserved evidence. Read-only; cannot change baseline or launch training.",
        parameters={"record_id": "mem_ record ID", "evidence_offset": "optional evidence page offset"},
        handler=lambda args, ctx: _research_memory(args, ctx, True)),

    'run.existing_model': ToolSpec(name='run.existing_model',
        description='Run an already implemented model: call this directly as the only action, without preliminary history lookup or repeated source-reading model turns. The host reads only current registered configurations/entrypoints and always creates Coding→Execution, skipping Idea/Experiment. It resolves a unique actual model/config or returns actual choices; never guesses. Budget steps means optimizer updates, epochs means complete epochs. Omit seed unless the user explicitly supplied it: the host reads declared config defaults or generates and freezes a new seed only under an explicit host policy; never borrow a historical seed. An existing but unregistered config returns execution_registration_required, not missing-model evidence. Explicit parameter overrides use ordinary Coding, preserving their meaning. Creation is not approval or training success. Successful preparation returns a factual terminal receipt; do not request another summary model turn.',
        parameters={'model_name': 'optional existing model name, exact or an unambiguous abbreviation',
                    'config_path': 'optional exact connected-repository relative configuration path; preferred when known',
                    'entrypoint': 'optional registered relative entrypoint, needed only if more than one matches',
                    'max_iters': 'required positive integer from current requested training budget',
                    'budget_unit': 'required steps|epochs; do not interpret repetition count as optimizer steps',
                    'seed': 'optional nonnegative integer; otherwise read actual config seed',
                    'parameter_overrides': 'optional explicit config-key/value object; unsupported by host shortcut, so ordinary Coding only',
                    'stop_after_execution': 'optional boolean, default false; true only when user explicitly requests stopping at results/no report; frozen before start',
                    'start': 'optional boolean, default true; false only if user requests preparation without starting'},
        handler=_run_existing_model),
    'routing.repository_check': ToolSpec(name='routing.repository_check',
        description='Verify current-turn source evidence before concluding that the existing run has the correct code-related route. Empty literal search is not evidence of absence. Read matching configuration/registration before concluding.',
        parameters={}, handler=_routing_check, mutation_level="read"),
    'run.correct_scope': ToolSpec(name='run.correct_scope',
        description='Correct an early misrouted existing task after the user corrects its scope. Preserve the same run ID, history and cumulative budget. Requires current code evidence. Refuses active/approved/executed/unreconciled work; cannot bypass it by creating another run.',
        parameters={'run_id':'optional linked run', 'entrypoint':'coding|experiment',
                    'user_request':'corrected task using verified existing implementation; no invented redesign',
                    'reason':'user correction and evidence', 'start':'boolean, default true'}, handler=_correct_scope),
    "code.repo_list": ToolSpec(name="code.repo_list",
        description="List the connected project's source paths, without reading contents. Browse relevant directories or filter filenames. Read-only; no shell. Paginate with next_offset; narrow the path if scan_limited.",
        parameters={"path": "relative directory, default .", "glob": "optional filename glob", "depth": "1 by default, at most 16", "offset": "optional page offset", "limit": "optional result count, at most 100"},
        handler=_repo_list, mutation_level="read"),
    "code.repo_search": ToolSpec(name="code.repo_search",
        description="Search literal text in a relative directory or exact text file (default '.'). If a permitted relative path is absent, MARS searches the connected repository root and reports requested_path/effective_path/warning; invalid, excluded, symbolic or unreadable paths remain errors. Returns bounded snippets with source SHA256.",
        parameters={"query": "literal text, not regex", "path": "relative directory or exact text file, default .", "glob": "optional filename glob", "depth": "optional depth, at most 16", "offset": "optional page offset", "limit": "optional result count, at most 100"},
        handler=_repo_search, mutation_level="read"),
    "code.repo_read_lines": ToolSpec(name="code.repo_read_lines",
        description="Read only a relevant source/config line range from the connected repository. Returns exact source, line numbers and SHA256. Maximum 160 lines/12000 characters per call. Use search results to pick ranges; no execution or modification.",
        parameters={"path": "exact project-relative file path", "start_line": "first line, 1-based", "end_line": "last line, inclusive; at most 160 lines"},
        handler=_repo_read, mutation_level="read"),
    "context.read_material": ToolSpec(name="context.read_material",
        description="Read original run-local context material. Follow next_offset for further pages.",
        parameters={"ref": "exact SHA256 ref from context", "char_offset": "optional character offset"},
        handler=_read_context_material, mutation_level="read"),
    "run.create": ToolSpec(
        name="run.create",
        description="Create without starting. Inspect task-relevant connected source in this turn before choosing entrypoint. For an explicit unchanged existing model/configuration with a supported entrypoint and resolved budget/seed, always pass coding plus existing_execution_job after reading the exact configuration. No history lookup is required: the host checks actual approved delivery and execution receipts plus fresh validation. Old memory summaries, failed short checks or reuse_allowed=false cannot reject this reuse request; they cannot certify it either. The host may generate a fresh validated delivery without Coding model calls; unavailable evidence falls back to ordinary Coding. This does not approve artifacts or start simulation. Do not use it for code changes or new methods. For unrelated tasks explain repository_scope=not_applicable in routing_reason.",
        parameters={
            "entrypoint": "one of pipeline|idea|experiment|coding|writing; new execution-only runs require coding delivery first",
            "repository_scope": "required (default) or not_applicable for tasks unrelated to code",
            "routing_reason": "why this is the earliest necessary stage, based on inspected code or non-code scope",
            "user_request": "natural-language task",
            "task": "optional short slug",
            "seed_artifact": "optional complete schema-valid markdown doc",
            "existing_execution_job": "optional no-code execution intent object: {config_path: exact inspected relative configuration path, entrypoint: existing relative training entry, max_iters: positive integer from current budget, budget_unit: steps|epochs, seed: nonnegative integer}; only with coding, never command_id/data/output paths or code modifications",
        },
        handler=_create_run,
    ),
    "run.start": ToolSpec(
        name="run.start",
        description="Start an existing run (defaults to the linked run).",
        parameters={"run_id": "optional; defaults to linked run"},
        handler=_start_run,
    ),
    "run.recovery_status": ToolSpec(
        name="run.recovery_status",
        description="Inspect checkpoint recovery and stage-retry options for the existing linked run. Read this before recovery; returns actions and a state token.",
        parameters={"run_id": "optional; defaults to linked run"},
        handler=_recovery_status,
        mutation_level="read",
    ),
    "run.recover": ToolSpec(
        name="run.recover",
        description="Explicitly recover the same run after the user asks to continue/retry. Prefer resume; otherwise use the offered retry action. Never creates a run or bypasses blocked/unknown outcomes. Do not automatically repeat this mutation.",
        parameters={"run_id": "optional; defaults to linked run", "action": "resume|retry from recovery_status",
                    "node": "node from recovery_status", "token": "exact token from recovery_status"},
        handler=_recover_run,
    ),
    "run.status": ToolSpec(
        name="run.status",
        description="Get the per-node states of a run.",
        parameters={"run_id": "optional; defaults to linked run"},
        handler=_get_run_status,
        mutation_level="read",
    ),
    "run.feedback_loop": ToolSpec(
        name="run.feedback_loop",
        description="Start the approved self-healing feedback loop from a diagnosis artifact.",
        parameters={"run_id": "optional", "diagnosis_version": "v1/v2/..."},
        handler=_feedback_loop,
    ),
    "artifact.read": ToolSpec(
        name="artifact.read",
        description="Read an artifact body and frontmatter.",
        parameters={"run_id": "optional", "agent": "stage name", "version": "default approved"},
        handler=_read_artifact,
        mutation_level="read",
    ),
    "artifact.review": ToolSpec(
        name="artifact.review",
        description="Approve, reject, or comment on a pending HITL artifact.",
        parameters={"run_id": "optional", "agent": "stage", "action": "approve|reject|comment"},
        handler=_review_artifact,
    ),
    "metrics.evaluate": ToolSpec(
        name="metrics.evaluate",
        description="Compare execution metrics.json against Commander metric targets.",
        parameters={"run_id": "optional", "targets": "optional metric -> numeric target"},
        handler=_evaluate_metrics,
        mutation_level="read",
    ),
    "diagnosis.failure_analysis": ToolSpec(
        name="diagnosis.failure_analysis",
        description="Read the latest self-healing diagnosis for a run.",
        parameters={"run_id": "optional; defaults to linked run"},
        handler=_get_diagnosis,
        mutation_level="read",
    ),
    "user.approval": ToolSpec(
        name="user.approval",
        description="Record a user's approve/reject decision for a waiting node.",
        parameters={"run_id": "optional", "agent": "stage", "action": "approve|reject"},
        handler=_review_artifact,
    ),
    "create_and_start_run": ToolSpec(
        name="create_and_start_run",
        description=(
            "Create and immediately start a pipeline/standalone run. For goal-driven continuous Auto Research, "
            "supply evolution with quantitative criteria, six-module code boundaries, matched baseline and budgets; "
            "use pipeline. The host repeats real simulations, checks objectives and constraints, and evolves "
            "within that frozen contract until replicated success or a declared stop. Use the "
            "task-relevant source read in this turn to choose the earliest necessary stage. "
            "For a NEW existing-model simulation use coding to bind execution delivery; "
            "for an explicit unchanged configuration and resolved budget/seed, always pass "
            "existing_execution_job after reading that exact configuration. No Commander history "
            "lookup is required. Old memory summaries, failed short checks or reuse_allowed=false "
            "neither reject nor certify reuse: the host checks actual receipts and fresh validation. "
            "This requests host-verified delivery reuse without Coding model calls. The host creates a fresh "
            "artifact for the current inputs and budget, retaining review and simulation confirmation; "
            "missing or changed evidence falls back to normal Coding. Do not request this for code "
            "changes or new methods, and never treat it as an imported approval or old result. "
            "Use recovery tools for existing execution runs. "
            "Running an existing model must not be expanded into designing a new one. "
            "`entrypoint` to SKIP agents the user doesn't need: 'pipeline' runs "
            "the full Idea→Experiment→Coding→Execution→Writing chain; or pick a "
            "single stage ('idea'|'experiment'|'coding'|'writing') to "
            "enter mid-chain (upstream stages are skipped). The entered stage's "
            "Agent drafts its artifact from `user_request` automatically. DO NOT "
            "pass seed_artifact unless the user gave a COMPLETE structured doc "
            "with YAML frontmatter — a natural-language idea/goal is NOT a "
            "seed_artifact; in that case just set entrypoint + user_request."
        ),
        parameters={
            "entrypoint": "one of pipeline|idea|experiment|coding|writing; new execution-only runs require coding delivery first",
            "user_request": "the research question / task description (natural language)",
            "task": "optional short slug for the run",
            "repository_scope": "required (default); not_applicable only for tasks unrelated to the connected code",
            "routing_reason": "evidence-backed reason for the selected stage; required when repository_scope=not_applicable",
            "seed_artifact": "RARELY used — only a complete schema-valid markdown doc",
            "existing_execution_job": "optional no-code execution intent object: {config_path: exact inspected relative configuration path, entrypoint: existing relative training entry, max_iters: positive integer from current budget, budget_unit: steps|epochs, seed: nonnegative integer}; only with coding, never command_id/data/output paths or code modifications",
            "evolution": 'Optional continuous Auto Research contract; prefer run.auto_research and read its full contract.',
        },
        handler=_create_and_start_run,
    ),
    "get_run_status": ToolSpec(
        name="get_run_status",
        description="Get the per-node states of a run (defaults to the linked run).",
        parameters={"run_id": "optional; defaults to the conversation's linked run"},
        handler=_get_run_status,
        mutation_level="read",
    ),
    "get_diagnosis": ToolSpec(
        name="get_diagnosis",
        description=(
            "Read the latest self-healing diagnosis for a run: whether metrics "
            "passed, which stage is suspected (the pull-back target), failed "
            "metrics, and budget status."
        ),
        parameters={"run_id": "optional; defaults to the linked run"},
        handler=_get_diagnosis,
        mutation_level="read",
    ),
    "approve_node": ToolSpec(
        name="approve_node",
        description="Approve a node that is waiting for HITL review, so the pipeline advances.",
        parameters={"run_id": "optional; defaults to linked run", "agent": "stage name"},
        handler=_approve_node,
    ),
    "reject_node": ToolSpec(
        name="reject_node",
        description="Reject a waiting node (marks it failed, halts the chain).",
        parameters={"run_id": "optional", "agent": "stage name", "reason": "why"},
        handler=_reject_node,
    ),
    "set_metric_targets": ToolSpec(
        name="set_metric_targets",
        description=(
            "Record the user's success criteria (the 'expectation' to drive the "
            "self-healing loop toward), e.g. {'RES': -42}. Stored on the session."
        ),
        parameters={"targets": "object mapping metric name -> numeric target"},
        handler=_set_metric_targets,
    ),
    "query_kb": ToolSpec(
        name="query_kb",
        description="Semantic search a KB zone (literature|methodology|code_assets|run_archive).",
        parameters={"zone": "zone name", "q": "query text", "top_k": "int, default 3"},
        handler=_query_kb,
    ),
    "list_runs": ToolSpec(
        name="list_runs",
        description="List recent runs (run_id, task, project, entrypoint).",
        parameters={},
        handler=_list_runs,
        mutation_level="read",
    ),
    "discovery.create": ToolSpec(
        name="discovery.create",
        description="Create an auditable model-discovery run from a Project Pack contract.",
        parameters={"spec": "DiscoveryRunSpec object", "idempotency_key": "required stable key"},
        handler=_discovery_create,
    ),
    "discovery.start": ToolSpec(
        name="discovery.start",
        description="Start a model-discovery run after adapter readiness succeeds.",
        parameters={"run_id": "optional linked run", "wait": "wait for completion"},
        handler=_discovery_start,
    ),
    "discovery.status": ToolSpec(
        name="discovery.status",
        description="Read lifecycle, budget, archive, and HITL state for Discovery.",
        parameters={"run_id": "optional linked run"},
        handler=_discovery_status,
        mutation_level="read",
    ),
    "discovery.pause": ToolSpec(
        name="discovery.pause",
        description="Pause a running Discovery loop at its durable checkpoint.",
        parameters={"run_id": "optional linked run", "reason": "audit reason"},
        handler=_discovery_pause,
    ),
    "discovery.resume": ToolSpec(
        name="discovery.resume",
        description="Resume Discovery or resolve its archive-level HITL review.",
        parameters={"run_id": "optional linked run", "wait": "wait for completion"},
        handler=_discovery_resume,
    ),
    "discovery.stop": ToolSpec(
        name="discovery.stop",
        description="Stop Discovery and persist its stop reason.",
        parameters={"run_id": "optional linked run", "reason": "audit reason"},
        handler=_discovery_stop,
    ),
    "discovery.replay": ToolSpec(
        name="discovery.replay",
        description="Replay candidates, evaluations, archive, budget, and events.",
        parameters={"run_id": "optional linked run"},
        handler=_discovery_replay,
        mutation_level="read",
    ),
}


def commander_tool_mutates(name: str) -> bool:
    """Use current configured policy, then declared metadata; unknown is write."""
    configured = load_tool_configs().get(name)
    if configured is not None:
        return configured.mutation_level != "read"
    spec = TOOLS.get(name)
    return spec is None or spec.mutation_level != "read"


async def execute_tool(name: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    spec = TOOLS.get(name)
    if spec is None:
        return {"ok": False, "error": f"unknown tool '{name}'"}
    started = time.perf_counter()
    _record_commander_tool_event(
        ctx=ctx,
        run_id=str(args.get("run_id") or ctx.session.linked_run_id or ""),
        kind="commander.tool_started",
        tool=name,
        args=args,
        result={},
    )
    try:
        if _is_registry_bridge_tool(name):
            result = await _execute_via_registry(name, args, ctx)
        else:
            result = await spec.handler(args, ctx)
    except Exception as exc:
        logger.exception("commander tool {} failed", name)
        result = {"ok": False, "error": str(exc)}
    duration_ms = int((time.perf_counter() - started) * 1000)
    result_run_id = str(result.get("run_id") or args.get("run_id") or ctx.session.linked_run_id or "")
    _record_commander_tool_event(
        ctx=ctx,
        run_id=result_run_id,
        kind="commander.tool_completed" if result.get("ok", False) else "commander.tool_failed",
        tool=name,
        args=args,
        result={**result, "duration_ms": duration_ms},
    )
    return result


async def _describe_tools(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    from app.bridge.commander_native import commander_tool_input_schema
    names = args.get("names")
    if not isinstance(names, list) or not 1 <= len(names) <= 8 or any(
            not isinstance(name, str) or name not in TOOLS for name in names):
        return {"ok": False, "error": "names must contain one to eight existing tool names", "retryable": False}
    configured = load_tool_configs()
    return {"ok": True, "contracts": {name: commander_tool_input_schema(name, configured) for name in names}}


TOOLS['run.auto_research'] = ToolSpec('run.auto_research',
    'Start end-to-end Auto Research from a quantitative goal and constraints. Read actual model/configuration '
    'before choosing six-module boundaries. Idea researches evidence, independently proposes and reviews candidates, '
    'ranks with Elo, hands off selected modules to Coding, runs real matched simulations, and evolves until '
    'verified attainment or an explicit budget/plateau stop. Read tools.describe for the contract; never invent user thresholds.',
    {'user_request': 'Original research objective and constraints', 'task': 'Short task slug',
     'evolution': 'Frozen goal, criteria, modules, baseline_names, candidate_pattern, protocol and iteration budgets'}, _auto_research)

TOOLS['models.list'] = ToolSpec('models.list', 'Read saved system-wide model names and IDs for multi-model research; no secrets are returned.',
    {}, _available_models, mutation_level='read')


TOOLS["tools.describe"] = ToolSpec("tools.describe", "Read full contracts for named tools; no operation is executed.",
    {"names": "array of exact tool names, at most 8"}, _describe_tools, mutation_level="read")


def tools_for_prompt() -> str:
    """Render the tool catalogue for the Commander's system prompt."""
    lines: list[str] = []
    from app.bridge.commander_native import _ALIASES
    for spec in TOOLS.values():
        if spec.name in _ALIASES:
            continue
        params = ", ".join(f"{k}: {v}" for k, v in spec.parameters.items()) or "(none)"
        lines.append(f"- {spec.name}({params})\n    {spec.description}")
    return "\n".join(lines)


def install_registry_adapters() -> None:
    global _REGISTRY_ADAPTERS_INSTALLED
    reg = get_tool_registry()
    bridge_tool_names = [name for name in TOOLS if _is_registry_bridge_tool(name)]
    if _REGISTRY_ADAPTERS_INSTALLED and all(reg.has(name) for name in bridge_tool_names):
        return
    for name in bridge_tool_names:
        spec = TOOLS[name]
        if reg.has(name):
            continue
        reg.register(
            name,
            _make_registry_adapter(name),
            spec=HarnessToolSpec(
                name=name,
                namespace=name.split(".", 1)[0],
                description=spec.description,
                bridge_only=True,
                policy=HarnessToolPolicy(mutation_level=spec.mutation_level, allowed_agents=('commander',)),
                permission_dependencies=('run.create', 'run.start') if name == 'run.auto_research' else (),
            ),
        )
    _REGISTRY_ADAPTERS_INSTALLED = True


def _is_registry_bridge_tool(name: str) -> bool:
    return (tool_config(name).bridge_only or name == 'run.auto_research') and name in TOOLS


async def _execute_via_registry(
    name: str,
    args: dict[str, Any],
    ctx: ToolContext,
) -> dict[str, Any]:
    install_registry_adapters()
    context_key = str(id(ctx))
    _COMMANDER_CONTEXTS[context_key] = ctx
    run_id = str(args.get("run_id") or ctx.session.linked_run_id or "")
    run_root = ""
    if run_id:
        run = ctx.run_store.get(run_id)
        if run is not None:
            run_root = str(run.root)
    try:
        result = await get_tool_registry().dispatch(
            name,
            args,
            HarnessToolContext(
                run_id=run_id,
                project=ctx.session.project,
                agent="commander",
                extra={
                    "commander_context_key": context_key,
                    "run_root": run_root,
                },
                session_id=ctx.session.conv_id,
            ),
        )
    finally:
        _COMMANDER_CONTEXTS.pop(context_key, None)
    return _harness_result_to_commander_payload(result)


def _make_registry_adapter(name: str) -> Callable[[dict[str, Any], HarnessToolContext], Awaitable[HarnessToolResult]]:
    async def _adapter(args: dict[str, Any], hctx: HarnessToolContext) -> HarnessToolResult:
        context_key = str(hctx.extra.get("commander_context_key", "") if hctx.extra else "")
        bridge_ctx = _COMMANDER_CONTEXTS.get(context_key)
        if bridge_ctx is None:
            return HarnessToolResult(ok=False, error="commander context is not available")
        payload = await TOOLS[name].handler(args, bridge_ctx)
        run_id = str(payload.get("run_id") or hctx.run_id or "")
        if run_id:
            hctx.run_id = run_id
            run = bridge_ctx.run_store.get(run_id)
            if run is not None:
                hctx.extra["run_root"] = str(run.root)
        ok = bool(payload.get("ok", False))
        return HarnessToolResult(
            ok=ok,
            output=payload,
            error=None if ok else str(payload.get("error") or "commander tool failed"),
            evidence_refs=[run_id] if run_id else [],
        )

    return _adapter


def _harness_result_to_commander_payload(result: HarnessToolResult) -> dict[str, Any]:
    if isinstance(result.output, dict):
        payload = dict(result.output)
    else:
        payload = {"ok": result.ok, "output": result.output}
    payload.setdefault("ok", result.ok)
    if result.error:
        payload.setdefault("error", result.error)
    payload["tool_status"] = result.status
    payload["tool_call_id"] = result.metadata.get("tool_call_id")
    payload["requires_approval"] = result.requires_approval
    payload["blocked_by_gate"] = result.blocked_by_gate
    return payload


def _record_commander_tool_event(
    *,
    ctx: ToolContext,
    run_id: str,
    kind: str,
    tool: str,
    args: dict[str, Any],
    result: dict[str, Any],
) -> None:
    if not run_id:
        return
    run = ctx.run_store.get(run_id)
    if run is None:
        return
    ok = bool(result.get("ok", True))
    write_event(
        run=run,
        stream="commander_tool_events",
        channel=f"run.{run.run_id}.tool",
        kind=kind,
        severity="info" if ok else "error",
        source={
            "component": "bridge.commander",
            "agent": "commander",
            "conversation_id": ctx.session.conv_id,
        },
        evidence=[run.run_id],
        payload={
            "tool": tool,
            "args": args,
            "result": result,
        },
    )
