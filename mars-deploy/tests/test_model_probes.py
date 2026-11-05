"""Pure protocol and report checks; no model, HTTP service or tool execution is replaced."""
import copy
import importlib.util
import json
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location("check_models", Path(__file__).parents[1] / "checks/check_models.py")
check = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = check
spec.loader.exec_module(check)


def test_json_and_tool_checks_reject_wrong_or_partial_values():
    content = json.dumps({"answer": "pong", "context": "input" * 120})
    assert check.validate_json({"ok": True, "content": content, "reply": content[-60:]})["ok"]
    assert not check.validate_json({"ok": True, "content": 'prefix {"answer":"pong"}'})["ok"]
    assert not check.validate_json({"ok": False, "content": '{"answer":"pong"}'})["ok"]
    assert not check.validate_json({"ok": True, "content": '{"answer":"wrong"}'})["ok"]
    for args in ('{"a":"2","b":3}', '{"a":true,"b":3}', '{"a":2,"b":4}', '{"a":2,'):
        assert check.tool_arguments({"tool_calls": [{"id": "input", "function": {"name": "add", "arguments": args}}]}) is None


def test_transport_selection_and_readiness_do_not_confuse_pong_with_role_success():
    result = {"stream": {"ok": True}, "nonstream": {"ok": False}}
    assert check.select_transport(result) == "stream"
    assert check.select_transport(result, "nonstream") is None
    assert check.select_transport({"stream": {"ok": False}, "nonstream": {"ok": True}}) == "nonstream"
    assert check.select_transport({"stream": {"ok": False}, "nonstream": {"ok": False}}) is None
    result.update(capability_transport="stream", json={"ok": False})
    assert check.suggest("strong", result) == {"stream": True, "json_mode": False}
    assert not check.role_ready("strong", result)
    result["structured"] = {"ok": True}
    assert check.role_ready("strong", result)
    result["tools"] = {"native_tool_call": True, "round_trip": {"ok": False}}
    assert not check.role_ready("coder", result)
    result["tools"]["round_trip"]["ok"] = True
    assert check.role_ready("coder", result)


def test_thinking_variants_remove_conflicts_without_mutating_provider_options():
    role = {"thinking": True, "reasoning_effort": "high", "extra_body": {"thinking": {"type": "enabled"},
        "chat_template_kwargs": {"enable_thinking": True, "other": 7}, "custom": "retained"}}
    before = copy.deepcopy(role)
    assert check.thinking_body(role, {}) == {"chat_template_kwargs": {"other": 7}, "custom": "retained"}
    off = check.thinking_body(role, check.THINKING["zhipu_disabled"])
    assert off["thinking"] == {"type": "disabled"} and "enable_thinking" not in off["chat_template_kwargs"]
    assert role == before


def test_tool_result_history_retains_reasoning_and_call_identity():
    response = {"content": "", "reasoning_content": "protocol history input", "tool_calls": [
        {"id": "history-input", "type": "function", "function": {"name": "add", "arguments": '{"a":2,"b":3}'}}]}
    before = copy.deepcopy(response)
    messages = check.tool_messages(response, 5)
    assert messages[-2]["reasoning_content"] == response["reasoning_content"]
    assert messages[-2]["tool_calls"] == response["tool_calls"]
    assert messages[-1] == {"role": "tool", "tool_call_id": "history-input", "content": "5"}
    assert response == before


def test_thinking_effect_requires_a_successful_same_dialect_pair():
    samples = {"none": {"ok": True, "reasoning_chars": 20}, "zhipu_disabled": {"ok": True, "reasoning_chars": 0}}
    assert check.thinking_effects(samples)["zhipu"]["status"] == "not_checked"
    samples["zhipu_enabled"] = {"ok": True, "reasoning_chars": 0}
    assert check.thinking_effects(samples)["zhipu"]["status"] == "inconclusive"
    samples["zhipu_enabled"]["reasoning_chars"] = 30
    assert check.thinking_effects(samples)["zhipu"]["status"] == "observed_disabled"
    assert check.suggest("strong", {"thinking": samples})["to_disable_thinking"] == {"thinking": {"type": "disabled"}}
    assert "[redacted]" in check.safe_error(ValueError("key=secret-input"), {"api_key": "secret-input"})
