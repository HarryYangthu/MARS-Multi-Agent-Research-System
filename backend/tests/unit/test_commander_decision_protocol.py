"""Pure Commander protocol regressions; these inputs do not execute tools or models."""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.bridge.commander import _parse_decision
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


@pytest.mark.parametrize("text", [
    "", "模型只返回了普通文本", "null", "[]", '[{"reply":"nested"}]',
    'prefix {"reply":"not a root decision"}',
    '{"reply":"first"} {"reply":"second"}',
    '{"reply":"first"},', '{"reply":"first"} prose', '{"reply":"first"}{',
    '{"reply":"first"}[', '{"reply":"first"};',
    '{"reply":"truncated"', '{"reply":"broken,"actions":[]}',
    '{"reply":"first","actions":[],}',
    '```json\n{"reply":"unclosed fence"}',
    '```python\n{"reply":"wrong fence"}\n```',
    '```json {"reply":"no newline"}```',
    '```json\n{"reply":"first"}\n``` trailing prose',
    '```json\n{"reply":"first"}\n```\n{"reply":"second"}',
])
def test_ambiguous_or_malformed_documents_fail_explicitly(text: str) -> None:
    with pytest.raises(CommanderDecisionError):
        _parse_decision(text)


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
    assert "重新读取" in message
    assert "继续" in message
    assert all(value not in message for value in ("private-provider-payload", "/private/path", "api_key", "secret"))
