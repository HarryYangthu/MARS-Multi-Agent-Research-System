"""Publication policy and real source/admission receipts; no model substitutes."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.bridge.commander import Commander, _parse_decision, _receipt_ends_action_batch
from app.bridge.commander_dispatch import (
    batch_created_identity, batch_start_is_bound,
    PREPARING_NOTICE, UNSTARTED_NOTICE, dispatch_failure_reason, existing_model_fallback_action, existing_run_recovery_guard,
    has_run_dispatch, retained_dispatch_failure_reply, retained_dispatch_run_id,
    pending_existing_admission, run_dispatch_receipt, stale_executing_without_run,
    truthful_unstarted_reply, unfulfilled_run_dispatch,
)
from app.bridge.commander_session import ChatMessage, CommanderSession
from app.bridge.commander_tools import ToolContext, execute_tool
from app.bridge.orchestrator import Orchestrator
from app.harness.project_workspace import open_folder
from app.harness.llm.model_registry import get_agent_config
from app.harness.runtime.conversation_state import ConversationState
from app.harness.runtime.event_bus import InProcessEventBus
from app.settings import reset_settings_cache
from app.storage.run_store import RunStore


@contextmanager
def _environment(values: dict[str, str]) -> Iterator[None]:
    saved = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    reset_settings_cache()
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        reset_settings_cache()


@contextmanager
def _project(tmp_path: Path) -> Iterator[tuple[ToolContext, Path]]:
    with _environment({"MARS_FOLDER_PROJECTS_REGISTRY": str(tmp_path / "registry.json"),
                       "MARS_EXECUTION_CONFIG_PATH": str(tmp_path / "execution.yaml"),
                       "MARS_EXECUTION_BACKEND": "local_command", "MARS_RUNTIME_MODE": "development"}):
        project = open_folder(str(tmp_path / "project"), create=True)
        source = project.root / "code/source"
        (source / "configs").mkdir(parents=True)
        (source / "configs/actual.yaml").write_text("model_name: ActualModel\nseed: 2026\n", encoding="utf-8")
        (source / "train.py").write_text("def train():\n    return None\n", encoding="utf-8")
        (project.metadata_root / "repo_link.yaml").write_text(yaml.safe_dump({
            "repo_path": str(source), "allowed_paths": ["configs/", "train.py"], "read_only": True}), encoding="utf-8")
        (tmp_path / "execution.yaml").write_text(yaml.safe_dump({"execution": {
            "backend": "local_command", "local_commands": [{"id": "actual-entry", "project_ids": [project.name],
                "entrypoints": ["train.py"], "config_paths": ["configs/actual.yaml"]}]}}), encoding="utf-8")
        store = RunStore(tmp_path / "runs", project_storage=True)
        session = CommanderSession(conv_id="actual-dispatch", project=project.name)
        user = session.add(ChatMessage(role="user", content="运行 MissingModel 1 epoch，先准备，不启动。"))
        session.active_turn_id = user.id
        user.turn_id = user.id
        yield ToolContext(Orchestrator(run_store=store, bus=InProcessEventBus()), session, store), source


def _observe(session: CommanderSession, tool: str, args: dict[str, Any], result: dict[str, Any]) -> None:
    session.add(ChatMessage(role="tool", content=json.dumps(result, ensure_ascii=False),
                           tool_name=tool, tool_args=args, tool_result=result))


@pytest.mark.asyncio
async def test_failed_new_creation_cannot_bind_start_to_a_retained_task(tmp_path: Path) -> None:
    with _project(tmp_path) as (ctx, _):
        read = await execute_tool('code.repo_read_lines', {'path': 'configs/actual.yaml'}, ctx)
        assert read['ok']
        _observe(ctx.session, 'code.repo_read_lines', {'path': 'configs/actual.yaml'}, read)
        old = await execute_tool('run.create', {'entrypoint': 'experiment', 'user_request': '准备方案'}, ctx)
        assert old['ok']
        retained = old['run_id']
        user = ctx.session.add(ChatMessage(role='user', content='另开一项学习率寻优'))
        user.turn_id = ctx.session.active_turn_id = user.id
        listed = await execute_tool('code.repo_list', {'path': '.'}, ctx)
        assert listed['ok']
        _observe(ctx.session, 'code.repo_list', {'path': '.'}, listed)
        failed = await execute_tool('run.create', {'entrypoint': 'experiment', 'user_request': user.content}, ctx)
        assert not failed['ok'] and failed['status'] == 'repository_inspection_required'
        bound = batch_created_identity(ctx.session, 'run.create', failed, initial_run_id=retained)
        assert bound is None
        assert not batch_start_is_bound(ctx.session, {}, created_run_id=bound)
        assert not batch_start_is_bound(ctx.session, {'run_id': retained}, created_run_id=bound)
        assert ctx.session.linked_run_id == retained and len(ctx.run_store.list()) == 1
        assert ctx.orchestrator.owned_tasks.active(retained) is None


@pytest.mark.asyncio
async def test_dependent_start_must_use_the_actual_new_allocation(tmp_path: Path) -> None:
    with _project(tmp_path) as (ctx, _):
        read = await execute_tool('code.repo_read_lines', {'path': 'configs/actual.yaml'}, ctx)
        assert read['ok']
        _observe(ctx.session, 'code.repo_read_lines', {'path': 'configs/actual.yaml'}, read)
        created = await execute_tool('run.create', {'entrypoint': 'experiment', 'user_request': '准备方案'}, ctx)
        assert created['ok']
        bound = batch_created_identity(ctx.session, 'run.create', created, initial_run_id=None)
        assert bound == created['run_id']
        assert bound is not None
        assert batch_start_is_bound(ctx.session, {}, created_run_id=bound)
        assert batch_start_is_bound(ctx.session, {'run_id': bound}, created_run_id=bound)
        assert not batch_start_is_bound(ctx.session, {'run_id': 'different-retained-task'}, created_run_id=bound)
        assert batch_created_identity(ctx.session, 'run.create', created, initial_run_id=bound) is None
        assert not ctx.orchestrator.owned_tasks.active(bound)


@pytest.mark.asyncio
async def test_failed_shortcut_really_reads_once_then_creates_only_from_actual_source(tmp_path: Path) -> None:
    with _project(tmp_path) as (ctx, source):
        configuration = source / "configs/unregistered.yaml"
        configuration.write_text("model_name: MissingModel\nseed: 2026\n", encoding="utf-8")
        args = {"config_path": "configs/unregistered.yaml", "model_name": "MissingModel",
                "max_iters": 1, "budget_unit": "epochs", "start": False}
        failed = await execute_tool("run.existing_model", args, ctx)
        assert failed.get("ok") is False and failed.get("status") == "execution_registration_required", failed
        _observe(ctx.session, "run.existing_model", args, failed)
        assert ctx.session.linked_run_id is None and not ctx.run_store.list()
        assert run_dispatch_receipt("run.existing_model", failed) is None
        assert unfulfilled_run_dispatch(ctx.session)

        fallback = existing_model_fallback_action(ctx.session)
        assert fallback == {"tool": "code.repo_read_lines", "args": {"path": "configs/unregistered.yaml"}}
        read = await execute_tool(fallback["tool"], fallback["args"], ctx)
        assert read["ok"] and read["output"]["sha256"] == hashlib.sha256(configuration.read_bytes()).hexdigest()
        _observe(ctx.session, fallback["tool"], fallback["args"], read)
        assert existing_model_fallback_action(ctx.session) is None
        repeated = await execute_tool("run.existing_model", args, ctx)
        assert repeated["ok"] is False
        _observe(ctx.session, "run.existing_model", args, repeated)
        assert existing_model_fallback_action(ctx.session) is None  # No second automatic read this turn.
        assert ctx.session.linked_run_id is None and not ctx.run_store.list()
        assert truthful_unstarted_reply("改走普通核对路径：先读取配置，再创建任务。") == UNSTARTED_NOTICE

        # Ordinary Coding is admitted only after real reads, without running a
        # model, preflight, training process, or an automatic approval.
        for path in ("train.py",):
            actual = await execute_tool("code.repo_read_lines", {"path": path}, ctx)
            assert actual["ok"], actual
            assert actual["output"]["sha256"] == hashlib.sha256((source / path).read_bytes()).hexdigest()
            _observe(ctx.session, "code.repo_read_lines", {"path": path}, actual)
        created = await execute_tool("run.create", {"entrypoint": "coding", "user_request":
            "核对现有模型及实际配置，仅准备 1 epoch 的运行交付；不启动训练。"}, ctx)
        assert created["ok"] and created["run_id"] == ctx.session.linked_run_id
        _observe(ctx.session, "run.create", {}, created)
        receipt = run_dispatch_receipt("run.create", created)
        assert receipt is not None and not receipt.executing
        assert "已创建" in receipt.reply and "尚未启动" in receipt.reply
        assert existing_model_fallback_action(ctx.session) is None
        assert not unfulfilled_run_dispatch(ctx.session)
        run = ctx.run_store.get(str(created["run_id"]))
        assert run is not None and not list(run.subdir("execution").iterdir())
        assert created["states"]["coding"] == "pending"


@pytest.mark.asyncio
async def test_no_config_fallback_lists_real_repository_without_fabricating_source_proof(tmp_path: Path) -> None:
    with _project(tmp_path) as (ctx, _source):
        args = {"model_name": "MissingModel", "max_iters": 1, "budget_unit": "epochs", "start": False}
        failed = await execute_tool("run.existing_model", args, ctx)
        assert failed["ok"] is False and failed["status"] == "model_not_resolved"
        _observe(ctx.session, "run.existing_model", args, failed)
        fallback = existing_model_fallback_action(ctx.session)
        assert fallback == {"tool": "code.repo_list", "args": {"path": ".", "depth": 1}}
        listed = await execute_tool(fallback["tool"], fallback["args"], ctx)
        assert listed["ok"] and {row["path"] for row in listed["output"]["entries"]} == {"configs", "train.py"}
        _observe(ctx.session, fallback["tool"], fallback["args"], listed)
        assert existing_model_fallback_action(ctx.session) is None
        # A directory listing cannot stand in for a source-content read.
        denied = await execute_tool("run.create", {"entrypoint": "coding", "user_request": "Run one epoch"}, ctx)
        assert denied["ok"] is False and denied["status"] == "repository_inspection_required"
        assert ctx.session.linked_run_id is None


@pytest.mark.parametrize("status", ["model_not_resolved", "capability_unavailable", "execution_registration_required"])
@pytest.mark.parametrize("path", [None, "../outside.yaml", "C:\\outside.yaml", "/outside.yaml", "secrets.txt"])
def test_pure_fallback_policy_never_synthesizes_an_unsafe_read(status: str, path: str | None) -> None:
    # Authored inputs to a pure policy, not substitute tool execution receipts.
    session = CommanderSession(conv_id="policy", project="example", active_turn_id="one")
    _observe(session, "run.existing_model", {"config_path": path}, {"ok": False, "status": status})
    assert existing_model_fallback_action(session) == {"tool": "code.repo_list", "args": {"path": ".", "depth": 1}}


@pytest.mark.parametrize("status", ["model_selection_required", "entrypoint_selection_required", "user_intent_confirmation_required"])
def test_pure_candidate_or_parameter_clarification_is_not_replaced_by_a_read(status: str) -> None:
    session = CommanderSession(conv_id="policy", project="example", active_turn_id="one")
    _observe(session, "run.existing_model", {}, {"ok": False, "status": status})
    assert existing_model_fallback_action(session) is None
    question = "存在两个实际配置，请选择需要运行的一个。"
    assert question in truthful_unstarted_reply(question)


def test_pure_policy_keeps_plain_questions_and_does_not_use_previous_turn_failure() -> None:
    session = CommanderSession(conv_id="policy", project="example", active_turn_id="old")
    _observe(session, "run.existing_model", {}, {"ok": False, "status": "model_not_resolved"})
    session.active_turn_id = "new-question"
    assert existing_model_fallback_action(session) is None
    assert not unfulfilled_run_dispatch(session)
    question = _parse_decision('{"reply":"epoch 表示完整遍历训练数据一次。","actions":[]}')
    assert not has_run_dispatch(question.actions) and question.reply.startswith("epoch")


def test_authored_start_claim_is_not_a_success_receipt_or_preparation_notice() -> None:
    decision = _parse_decision('{"reply":"已为你启动基线仿真。","next_state":"executing",'
                               '"actions":[{"tool":"run.existing_model","args":{"max_iters":1,"budget_unit":"epochs"}}]}')
    assert has_run_dispatch(decision.actions)
    assert "已启动" not in PREPARING_NOTICE and "已创建" not in PREPARING_NOTICE
    assert truthful_unstarted_reply(decision.reply) == UNSTARTED_NOTICE
    for tool in ("run.existing_model", "run.create", "run.start", "create_and_start_run"):
        assert run_dispatch_receipt(tool, {"ok": False, "status": "not_startable", "run_id": "retained"}) is None
        assert run_dispatch_receipt(tool, {"ok": True}) is None


def test_specialized_creation_ends_a_multi_action_batch_but_ordinary_create_start_remains_supported() -> None:
    # Pure scheduling policy only; authored decisions are not model/tool runs.
    extra_creation = _parse_decision('{"actions":[{"tool":"run.existing_model"},'
                                     '{"tool":"run.existing_model"}]}')
    assert _receipt_ends_action_batch(extra_creation.actions[0]["tool"], len(extra_creation.actions))
    ordinary = _parse_decision('{"actions":[{"tool":"run.create"},{"tool":"run.start"}]}')
    assert not _receipt_ends_action_batch(ordinary.actions[0]["tool"], len(ordinary.actions))
    assert _receipt_ends_action_batch("run.start", 1)


@pytest.mark.parametrize("tool", ["run.start", "create_and_start_run"])
def test_pure_start_receipt_requires_actual_start_status_and_does_not_claim_training_success(tool: str) -> None:
    # This verifies formatting/validation only, not a successful process start.
    assert run_dispatch_receipt(tool, {"ok": True, "run_id": "r", "status": "created"}) is None
    result = run_dispatch_receipt(tool, {"ok": True, "run_id": "r", "status": "started"})
    assert result is not None and result.executing
    assert "流程" in result.reply and "训练进度" in result.reply and "训练已完成" not in result.reply


@pytest.mark.parametrize("processing,linked,retained", [(True, None, False), (False, "linked", False), (False, None, True)])
def test_stale_executing_reset_never_touches_active_or_retained_tasks(
    processing: bool, linked: str | None, retained: bool,
) -> None:
    session = CommanderSession(conv_id="policy", project="example", active_turn_id="old",
        state=ConversationState.EXECUTING, processing=processing, linked_run_id=linked)
    result: dict[str, Any] = {"ok": False, "status": "model_not_resolved"}
    if retained:
        result["run_id"] = "retained-created-task"
    _observe(session, "run.existing_model", {}, result)
    assert not stale_executing_without_run(session)


def test_stale_executing_reset_requires_real_failed_admission_and_preserves_history() -> None:
    session = CommanderSession(conv_id="policy", project="example", active_turn_id="old", state=ConversationState.EXECUTING)
    assert not stale_executing_without_run(session)
    _observe(session, "run.existing_model", {}, {"ok": False, "status": "model_not_resolved"})
    before = [message.to_dict() for message in session.messages]
    assert stale_executing_without_run(session)
    assert [message.to_dict() for message in session.messages] == before
    session.state = ConversationState.IDLE
    assert not stale_executing_without_run(session)


def test_stale_executing_reset_does_not_override_a_historical_successful_run() -> None:
    session = CommanderSession(conv_id="policy", project="example", active_turn_id="old", state=ConversationState.EXECUTING)
    _observe(session, "run.start", {}, {"ok": True, "status": "started", "run_id": "historical"})
    _observe(session, "run.existing_model", {}, {"ok": False, "status": "model_not_resolved"})
    assert not stale_executing_without_run(session)


def test_retained_run_after_failed_start_prevents_new_admission() -> None:
    session = CommanderSession(conv_id="policy", project="example", active_turn_id="one")
    _observe(session, "run.existing_model", {}, {"ok": False, "status": "not_startable", "run_id": "retained"})
    assert existing_model_fallback_action(session) is None
    assert unfulfilled_run_dispatch(session)
    assert retained_dispatch_run_id(session) == "retained"
    reply = truthful_unstarted_reply("已为你启动。", retained_run_id=retained_dispatch_run_id(session))
    assert "已有任务 retained 已保留" in reply and "已为你启动" not in reply
    assert run_dispatch_receipt("run.existing_model", session.messages[-1].tool_result or {}) is None
    assert retained_dispatch_failure_reply("run.existing_model", session.messages[-1].tool_result or {}) == reply
    assert retained_dispatch_failure_reply("run.existing_model", {"ok": False, "status": "model_not_resolved"}) is None


def test_recovery_redirect_is_not_a_failed_start_and_does_not_rewrite_later_recovery() -> None:
    # Authored inputs test policy only, not a claimed successful recovery.
    session = CommanderSession(conv_id="policy", project="example", active_turn_id="one", linked_run_id="existing")
    redirect = {"ok": False, "status": "existing_run_recovery_required", "run_id": "existing"}
    _observe(session, "run.existing_model", {}, redirect)
    assert retained_dispatch_failure_reply("run.existing_model", redirect) is None
    assert not unfulfilled_run_dispatch(session)
    assert existing_run_recovery_guard(session, [{"tool": "run.create", "args": {}}]) == {
        "tool": "run.recovery_status", "args": {"run_id": "existing"}}
    assert existing_run_recovery_guard(session, [{"tool": "run.recover", "args": {"run_id": "existing"}}]) is None
    _observe(session, "run.recover", {}, {"ok": True, "status": "resuming", "run_id": "existing"})
    assert not unfulfilled_run_dispatch(session)
    session.active_turn_id = "a-new-explicit-request"
    assert existing_run_recovery_guard(session, [{"tool": "run.create", "args": {}}]) is None


@pytest.mark.asyncio
async def test_actual_invalid_path_remains_invalid_and_reports_its_real_error(tmp_path: Path) -> None:
    with _project(tmp_path) as (ctx, _source):
        args = {"config_path": "configs/missing.yaml", "max_iters": 1, "budget_unit": "epochs", "start": False}
        result = await execute_tool("run.existing_model", args, ctx)
        assert result["ok"] is False and result["status"] == "existing_model_request_invalid"
        _observe(ctx.session, "run.existing_model", args, result)
        assert existing_model_fallback_action(ctx.session) is None
        reply = truthful_unstarted_reply("先读取配置，再创建任务。", failure_reason=dispatch_failure_reason(ctx.session))
        assert result["error"] in reply and "先读取" not in reply
        assert ctx.session.linked_run_id is None and not ctx.run_store.list()


def _continue(session: CommanderSession) -> ChatMessage:
    user = ChatMessage(role="user", content="继续")
    session.active_turn_id = user.id
    return session.add(user)


@pytest.mark.asyncio
async def test_again_continue_uses_previous_actual_read_then_revalidates_and_creates_once(
    tmp_path: Path,
) -> None:
    with _project(tmp_path) as (ctx, source):
        config = source / "configs/unregistered.yaml"
        config.write_text("model_name: MissingModel\nseed: 2026\n", encoding="utf-8")
        original_user = next(message for message in ctx.session.messages if message.role == "user")
        args = {"config_path": "configs/unregistered.yaml", "model_name": "MissingModel",
                "max_iters": 1, "budget_unit": "epochs", "start": False}
        failed = await execute_tool("run.existing_model", args, ctx)
        assert failed["status"] == "execution_registration_required" and failed["ok"] is False
        _observe(ctx.session, "run.existing_model", args, failed)
        first_continue = _continue(ctx.session)
        read = await execute_tool("code.repo_read_lines", {"path": args["config_path"]}, ctx)
        assert read["ok"]
        old_sha = read["output"]["sha256"]
        _observe(ctx.session, "code.repo_read_lines", {"path": args["config_path"]}, read)
        ctx.session.add(ChatMessage(role="assistant", content="配置已核实，现在按普通 Coding→Execution 路径创建任务。"))

        # The retained incident also includes a generic creation tool denied
        # before allocation because this turn has no source reads.
        generic_continue = _continue(ctx.session)
        generic_args = {"entrypoint": "coding", "user_request": "Run the existing configuration for 1 epoch.",
                        "existing_execution_job": {"config_path": args["config_path"], "entrypoint": "train.py",
                                                   "max_iters": 1, "budget_unit": "epochs"}}
        generic_failure = await execute_tool("create_and_start_run", generic_args, ctx)
        assert generic_failure["ok"] is False and generic_failure["status"] == "repository_inspection_required"
        _observe(ctx.session, "create_and_start_run", generic_args, generic_failure)

        # The next user's single continuation must not need another request.
        # Registration and config really change here, rather than substituting
        # a provider/tool result. Old reads identify the selected file only.
        config.write_text("model_name: MissingModel\nseed: 2027\n", encoding="utf-8")
        profile_path = tmp_path / "execution.yaml"
        profile = yaml.safe_load(profile_path.read_text())
        profile["execution"]["local_commands"][0]["config_paths"].append(args["config_path"])
        profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")
        action = pending_existing_admission(ctx.session)
        assert action == {"tool": "run.existing_model", "args": args}
        assert action is not None
        assert "seed" not in action["args"]
        # A real unavailable endpoint is not a provider substitute: any accidental
        # model dispatch makes this fail through its actual network connection.
        # The host restores the prior request before a model is needed.
        config_provider = replace(get_agent_config("commander"), model_provider="local_vllm",
            base_url="http://127.0.0.1:1/v1", base_url_env="", api_key_env="", raw={"loop": {"max_tool_steps": 1}})
        commander = Commander(orchestrator=ctx.orchestrator, run_store=ctx.run_store, agent_config=config_provider)
        try:
            emitted = await commander.handle_user_message(ctx.session, "继续")
        finally:
            await commander._provider.close()
        second_continue = next(message for message in reversed(ctx.session.messages) if message.role == "user")
        created = next(message.tool_result for message in emitted if message.tool_name == "run.existing_model")
        assert created is not None
        assert created["ok"] and created["status"] == "created", created
        assert not any(activity.kind == "model" for activity in ctx.session.activities)
        assert sum(message.tool_name == "run.existing_model" for message in emitted) == 1
        run = ctx.run_store.get(str(created["run_id"]))
        assert run is not None and len(ctx.run_store.list()) == 1
        record = json.loads((run.root / "context/existing_model_request.json").read_text())
        assert record["requested"]["seed"] == 2027  # Current real file, not the old read.
        assert record["requested"]["max_iters"] == 1 and record["requested"]["budget_unit"] == "epochs"
        assert record["source_messages"] == [{"id": message.id, "content": message.content}
                                             for message in (original_user, first_continue, generic_continue, second_continue)]
        current_read = next(row for row in record["evidence"] if row["path"] == args["config_path"])
        assert current_read["sha256"] == hashlib.sha256(config.read_bytes()).hexdigest() != old_sha
        assert pending_existing_admission(ctx.session) is None
        assert not list(run.subdir("execution").iterdir()) and created["states"]["coding"] == "pending"


@pytest.mark.asyncio
async def test_pending_continue_without_prior_read_revalidates_original_args_once(tmp_path: Path) -> None:
    with _project(tmp_path) as (ctx, source):
        config = source / "configs/unregistered.yaml"
        config.write_text("model_name: MissingModel\nseed: 2026\n", encoding="utf-8")
        args = {"config_path": "configs/unregistered.yaml", "model_name": "MissingModel",
                "max_iters": 1, "budget_unit": "epochs", "start": False}
        failed = await execute_tool("run.existing_model", args, ctx)
        assert failed["status"] == "execution_registration_required"
        _observe(ctx.session, "run.existing_model", args, failed)
        _continue(ctx.session)
        admission = pending_existing_admission(ctx.session)
        assert admission == {"tool": "run.existing_model", "args": args}
        assert admission is not None
        # A second actual failure consumes the one re-admission allowance.
        repeated = await execute_tool(admission["tool"], admission["args"], ctx)
        assert repeated["ok"] is False
        _observe(ctx.session, admission["tool"], admission["args"], repeated)
        assert pending_existing_admission(ctx.session) is None
        assert not ctx.run_store.list() and ctx.session.linked_run_id is None


def test_first_turn_pending_admission_does_not_infer_missing_fields_or_a_new_goal() -> None:
    session = CommanderSession(conv_id="policy", project="example", active_turn_id="original")
    original = ChatMessage(role="user", content="Run the current model for one epoch", id="original")
    session.add(original)
    args = {"config_path": "configs/current.yaml", "budget_unit": "epochs"}
    _observe(session, "run.existing_model", args, {"ok": False, "status": "capability_unavailable"})
    _continue(session)
    assert pending_existing_admission(session) is None  # No inferred count or seed.
    new_goal = ChatMessage(role="user", content="运行另一个模型 2 epochs")
    session.active_turn_id = new_goal.id
    session.add(new_goal)
    assert pending_existing_admission(session) is None
