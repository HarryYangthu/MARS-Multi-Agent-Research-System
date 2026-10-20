"""Pure parsing and actual file/tool snapshots; no provider substitutes."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.bridge.commander_semantics import parse_semantic_review, semantic_review_snapshot
from app.bridge.commander_completion import assess_completion, parse_turn_goal
from app.bridge.commander_session import ChatMessage, CommanderSession, CommanderSessionStore
from app.bridge.commander_tools import ToolContext, execute_tool
from app.bridge.orchestrator import Orchestrator
from app.harness.project_workspace import open_folder
from app.harness.runtime.event_bus import InProcessEventBus
from app.settings import reset_settings_cache
from app.storage.run_store import RunStore


def _payload(**changes: Any) -> dict[str, Any]:
    result = {"goal": {"intent": "action", "required_effects": ["run_created", "run_started"], "run_target": "new"},
              "status": "pending", "reason": "尚无创建启动回执", "question": "", "evidence_ids": ["actual-read"],
              "missing": ["run_created", "run_started"]}
    result.update(changes)
    return result


@pytest.mark.parametrize("wrapper", ["{}", "```json\n{}\n```", "语义核对：\n{}\n以上是本轮判断。"])
def test_json_fences_and_presentation_preserve_goal_and_actual_ids(wrapper: str) -> None:
    raw = _payload()
    review = parse_semantic_review(wrapper.format(json.dumps(raw, ensure_ascii=False)),
                                   allowed_evidence_ids=frozenset({"actual-read"}))
    assert review.goal_raw == raw["goal"] and review.status == "pending"
    assert review.evidence_ids == ("actual-read",) and review.missing == ("run_created", "run_started")
    assert "actions" not in asdict(review) and "args" not in asdict(review)


def test_explicit_natural_labels_are_accepted_without_inventing_action_parameters() -> None:
    review = parse_semantic_review(
        "意图：操作\n完成义务：run_created、run_started\n目标任务：新任务\n状态：待继续\n"
        "原因：目前只有读取回执\n问题：\n证据：[\"actual-read\"]\n缺少：run_created, run_started",
        allowed_evidence_ids=frozenset({"actual-read"}))
    assert review.status == "pending" and review.goal_raw == _payload()["goal"]
    assert review.evidence_ids == ("actual-read",) and review.question == ""


@pytest.mark.parametrize("text", [
    "我现在会开始创建并启动。", "已经完成了用户所有要求。", "status: completed", "状态：已经全部完成",
    '{"status":"pending","goal":',
    '{"status":"pending"}{"status":"answer_sufficient"}',
    '{"status":"pending","status":"answer_sufficient"}',
    "状态：答复充分\n状态：待继续\n意图：问答\n目标任务：无",
])
def test_incomplete_ambiguous_or_unlabelled_text_is_unknown_not_a_format_error(text: str) -> None:
    review = parse_semantic_review(text, allowed_evidence_ids=frozenset())
    assert review.status == "unknown" and not review.evidence_ids


@pytest.mark.parametrize("goal", [
    {"intent": [], "required_effects": ["run_started"], "run_target": "new"},
    {"intent": "action", "required_effects": [["run_started"]], "run_target": "new"},
    {"intent": "action", "required_effects": [], "run_target": "new"},
    {"intent": "action", "required_effects": ["trained_successfully"], "run_target": "new"},
    {"intent": "action", "required_effects": ["run_started"], "run_target": []},
    {"intent": "action", "required_effects": ["run_started"], "run_target": "new", "expected_tools": [{"tool": "run.start"}]},
])
def test_invalid_goal_shapes_are_unknown(goal: Any) -> None:
    review = parse_semantic_review(json.dumps(_payload(goal=goal)), allowed_evidence_ids=frozenset({"actual-read"}))
    assert review.status == "unknown" and review.goal_raw is None


def test_unknown_observation_ids_cannot_become_completion_evidence() -> None:
    review = parse_semantic_review(json.dumps(_payload(evidence_ids=["actual-read", "invented-start"])),
                                   allowed_evidence_ids=frozenset({"actual-read"}))
    assert review.status == "unknown" and not review.evidence_ids
    assert review.reason == "semantic_review_unobserved_evidence"


def test_model_answer_sufficient_does_not_certify_an_action() -> None:
    review = parse_semantic_review(json.dumps(_payload(status="answer_sufficient")),
                                  allowed_evidence_ids=frozenset({"actual-read"}))
    assert review.status == "pending"
    answer = parse_semantic_review(json.dumps(_payload(status="answer_sufficient", goal={
        "intent": "answer", "required_effects": [], "run_target": "none"}, evidence_ids=[], missing=[])),
        allowed_evidence_ids=frozenset())
    assert answer.status == "answer_sufficient" and answer.goal_raw is not None


def test_actual_explanation_review_projection_ignores_nonexecution_observed_annotation() -> None:
    # Semantic fields projected from the real ordinary-question regression;
    # no private conversation IDs, provider, tools or success receipts are used.
    raw = {'goal': {'intent': 'answer', 'required_effects': ['observed'], 'run_target': 'none'},
           'status': 'answer_sufficient', 'reason': '概念解释和简短例子已充分回答用户问题',
           'question': '', 'evidence_ids': [], 'missing': []}
    review = parse_semantic_review(json.dumps(raw), allowed_evidence_ids=frozenset())
    assert review.status == 'answer_sufficient'
    assert review.goal_raw == {'intent': 'answer', 'required_effects': [], 'run_target': 'none'}
    session = CommanderSession('explanation-projection', 'example')
    user = ChatMessage(role='user', content='只解释 epoch 和 optimizer step 的区别，用简短例子说明。不要创建或运行任务。')
    session.active_turn_id = user.id
    session.add(user)
    goal = parse_turn_goal(review.goal_raw, session)
    direct_goal = parse_turn_goal(raw['goal'], session)
    assert goal is not None and direct_goal == goal and goal.source_turn_id == user.id
    answer = '一个 epoch 遍历一次训练集；optimizer step 更新一次参数。例如每轮有十个 batch，每个 batch 更新一次，就是十次 step。'
    verdict = assess_completion(session, goal, answer, semantic_status=review.status)
    assert verdict.status == 'fulfilled' and verdict.reason == 'semantic_answer_sufficient'
    assert not verdict.evidence_ids and not session.linked_run_id
    assert assess_completion(session, goal, answer).status == 'pending'
    assert assess_completion(session, goal, '', semantic_status=review.status).status == 'pending'


def test_answer_annotation_normalization_cannot_satisfy_pending_action_or_real_observation() -> None:
    session = CommanderSession('pending-bound-obligations', 'example')
    user = ChatMessage(role='user', content='请创建并启动任务。')
    session.active_turn_id = user.id
    session.add(user)
    action = parse_turn_goal({'intent': 'action', 'required_effects': ['run_created', 'run_started'],
                              'run_target': 'new'}, session)
    observe = parse_turn_goal({'intent': 'observe', 'required_effects': ['observed'], 'run_target': 'none'}, session)
    assert action is not None and observe is not None
    for existing in (action, observe):
        verdict = assess_completion(session, existing, '已经充分解释。', semantic_status='answer_sufficient')
        assert verdict.status == 'pending' and verdict.missing == existing.required_effects
    for effect in ('run_created', 'run_started', 'artifact_updated', 'exported', 'tool_effect'):
        raw = {'intent': 'answer', 'required_effects': ['observed', effect], 'run_target': 'new'}
        assert parse_turn_goal(raw, session) is None
        review = parse_semantic_review(json.dumps({'goal': raw, 'status': 'answer_sufficient'}),
                                       allowed_evidence_ids=frozenset())
        assert review.status == 'unknown' and review.goal_raw is None


def test_goal_projects_only_semantics_while_tolerating_internal_explanations() -> None:
    raw = _payload()
    raw["goal"].update({"reason": "额外解释", "description": "查阅后回答", "source_turn_id": "not-authoritative",
                        "args": {"seed": 999}, "actions": [{"tool": "run.start", "args": {}}]})
    review = parse_semantic_review(json.dumps(raw), allowed_evidence_ids=frozenset({"actual-read"}))
    assert review.goal_raw == _payload()["goal"] and review.status == "pending"
    assert review.goal_raw is not None
    assert not {"reason", "description", "source_turn_id", "args", "actions"} & review.goal_raw.keys()


def test_clarification_and_observed_block_remain_semantics_for_host_verification() -> None:
    for status in ("needs_user", "blocked"):
        review = parse_semantic_review(json.dumps(_payload(status=status, question="本次选择哪个模型配置？")),
                                      allowed_evidence_ids=frozenset({"actual-read"}))
        assert review.status == status and review.question == "本次选择哪个模型配置？"
        assert review.evidence_ids == ("actual-read",)
    rejected = parse_semantic_review(json.dumps(_payload(actions=[{"tool": "run.start", "args": {}}])),
                                    allowed_evidence_ids=frozenset({"actual-read"}))
    assert rejected.status == "unknown"


def test_inactive_legacy_messages_are_not_a_current_user_or_current_evidence() -> None:
    session = CommanderSession(conv_id="inactive", project="example")
    session.add(ChatMessage(role="user", content="旧回合，没有当前处理的请求"))
    session.add(ChatMessage(role="tool", content="旧记录", tool_name="code.repo_list", tool_result={"ok": True}))
    snapshot = semantic_review_snapshot(session, "旧回复")
    assert snapshot["current_user"] is None and not snapshot["observations"]


@pytest.mark.parametrize("field", ["goal", "previous_goal"])
def test_pending_original_user_is_hash_verified_beyond_last_three_users(field: str) -> None:
    session = CommanderSession(conv_id="retained-goal", project="example")
    original = ChatMessage(role="user", content="只改配置参数，两组各跑完整2个epoch。")
    session.active_turn_id = original.id
    session.add(original)
    for index in range(5):
        message = ChatMessage(role="user", content=f"历史跟进{index}")
        session.active_turn_id = message.id
        session.add(message)
    current = ChatMessage(role="user", content="继续")
    session.active_turn_id = current.id
    session.add(current)
    session.completion_state = {field: {"source_turn_id": original.id, "project": session.project,
        "source_user_sha256": hashlib.sha256(original.content.encode()).hexdigest()}}
    snapshot = semantic_review_snapshot(session, "正在继续。")
    assert original.id not in {row["id"] for row in snapshot["previous_users"]}
    assert snapshot["pending_goal_user"]["id"] == original.id
    assert snapshot["pending_goal_user"]["content"] == original.content
    assert snapshot["pending_goal_user"]["reference_only"] is True
    assert snapshot["current_user"]["content"] == "继续"
    session.completion_state[field]["source_user_sha256"] = "0" * 64
    assert semantic_review_snapshot(session, "继续核对")["pending_goal_user"] is None


@pytest.mark.parametrize("state", [None, [], {"goal": []}, {"goal": {"source_turn_id": [], "source_user_sha256": []}}])
def test_malformed_internal_goal_metadata_does_not_raise_or_invent_an_original_request(state: Any) -> None:
    session = CommanderSession(conv_id="legacy-metadata", project="example")
    current = ChatMessage(role="user", content="继续")
    session.active_turn_id = current.id
    session.add(current)
    session.completion_state = state
    snapshot = semantic_review_snapshot(session, "继续核对。")
    assert snapshot["pending_goal_user"] is None and snapshot["current_user"]["id"] == current.id


@pytest.mark.asyncio
async def test_snapshot_keeps_exact_current_user_and_only_real_current_turn_tool_receipts(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARS_FOLDER_PROJECTS_REGISTRY", str(tmp_path / "registry.json"))
    reset_settings_cache()
    try:
        project = open_folder(str(tmp_path / "project"), create=True)
        code = tmp_path / "code"
        (code / "configs").mkdir(parents=True)
        selected = code / "configs/current.yaml"
        selected.write_text("model_name: ExistingModel\nlr: 0.0004\n")
        (project.metadata_root / "repo_link.yaml").write_text(yaml.safe_dump({
            "repo_path": str(code), "allowed_paths": ["configs/"], "read_only": True}))
        sessions = CommanderSessionStore(tmp_path / "conversations", project_storage=False)
        session = sessions.create(project=project.name)
        store = RunStore(tmp_path / "runs", project_storage=False)
        ctx = ToolContext(Orchestrator(run_store=store, bus=InProcessEventBus()), session, store)
        old = ChatMessage(role="user", content="以前的问题")
        session.active_turn_id = old.id
        session.add(old)
        previous_read = await execute_tool("code.repo_read_lines", {"path": "configs/current.yaml"}, ctx)
        assert previous_read["ok"]
        old_receipt = session.add(ChatMessage(role="tool", content="以前的真实读取", tool_name="code.repo_read_lines",
                                              tool_args={"path": "configs/current.yaml"}, tool_result=previous_read))
        session.add(ChatMessage(role="assistant", content="UNRELATED_FULL_BACKGROUND" * 3000))
        user_text = "只改 YAML 参数，不要修改 Python 代码；学习率改为0.0002，两组各跑完整2个epoch。"
        current = ChatMessage(role="user", content=user_text)
        session.active_turn_id = current.id
        session.add(current)
        actual = await execute_tool("code.repo_read_lines", {"path": "configs/current.yaml"}, ctx)
        assert actual["ok"] and actual["output"]["sha256"] == hashlib.sha256(selected.read_bytes()).hexdigest()
        receipt = session.add(ChatMessage(role="tool", content=json.dumps(actual), tool_name="code.repo_read_lines",
                                         tool_args={"path": "configs/current.yaml"}, tool_result=actual))
        sessions.persist(session)
        restored = CommanderSessionStore(tmp_path / "conversations", project_storage=False).get(session.conv_id)
        assert restored is not None
        assert [message.to_dict() for message in restored.messages] == [message.to_dict() for message in session.messages]
        # Reloading interrupts an abandoned active turn. Review the genuinely
        # active in-memory turn, as Commander.handle_user_message does.
        before = json.dumps([message.to_dict() for message in session.messages], sort_keys=True)
        snapshot = semantic_review_snapshot(session, "先核对配置，再创建任务。")
        assert snapshot["current_user"] == {"id": current.id, "turn_id": current.id, "content": user_text}
        assert snapshot["previous_users"][0]["content"] == old.content
        assert [row["id"] for row in snapshot["observations"]] == [receipt.id]
        assert old_receipt.id not in [row["id"] for row in snapshot["observations"]]
        assert snapshot["observations"][0]["result"] == actual
        assert "UNRELATED_FULL_BACKGROUND" not in json.dumps(snapshot)
        snapshot["observations"][0]["result"]["ok"] = False
        assert json.dumps([message.to_dict() for message in session.messages], sort_keys=True) == before
        # A sufficient observation answer still needs an independently real
        # receipt from this exact user turn; old reads cannot complete it.
        observe_user = ChatMessage(role="user", content="读取configs/current.yaml，告诉我当前学习率。")
        session.active_turn_id = observe_user.id
        session.add(observe_user)
        goal_raw = {"intent": "observe", "required_effects": ["observed"], "run_target": "none",
                    "expected_tools": ["code.repo_read_lines"], "description": "只读查看配置"}
        sem = parse_semantic_review(json.dumps(_payload(goal=goal_raw, status="answer_sufficient", evidence_ids=[], missing=[])),
                                    allowed_evidence_ids=frozenset())
        assert sem.status == "answer_sufficient"
        goal = parse_turn_goal(sem.goal_raw, session)
        assert goal is not None
        answer = "当前配置的学习率是0.0004。"
        assert assess_completion(session, goal, answer, semantic_status=sem.status).status == "pending"
        observed = await execute_tool("code.repo_read_lines", {"path": "configs/current.yaml"}, ctx)
        assert observed["ok"]
        observed_message = session.add(ChatMessage(role="tool", content=json.dumps(observed), tool_name="code.repo_read_lines",
            tool_args={"path": "configs/current.yaml"}, tool_result=observed))
        sem = parse_semantic_review(json.dumps(_payload(goal=goal_raw, status="answer_sufficient",
            evidence_ids=[observed_message.id], missing=[])), allowed_evidence_ids=frozenset({observed_message.id}))
        verdict = assess_completion(session, goal, answer, semantic_status=sem.status, evidence_ids=sem.evidence_ids)
        assert verdict.status == "fulfilled" and verdict.evidence_ids == (observed_message.id,)
        assert not store.list() and selected.read_text() == "model_name: ExistingModel\nlr: 0.0004\n"
    finally:
        reset_settings_cache()


def test_auxiliary_review_preserves_glm_compatibility_in_the_actual_request_builder() -> None:
    from app.bridge.commander_semantics import semantic_review_config
    from app.harness.llm.openai_provider import _OpenAICompatProvider
    from app.harness.llm.provider_base import LLMConfig, Message
    original = LLMConfig(provider='openai_compatible', model='glm-5.3', thinking_enabled=True,
                         reasoning_effort='low', max_tokens=8192, request_timeout_seconds=120,
                         json_mode=True, response_schema='optional-primary-schema', extra={'retained': 'setting'})
    config = semantic_review_config(original, 30)
    # This calls the real pure wire builder, without constructing an SDK client,
    # substituting a provider or issuing a model request.
    provider = _OpenAICompatProvider(api_key='unused-wire-builder-only', base_url='http://127.0.0.1:1/v1',
                                    provider_name='openai_compatible')
    request = provider._request_kwargs([Message('user', '核对本轮结果')], config)
    assert request['extra_body']['thinking']['type'] == 'enabled'
    assert config.thinking_enabled is True and config.reasoning_effort == 'low'
    assert config.max_tokens == 4096 and config.request_timeout_seconds == 30 and config.max_retries == 0
    assert not config.json_mode and config.response_schema is None and not config.tools
    assert config.extra == original.extra and config.extra is not original.extra
    assert provider._client is None and original.max_tokens == 8192 and original.json_mode


def test_auxiliary_review_keeps_nonreasoning_provider_settings_and_output_bound() -> None:
    from app.bridge.commander_semantics import semantic_review_config
    from app.harness.llm.provider_base import LLMConfig
    config = semantic_review_config(LLMConfig(provider='openai', model='any', thinking_enabled=False,
                                             request_timeout_seconds=10, max_tokens=2048), 30)
    assert config.thinking_enabled is False and config.max_tokens == 1000
    assert config.request_timeout_seconds == 10
