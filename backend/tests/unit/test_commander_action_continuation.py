"""Continue unfinished actions using real file observations, not model doubles.

Policy inputs below are authored responses or the retained incident's exact
text. No test substitutes a provider, `_decide`, or successful tool execution.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

import pytest
import yaml

from app.bridge.commander import _decision_from_response
from app.bridge.commander_dispatch import (
    action_continuation, action_continuation_exhausted_reply,
    ordinary_coding_handoff_actions, promised_source_read_action,
)
from app.bridge.commander_session import ChatMessage, CommanderSession
from app.bridge.commander_tools import ToolContext, execute_tool
from app.bridge.orchestrator import Orchestrator
from app.harness.project_workspace import open_folder
from app.harness.runtime.conversation_state import ConversationState
from app.harness.runtime.event_bus import InProcessEventBus
from app.settings import reset_settings_cache
from app.storage.run_store import RunStore


ORIGINAL_REQUEST = (
    "将现有7层 StaticPIMC 的第二层卷积抽头数增加 2，学习率减半，固定 seed=2026，"
    "训练 2个 epoch，其余配置不变。 和改动前进行对比"
)
INCIDENT_PROMISE = "MARS 正在核对 7 层配置的具体字段（层数、抽头、学习率、seed），先读取两个 7 层配置文件。"


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


def _user(session: CommanderSession, text: str) -> ChatMessage:
    message = ChatMessage(role="user", content=text)
    session.active_turn_id = message.id
    session.add(message)
    return message


def _observe(session: CommanderSession, tool: str, args: dict[str, Any], result: dict[str, Any]) -> None:
    session.add(ChatMessage(role="tool", content=json.dumps(result, ensure_ascii=False),
                           tool_name=tool, tool_args=args, tool_result=result))


@contextmanager
def _project(tmp_path: Path, request: str = ORIGINAL_REQUEST) -> Iterator[tuple[ToolContext, Path]]:
    with _environment({"MARS_FOLDER_PROJECTS_REGISTRY": str(tmp_path / "registry.json"),
        "MARS_EXECUTION_CONFIG_PATH": str(tmp_path / "execution.yaml"),
        "MARS_EXECUTION_BACKEND": "local_command", "MARS_RUNTIME_MODE": "development"}):
        project = open_folder(str(tmp_path / "project"), create=True)
        source = project.root / "code/current"
        (source / "configs").mkdir(parents=True)
        for name, model, kernel in (("seven_a", "First7L", 3), ("seven_b", "Second7L", 31)):
            (source / f"configs/{name}.yaml").write_text(yaml.safe_dump({
                "model_name": model, "model": {"num_layers": 7, "inner_kernel": kernel},
                "lr_init": 0.0004, "seed": 2026}), encoding="utf-8")
        (source / "module.py").write_text("class StaticPIMC:\n    num_layers = 7\n", encoding="utf-8")
        (source / "train.py").write_text("def declared_entrypoint():\n    return None\n", encoding="utf-8")
        (project.metadata_root / "repo_link.yaml").write_text(yaml.safe_dump({
            "repo_path": str(source), "allowed_paths": ["configs/", "module.py", "train.py"], "read_only": True}))
        (tmp_path / "execution.yaml").write_text(yaml.safe_dump({"execution": {
            "backend": "local_command", "local_commands": [{"id": "actual-entry", "project_ids": [project.name],
                "entrypoints": ["train.py"], "config_paths": ["configs/seven_a.yaml", "configs/seven_b.yaml"]}]}}))
        store = RunStore(tmp_path / "runs", project_storage=True)
        session = CommanderSession(conv_id="action-continuation", project=project.name, state=ConversationState.PLANNING)
        _user(session, request)
        yield ToolContext(Orchestrator(run_store=store, bus=InProcessEventBus()), session, store), source


async def _inspect(ctx: ToolContext, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    actual = await execute_tool(tool, args, ctx)
    assert actual["ok"], actual
    _observe(ctx.session, tool, args, actual)
    return actual


async def _incident_inspections(ctx: ToolContext) -> None:
    await _inspect(ctx, "code.repo_list", {"path": "configs", "depth": 1})
    for query in ("num_layers", "StaticPIMC"):
        found = await _inspect(ctx, "code.repo_search", {"query": query})
        assert found["output"]["matches"], found


@pytest.mark.asyncio
async def test_original_change_request_real_list_search_then_plain_promise_requires_continuation(tmp_path: Path) -> None:
    with _project(tmp_path) as (ctx, source):
        await _incident_inspections(ctx)
        decision = _decision_from_response(INCIDENT_PROMISE)
        assert decision.response_mode == "plain_reply" and not decision.actions
        before = json.dumps([message.to_dict() for message in ctx.session.messages], sort_keys=True)
        files_before = {path: path.read_bytes() for path in source.rglob("*") if path.is_file()}
        directive = action_continuation(ctx.session, decision.reply, response_mode=decision.response_mode)
        assert directive is not None
        assert set(asdict(directive)) == {"reason", "fingerprint", "prompt"}
        assert directive.reason and directive.fingerprint and directive.prompt
        assert json.dumps([message.to_dict() for message in ctx.session.messages], sort_keys=True) == before
        assert {path: path.read_bytes() for path in files_before} == files_before
        assert not ctx.run_store.list() and ctx.session.linked_run_id is None


@pytest.mark.asyncio
async def test_real_content_read_advances_fingerprint_but_creating_once_ends_continuation(tmp_path: Path) -> None:
    with _project(tmp_path) as (ctx, source):
        await _incident_inspections(ctx)
        before_read = action_continuation(ctx.session, INCIDENT_PROMISE, response_mode="plain_reply")
        assert before_read is not None
        for path in ("configs/seven_a.yaml", "configs/seven_b.yaml", "train.py"):
            read = await _inspect(ctx, "code.repo_read_lines", {"path": path})
            assert read["output"]["sha256"] == hashlib.sha256((source / path).read_bytes()).hexdigest()
        after_read = action_continuation(ctx.session, "配置已核对，接下来创建改动对照任务。", response_mode="plain_reply")
        assert after_read is not None and after_read.fingerprint != before_read.fingerprint

        # This is a real workflow allocation, deliberately not started. It
        # proves the policy stops after a genuine side effect and does not
        # manufacture another create/start action or claim scientific output.
        args = {"entrypoint": "coding", "task": "change_comparison_preparation", "user_request": ORIGINAL_REQUEST}
        created = await execute_tool("run.create", args, ctx)
        assert created["ok"] and created["run_id"] == ctx.session.linked_run_id
        _observe(ctx.session, "run.create", args, created)
        assert action_continuation(ctx.session, "接下来启动任务。", response_mode="plain_reply") is None
        assert len(ctx.run_store.list()) == 1
        run = ctx.run_store.get(str(created["run_id"]))
        assert run is not None and not list(run.subdir("execution").iterdir())
        assert created["states"]["idea"] == created["states"]["experiment"] == "skipped"
        assert created["states"]["coding"] == "pending"


@pytest.mark.asyncio
async def test_repeating_same_real_observations_does_not_make_progress_or_claim_execution(tmp_path: Path) -> None:
    with _project(tmp_path) as (ctx, _source):
        await _incident_inspections(ctx)
        first = action_continuation(ctx.session, INCIDENT_PROMISE, response_mode="plain_reply")
        assert first is not None
        # Distinct real calls have distinct transport call IDs. Their unchanged
        # content must still share one progress identity, so a loop cannot use
        # repeated listing/search as unlimited fresh work.
        await _incident_inspections(ctx)
        again = action_continuation(ctx.session, INCIDENT_PROMISE, response_mode="plain_reply")
        assert again is not None and again.fingerprint == first.fingerprint
        stopped = action_continuation_exhausted_reply(ctx.session)
        assert re.search(r"尚未|未完成|未创建|未启动|未执行|没有(?:创建|启动)", stopped)
        assert not re.search(r"(?<!未)(?:已启动|已创建|已完成)|正在(?:运行|仿真|训练)", stopped)
        assert not ctx.run_store.list() and ctx.session.linked_run_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("question", [
    "代码里有 First7L 和 Second7L 两个配置，请选择本次需要修改的模型？",
    "请确认要用哪份配置：seven_a.yaml 还是 seven_b.yaml？",
    "训练预算是 2 个完整 epoch，还是 2 次优化器更新？请确认。",
])
async def test_genuine_selection_or_required_parameter_question_is_terminal(tmp_path: Path, question: str) -> None:
    with _project(tmp_path) as (ctx, _source):
        await _incident_inspections(ctx)
        decision = _decision_from_response(question)
        assert not decision.actions
        assert action_continuation(ctx.session, decision.reply, response_mode=decision.response_mode) is None
        assert not ctx.run_store.list()


@pytest.mark.asyncio
@pytest.mark.parametrize("request_text,answer", [
    ("epoch是什么意思？", "epoch 指完整遍历一次训练集，与一个 batch 的优化器更新不同。"),
    ("只解释两个配置有什么区别，不修改代码也不运行。", "两个配置的第二层抽头数不同；学习率和种子设置相同。"),
    ("我只要运行策略建议，先不要启动。", "建议固定数据与训练预算，分别比较改动前后的性能。"),
    ("我只要建议，先不要执行。", "我建议先读取配置，明确模型和训练预算，再决定是否启动。"),
])
async def test_plain_explanation_and_explicit_no_action_request_remain_terminal(
    tmp_path: Path, request_text: str, answer: str,
) -> None:
    with _project(tmp_path, request_text) as (ctx, _source):
        await _incident_inspections(ctx)
        decision = _decision_from_response(answer)
        assert decision.response_mode == "plain_reply"
        assert action_continuation(ctx.session, decision.reply, response_mode=decision.response_mode) is None
        assert not ctx.run_store.list()


@pytest.mark.parametrize("request_text", ["继续", "重试", "恢复"])
def test_pure_running_recovery_observation_completes_only_a_resume_request(request_text: str) -> None:
    # Authored policy data only; this does not claim that a process is running.
    session = CommanderSession(conv_id="running-policy", project="example", linked_run_id="original")
    _user(session, request_text)
    _observe(session, "run.recovery_status", {"run_id": "original"},
             {"ok": True, "status": "running", "run_id": "original"})
    assert action_continuation(session, "原任务正在运行，无需重复启动。", response_mode="plain_reply") is None
    assert session.linked_run_id == "original"


@pytest.mark.parametrize("request_text", ["停止当前仿真", "修改当前模型的第二层抽头"])
def test_running_status_is_not_completion_of_a_stop_or_modification_request(request_text: str) -> None:
    session = CommanderSession(conv_id="pending-policy", project="example", linked_run_id="original")
    _user(session, request_text)
    _observe(session, "run.recovery_status", {"run_id": "original"},
             {"ok": True, "status": "running", "run_id": "original"})
    assert action_continuation(session, "原任务正在运行，无需重复启动。", response_mode="plain_reply") is not None


@pytest.mark.parametrize("request_text", ["当前任务在运行吗？", "帮我看一下任务状态"])
def test_status_question_does_not_become_a_new_operation_from_the_answer(request_text: str) -> None:
    session = CommanderSession(conv_id="status-question", project="example", linked_run_id="original")
    _user(session, request_text)
    _observe(session, "run.status", {"run_id": "original"},
             {"ok": True, "run_id": "original", "states": {"execution": "running"}})
    assert action_continuation(session, "MARS 正在运行现有任务。", response_mode="plain_reply") is None
    assert session.linked_run_id == "original"


@pytest.mark.asyncio
async def test_protocol_clarification_is_not_overridden_by_operation_detection(tmp_path: Path) -> None:
    with _project(tmp_path) as (ctx, _source):
        await _incident_inspections(ctx)
        malformed = _decision_from_response('{"actions":[{"tool":"run.create","args":')
        assert malformed.response_mode == "clarification" and not malformed.actions
        assert action_continuation(ctx.session, malformed.reply, response_mode=malformed.response_mode) is None
        assert not ctx.run_store.list()


@pytest.mark.asyncio
async def test_unexecuted_admission_failure_can_continue_without_replaying_a_creation(tmp_path: Path) -> None:
    with _project(tmp_path) as (ctx, _source):
        await _incident_inspections(ctx)
        # Listing and searching are not a content read. The actual bridge
        # rejects this before allocating any workflow/task directory.
        args = {"entrypoint": "coding", "user_request": ORIGINAL_REQUEST}
        denied = await execute_tool("run.create", args, ctx)
        assert denied["ok"] is False and denied["status"] == "repository_inspection_required"
        assert not denied.get("run_id")
        _observe(ctx.session, "run.create", args, denied)
        assert action_continuation(ctx.session, INCIDENT_PROMISE, response_mode="plain_reply") is not None
        assert not ctx.run_store.list() and ctx.session.linked_run_id is None


@pytest.mark.asyncio
async def test_old_turn_reading_and_unrelated_link_do_not_restart_work_on_a_new_question(tmp_path: Path) -> None:
    with _project(tmp_path) as (ctx, _source):
        await _incident_inspections(ctx)
        assert action_continuation(ctx.session, INCIDENT_PROMISE, response_mode="plain_reply") is not None
        before = [message.to_dict() for message in ctx.session.messages]
        ctx.session.linked_run_id = "unrelated-retained-task"
        _user(ctx.session, "先别继续实验，解释一下 seed 是什么意思？")
        reply = "seed 是随机种子，用于复现初始化和采样等随机过程。"
        assert action_continuation(ctx.session, reply, response_mode="plain_reply") is None
        assert not ctx.run_store.list() and ctx.session.linked_run_id == "unrelated-retained-task"
        assert [message.to_dict() for message in ctx.session.messages[:-1]] == before


@pytest.mark.asyncio
@pytest.mark.parametrize("mentioned_by", ["user", "promise"])
@pytest.mark.parametrize("evidence_tool", [None, "code.repo_list", "code.repo_search"])
async def test_promised_read_uses_exact_real_known_file_and_never_repeats_current_attempt(
    tmp_path: Path, mentioned_by: str, evidence_tool: str | None,
) -> None:
    request_text = ("修改configs/seven_a.yaml第二层抽头增加2并运行2epochs，seed=2026"
                    if mentioned_by == "user" else ORIGINAL_REQUEST)
    reply = ("MARS先读取实际配置，再准备改动。" if mentioned_by == "user"
             else "MARS先读取configs/seven_a.yaml，核对字段再准备改动。")
    with _project(tmp_path, request_text) as (ctx, source):
        if evidence_tool is not None:
            args = {"path": "configs", "depth": 1} if evidence_tool == "code.repo_list" else {"query": "First7L"}
            await _inspect(ctx, evidence_tool, args)
        before = json.dumps([message.to_dict() for message in ctx.session.messages], sort_keys=True)
        action = promised_source_read_action(ctx.session, reply, response_mode="plain_reply")
        if mentioned_by != "user":
            assert action is None  # Assistant wording cannot supply user scope.
            assert not ctx.run_store.list()
            return
        assert action == {"tool": "code.repo_read_lines", "args": {"path": "configs/seven_a.yaml"}}
        assert json.dumps([message.to_dict() for message in ctx.session.messages], sort_keys=True) == before
        read = await _inspect(ctx, action["tool"], action["args"])
        assert read["output"]["sha256"] == hashlib.sha256((source / "configs/seven_a.yaml").read_bytes()).hexdigest()
        assert promised_source_read_action(ctx.session, reply, response_mode="plain_reply") is None
        assert not ctx.run_store.list()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [
    "configs", "../configs/seven_a.yaml", "/configs/seven_a.yaml",
    "C:\\configs\\seven_a.yaml", "configs/../configs/seven_a.yaml",
])
async def test_promised_read_does_not_guess_missing_directory_or_unsafe_path(tmp_path: Path, path: str) -> None:
    with _project(tmp_path, f"修改{path}后运行2epochs，seed=2026") as (ctx, _source):
        await _incident_inspections(ctx)
        assert promised_source_read_action(ctx.session, "MARS先读取明确配置再核对。", response_mode="plain_reply") is None
        assert ordinary_coding_handoff_actions(ctx.session, INCIDENT_PROMISE, response_mode="plain_reply") is None
        assert not ctx.run_store.list()


@pytest.mark.asyncio
async def test_explicit_missing_user_file_really_fails_once_and_cannot_generate_a_handoff(tmp_path: Path) -> None:
    request_text = "修改configs/not_there.yaml第二层抽头，然后训练2epochs，seed=2026"
    with _project(tmp_path, request_text) as (ctx, _source):
        action = promised_source_read_action(ctx.session, "MARS先核对明确配置。", response_mode="plain_reply")
        assert action == {"tool": "code.repo_read_lines", "args": {"path": "configs/not_there.yaml"}}
        failed = await execute_tool(action["tool"], action["args"], ctx)
        assert failed["ok"] is False and not failed.get("run_id")
        _observe(ctx.session, action["tool"], action["args"], failed)
        assert promised_source_read_action(ctx.session, "MARS再读取配置。", response_mode="plain_reply") is None
        assert ordinary_coding_handoff_actions(ctx.session, "MARS接下来创建任务。", response_mode="plain_reply") is None
        assert not ctx.run_store.list()


@pytest.mark.asyncio
async def test_failed_real_read_is_not_retried_by_the_promised_read_policy(tmp_path: Path) -> None:
    request_text = "修改configs/seven_a.yaml第二层抽头再运行2epochs，seed=2026"
    with _project(tmp_path, request_text) as (ctx, source):
        await _inspect(ctx, "code.repo_list", {"path": "configs", "depth": 1})
        path = source / "configs/seven_a.yaml"
        content = path.read_bytes()
        path.unlink()
        args = {"path": "configs/seven_a.yaml"}
        failed = await execute_tool("code.repo_read_lines", args, ctx)
        assert failed["ok"] is False
        _observe(ctx.session, "code.repo_read_lines", args, failed)
        path.write_bytes(content)
        assert promised_source_read_action(ctx.session, "MARS先读取配置。", response_mode="plain_reply") is None
        assert not ctx.run_store.list()


@pytest.mark.asyncio
async def test_explicit_symlink_escape_is_rejected_by_the_real_reader_not_treated_as_evidence(tmp_path: Path) -> None:
    request_text = "修改configs/linked.yaml的参数，然后训练2epochs，seed=2026"
    with _project(tmp_path, request_text) as (ctx, source):
        outside = tmp_path / "outside.yaml"
        outside.write_text("model_name: Outside\nseed: 2026\n")
        (source / "configs/linked.yaml").symlink_to(outside)
        action = promised_source_read_action(ctx.session, "MARS先读取明确配置。", response_mode="plain_reply")
        assert action == {"tool": "code.repo_read_lines", "args": {"path": "configs/linked.yaml"}}
        failed = await execute_tool(action["tool"], action["args"], ctx)
        assert failed["ok"] is False
        _observe(ctx.session, action["tool"], action["args"], failed)
        assert promised_source_read_action(ctx.session, "MARS再读取配置。", response_mode="plain_reply") is None
        assert ordinary_coding_handoff_actions(ctx.session, "MARS接下来创建任务。", response_mode="plain_reply") is None
        assert not ctx.run_store.list()


@pytest.mark.asyncio
@pytest.mark.parametrize("coding_name", [False, True])
async def test_deterministic_coding_handoff_preserves_original_request_and_only_really_allocates_once(
    tmp_path: Path, coding_name: bool,
) -> None:
    request_text = ("请从Coding开始。" if coding_name else "") + (
        "将configs/seven_a.yaml第二层抽头增加2，学习率减半，固定seed=2026，训练2个epoch，"
        "其余配置不变，与改动前对比。")
    with _project(tmp_path, request_text) as (ctx, source):
        await _inspect(ctx, "code.repo_read_lines", {"path": "configs/seven_a.yaml"})
        before = json.dumps([message.to_dict() for message in ctx.session.messages], sort_keys=True)
        plan = ordinary_coding_handoff_actions(ctx.session, "MARS接下来创建改动对照任务。", response_mode="plain_reply")
        assert plan is not None and len(plan) == 2
        assert plan[0]["tool"] == "run.create" and plan[1] == {"tool": "run.start", "args": {}}
        create_args = plan[0]["args"]
        assert create_args["entrypoint"] == "coding" and create_args["user_request"] == request_text
        assert create_args["repository_scope"] == "required"
        assert "configs/seven_a.yaml" in create_args["routing_reason"]
        assert set(create_args) <= {"entrypoint", "user_request", "repository_scope", "routing_reason", "task"}
        assert json.dumps([message.to_dict() for message in ctx.session.messages], sort_keys=True) == before
        assert not ctx.run_store.list()

        # Execute only the actual allocation stage. The second planned action
        # is inspected, never substituted or called: this test launches neither
        # a Coding model nor a training process and claims no start success.
        created = await execute_tool(plan[0]["tool"], create_args, ctx)
        assert created["ok"] and created["states"]["coding"] == "pending", created
        _observe(ctx.session, plan[0]["tool"], create_args, created)
        run = ctx.run_store.get(str(created["run_id"]))
        assert run is not None
        actual = ctx.orchestrator.session(run.run_id)
        assert actual.request.user_request == request_text and actual.request.entrypoint == "coding"
        assert "existing_execution_job" not in actual.request.extra
        assert actual.graph.state("idea").value == actual.graph.state("experiment").value == "skipped"
        assert ordinary_coding_handoff_actions(ctx.session, INCIDENT_PROMISE, response_mode="plain_reply") is None
        assert promised_source_read_action(ctx.session, "MARS先读取configs/seven_b.yaml。", response_mode="plain_reply") is None
        assert len(ctx.run_store.list()) == 1 and not list(run.subdir("execution").iterdir())
        assert yaml.safe_load((source / "configs/seven_a.yaml").read_text())["model"]["inner_kernel"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("request_text", [
    "只读取configs/seven_a.yaml，解释配置，不修改，不运行。",
    "读取configs/seven_a.yaml，只讨论方案，先不要执行。",
    "修改configs/seven_a.yaml，先创建不启动。",
])
async def test_only_read_advice_and_create_without_start_do_not_get_auto_start_plan(tmp_path: Path, request_text: str) -> None:
    with _project(tmp_path, request_text) as (ctx, _source):
        await _inspect(ctx, "code.repo_read_lines", {"path": "configs/seven_a.yaml"})
        assert ordinary_coding_handoff_actions(ctx.session, "MARS接下来创建任务。", response_mode="plain_reply") is None
        assert not ctx.run_store.list()


@pytest.mark.asyncio
async def test_coding_handoff_requires_real_current_content_read_and_no_unrelated_link(tmp_path: Path) -> None:
    request_text = "修改configs/seven_a.yaml第二层抽头增加2，然后训练2epochs，seed=2026"
    with _project(tmp_path, request_text) as (ctx, _source):
        await _incident_inspections(ctx)
        assert ordinary_coding_handoff_actions(ctx.session, INCIDENT_PROMISE, response_mode="plain_reply") is None
        await _inspect(ctx, "code.repo_read_lines", {"path": "configs/seven_a.yaml"})
        assert ordinary_coding_handoff_actions(ctx.session, INCIDENT_PROMISE, response_mode="plain_reply") is not None
        ctx.session.linked_run_id = "unrelated-existing-task"
        assert ordinary_coding_handoff_actions(ctx.session, INCIDENT_PROMISE, response_mode="plain_reply") is None
        assert not ctx.run_store.list()


@pytest.mark.asyncio
async def test_actual_candidate_selection_is_not_bypassed_by_deterministic_handoff(tmp_path: Path) -> None:
    with _project(tmp_path) as (ctx, _source):
        for path in ("configs/seven_a.yaml", "configs/seven_b.yaml"):
            await _inspect(ctx, "code.repo_read_lines", {"path": path})
        args = {"model_name": "7L", "max_iters": 2, "budget_unit": "epochs", "seed": 2026, "start": False}
        candidates = await execute_tool("run.existing_model", args, ctx)
        assert candidates["ok"] is False and candidates["status"] == "model_selection_required"
        assert len(candidates["candidates"]) == 2
        _observe(ctx.session, "run.existing_model", args, candidates)
        question = "有两个实际配置，请选择本次需要修改的模型。"
        assert ordinary_coding_handoff_actions(ctx.session, question, response_mode="plain_reply") is None
        assert promised_source_read_action(ctx.session, question, response_mode="plain_reply") is None
        assert not ctx.run_store.list()
