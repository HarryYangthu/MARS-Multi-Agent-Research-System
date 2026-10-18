"""Pure Commander protocol regressions; these inputs do not execute tools or models."""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.bridge.commander import _decision_from_response, _parse_decision
from app.bridge.commander_errors import CommanderDecisionError, conversation_failure
from app.harness.runtime.conversation_state import ConversationState


def test_incident_extra_closings_preserve_the_complete_start_action() -> None:
    authored = {
        "reply": "将启动研究。",
        "next_state": "executing",
        "actions": [{
            "tool": "create_and_start_run",
            "args": {
                "entrypoint": "pipeline",
                "user_request": "在基线 PIMC 代码基础上，保持模型参数量不变，使残差降低 2dB。",
                "task": "pimc_residual_minus2db",
            },
        }],
    }
    decision = _parse_decision(json.dumps(authored, ensure_ascii=False) + "]}")
    assert decision.reply == authored["reply"]
    assert decision.next_state == "executing"
    assert decision.actions == authored["actions"]


def test_actual_incident_stray_array_close_preserves_complete_existing_model_action() -> None:
    # Captured response shape from conv_2026-10-18T155011. This is pure parsing,
    # not a generated model response or a substituted tool execution.
    text = ('{"reply":"改用基线专用快捷通道。","next_state":"executing","actions":['
            '{"tool":"run.existing_model","args":{"model_name":"StaticPIMC",'
            '"config_path":"configs/static.yaml","entrypoint":"train_static.py",'
            '"max_iters":1,"budget_unit":"epochs","stop_after_execution":false}]}]}')
    decision = _decision_from_response(text)
    assert decision.response_mode == "structured"
    assert decision.reply == "改用基线专用快捷通道。"
    assert decision.next_state == "executing"
    assert decision.actions == [{"tool": "run.existing_model", "args": {
        "model_name": "StaticPIMC", "config_path": "configs/static.yaml",
        "entrypoint": "train_static.py", "max_iters": 1, "budget_unit": "epochs",
        "stop_after_execution": False}}]
    assert "seed" not in decision.actions[0]["args"]  # Never invent a missing scientific parameter.


@pytest.mark.parametrize("text", [
    "请告诉我需要运行哪个模型，以及训练多少轮。",
    "我可以先解释这些设置分别控制什么。",
    "参数名可以写成 `lr_init`，不需要指定 JSON。",
    '```yaml\nmodel_name: ExistingModel\nlr_init: 0.0001\n```',
    '["参数量", "性能", "收敛速度"]',
])
def test_natural_response_is_retained_without_inventing_tool_actions(text: str) -> None:
    decision = _decision_from_response(text)
    assert decision.reply == text and decision.response_mode == "plain_reply"
    assert decision.actions == [] and decision.next_state is None


@pytest.mark.parametrize("text", [
    "", '{"actions":[{"tool":"run.existing_model","args":',
    '{"reply":"first"} {"actions":[{"tool":"create_and_start_run"}]}',
    '{"actions":[{"tool":"get_run_status"},{"tool":"create_and_start_run","args":false}]}',
    '{"actions":[{"tool":"get_run_status","tool":"create_and_start_run"}]}',
    '{"actions":[{"tool":"run.existing_model","args":{"seed":NaN}}]}',
    "任务已启动，我会继续处理。",
    "我现在创建仿真任务。",
    "The experiment has started.",
    "当前任务正在运行。",
    "已停止当前实验。", "已暂停仿真。", "已取消当前任务。", "已删除临时任务。", "已批准方案。", "已修改代码。",
    "已经停止当前实验。", "已经批准方案。", "已经保存改动。", "代码已经提交。",
    "The experiment was stopped.", "The run is paused.",
])
def test_unrecoverable_response_requests_clarification_without_partial_dispatch_or_http_error(text: str) -> None:
    decision = _decision_from_response(text)
    assert decision.response_mode == "clarification"
    assert decision.reply and "已有任务和结果已保留" in decision.reply
    assert decision.actions == [] and decision.next_state is None


def test_single_stray_close_repair_does_not_accept_multiple_decisions_or_missing_values() -> None:
    for text in (
        '{"actions":[{"tool":"get_run_status","args":{}}]}] {"actions":[]}',
        '{"actions":[{"tool":"run.existing_model","args":{"seed":]}]}',
        '{"actions":[{"tool":"run.existing_model","args":{"seed":2026}]}',
    ):
        assert _decision_from_response(text).response_mode == "clarification"


@pytest.mark.parametrize("suffix", ["", " \n\t", "}", "]}", " \n] }\t"])
def test_only_redundant_closing_suffix_is_tolerated(suffix: str) -> None:
    decision = _parse_decision('{"reply":"  请确认目标  ","actions":[]}' + suffix)
    assert decision.reply == "请确认目标"
    assert decision.actions == []


@pytest.mark.parametrize("fence", ["```", "```json", "```JSON"])
def test_matched_json_fences_are_accepted(fence: str) -> None:
    decision = _parse_decision(f'{fence}\n{{"reply":"已收到","actions":[]}}\n```')
    assert decision.reply == "已收到"


def test_quoted_internal_delimiters_and_escaped_quotes_are_unchanged() -> None:
    reply = '保留字面量 {"key": "]}"}，以及反引号 ``` 和换行\n。'
    args = {"user_request": '字符串内的 }、]、{ 不是边界："quoted"。'}
    decision = _parse_decision(json.dumps({
        "reply": reply, "actions": [{"tool": "create_and_start_run", "args": args}],
    }, ensure_ascii=False) + "]}")
    assert decision.reply == reply
    assert decision.actions == [{"tool": "create_and_start_run", "args": args}]


@pytest.mark.parametrize("state", list(ConversationState))
def test_known_states_are_accepted(state: ConversationState) -> None:
    decision = _parse_decision(json.dumps({"reply": "状态更新", "next_state": state.value}))
    assert decision.next_state == state.value


def test_optional_fields_keep_their_protocol_defaults() -> None:
    reply = _parse_decision('{"reply":"讨论目标"}')
    assert reply.next_state is None
    assert reply.actions == []
    action = _parse_decision('{"actions":[{"tool":"get_run_status"}]}')
    assert action.reply == ""
    assert action.actions == [{"tool": "get_run_status", "args": {}}]


def test_actual_flat_source_search_keeps_all_arguments_without_execution() -> None:
    action = {'tool': 'code.repo_search', 'query': 'loss', 'path': 'libs/engine.py'}
    result = _parse_decision(json.dumps({'actions': [action]}))
    assert result.actions == [{'tool': 'code.repo_search', 'args': {'query': 'loss', 'path': 'libs/engine.py'}}]
    with pytest.raises(CommanderDecisionError, match='ambiguous'):
        _parse_decision(json.dumps({'actions': [{**action, 'args': {'query': 'another'}}]}))


@pytest.mark.parametrize("text", [
    "", "模型只返回了普通文本", "null", "[]", '[{"reply":"nested"}]',
    '{"reply":"first"} {"reply":"second"}',
    '{"reply":"first"}{', '{"reply":"first"}[',
    '{"reply":"truncated"', '{"reply":"broken,"actions":[]}',
    '```json\n{"reply":"first"}\n```\n{"reply":"second"}',
])
def test_ambiguous_or_malformed_documents_fail_explicitly(text: str) -> None:
    with pytest.raises(CommanderDecisionError):
        _parse_decision(text)


@pytest.mark.parametrize('text', [
    'prefix {"reply":"first"}', '{"reply":"first"},', '{"reply":"first"} prose',
    '{"reply":"first"};', '{"reply":"first","actions":[],}',
    '```json\n{"reply":"first"}', '```python\n{"reply":"first"}\n```',
    '```json {"reply":"first"}```', '```json\n{"reply":"first"}\n``` trailing prose',
    '\ufeff```JSON\r\n{"reply":"first"}\r\n```',
])
def test_noncritical_presentation_does_not_block_complete_decision(text: str) -> None:
    decision = _decision_from_response(text)
    assert decision.reply == 'first' and decision.actions == []
    assert decision.response_mode == 'structured'


@pytest.mark.parametrize("payload", [
    {}, {"next_state": "executing"}, {"unexpected": "object"},
    {"reply": None}, {"reply": True}, {"reply": 2}, {"reply": {}}, {"reply": []},
    {"reply": "ok", "next_state": "unknown"}, {"reply": "ok", "next_state": ""},
    {"reply": "ok", "next_state": False}, {"reply": "ok", "next_state": {}},
    {"reply": "ok", "actions": None}, {"reply": "ok", "actions": {}},
    {"reply": "ok", "actions": "[]"}, {"reply": "ok", "actions": False},
    {"actions": [None]}, {"actions": ["get_run_status"]}, {"actions": [[]]},
    {"actions": [{}]}, {"actions": [{"tool": ""}]}, {"actions": [{"tool": " \n"}]},
    {"actions": [{"tool": 1}]}, {"actions": [{"tool": True}]},
    {"actions": [{"tool": "get_run_status", "args": None}]},
    {"actions": [{"tool": "get_run_status", "args": []}]},
    {"actions": [{"tool": "get_run_status", "args": "{}"}]},
    {"actions": [{"tool": "get_run_status", "args": False}]},
])
def test_invalid_field_types_or_shapes_are_not_silently_coerced(payload: dict[str, Any]) -> None:
    with pytest.raises(CommanderDecisionError):
        _parse_decision(json.dumps(payload))


def test_later_invalid_action_rejects_the_whole_decision() -> None:
    # No partial Decision escapes to the dispatch loop, even when its first
    # authored action could start a run. This test itself performs no dispatch.
    payload = {
        "reply": "应当作为一个整体校验",
        "actions": [
            {"tool": "create_and_start_run", "args": {"entrypoint": "pipeline", "user_request": "研究目标"}},
            {"tool": "get_run_status", "args": "invalid argument object"},
        ],
    }
    with pytest.raises(CommanderDecisionError):
        _parse_decision(json.dumps(payload))


@pytest.mark.parametrize("text", [
    '{"reply":"first","reply":"second"}',
    '{"reply":"ok","actions":[],"actions":[{"tool":"create_and_start_run"}]}',
    '{"actions":[{"tool":"get_run_status","tool":"create_and_start_run"}]}',
    '{"actions":[{"tool":"get_run_status","args":{"run_id":"first","run_id":"second"}}]}',
])
def test_duplicate_keys_at_any_depth_are_ambiguous(text: str) -> None:
    with pytest.raises(CommanderDecisionError):
        _parse_decision(text)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e999", "-1e999"])
def test_nonfinite_tool_arguments_are_rejected(value: str) -> None:
    text = '{"actions":[{"tool":"get_run_status","args":{"limit":' + value + '}}]}'
    with pytest.raises(CommanderDecisionError):
        _parse_decision(text)


def test_finite_numeric_and_nested_argument_values_are_preserved() -> None:
    args = {"limit": 3, "weight": 0.5, "large": 1e308, "nested": [None, True, {"text": "Infinity"}]}
    decision = _parse_decision(json.dumps({"actions": [{"tool": "get_run_status", "args": args}]}))
    assert decision.actions == [{"tool": "get_run_status", "args": args}]


def test_protocol_error_mapping_is_actionable_and_does_not_expose_raw_payload() -> None:
    failure = conversation_failure(CommanderDecisionError("private-provider-payload /private/path api_key=secret"))
    assert failure is not None
    code, message = failure
    assert code == "invalid_commander_decision"
    assert "对话" in message and "已保留" in message
    assert "不会因此被停止" in message
    assert "格式" not in message and "发送" not in message
    assert all(value not in message for value in ("private-provider-payload", "/private/path", "api_key", "secret"))


def test_api_response_fallback_keeps_existing_task_and_messages() -> None:
    from app.api.chat import preserve_conversation_on_response_error
    from app.bridge.commander_session import ChatMessage, CommanderSession
    session = CommanderSession(conv_id='response-fallback', project='synthetic_regression')
    session.linked_run_id = 'existing_actual_task_reference'
    user = ChatMessage(role='user', content='查看当前进度')
    session.add(user)
    preserve_conversation_on_response_error(session)
    assert session.messages[0] == user and session.linked_run_id == 'existing_actual_task_reference'
    assert len(session.messages) == 2 and session.messages[-1].role == 'assistant'
    assert all(message.role != 'tool' for message in session.messages)


def test_edit_response_fallback_stays_in_its_original_turn() -> None:
    from app.api.chat import preserve_conversation_on_response_error
    from app.bridge.commander_session import ChatMessage, CommanderSession
    session = CommanderSession(conv_id='edited-response', project='synthetic_regression')
    first = session.add(ChatMessage(role='user', content='运行旧模型'))
    second = session.add(ChatMessage(role='user', content='解释输出'))
    later = session.add(ChatMessage(role='assistant', content='后一个回合的回复', turn_id=second.id))
    preserve_conversation_on_response_error(session, first.id)
    assert session.messages[0] == first and session.messages[2:] == [second, later]
    assert session.messages[1].role == 'assistant' and session.messages[1].turn_id == first.id
    assert session.edit_cursor is None and session.active_turn_id is None
