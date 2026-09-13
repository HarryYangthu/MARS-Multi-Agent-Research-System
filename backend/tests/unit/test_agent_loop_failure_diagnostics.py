"""Error-value and disk-ledger contracts; no provider calls or service substitutes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from openai import APIConnectionError, APIStatusError, APITimeoutError

from app.bridge.agent_runner import _write_agent_failure_diagnostic
from app.harness.agent_loop.executor import AgentLoopError, LoopResult
from app.harness.agent_loop.trace import LoopTrace
from app.harness.llm.failures import model_failure_message, model_failure_reason
from app.harness.llm.provider_base import LLMCompletionError, LLMConfig
from app.storage.run_store import RunStore


def test_payment_failure_survives_checkpoint_result_and_bridge_without_private_content(tmp_path: Path) -> None:
    request = httpx.Request("POST", "https://example.invalid", headers={"authorization": "private-header"})
    response = httpx.Response(402, request=request)
    sdk_error = APIStatusError("private-exception-message", response=cast(Any, response),
                              body={"error": {"code": "invalid_request_error", "message": "private-body"}})
    reason = model_failure_reason(sdk_error, LLMConfig(provider="deepseek", model="configured-model"))
    assert reason == {"code": "provider_payment_required", "provider": "deepseek", "model": "configured-model",
                      "exception_type": "APIStatusError", "http_status": 402, "api_error_code": "invalid_request_error"}
    run = RunStore(tmp_path / "runs").create(task="failure-ledger", project="pimc", entrypoint="idea", user_request="contract")
    trace_root = run.root / "agent_traces" / "idea" / "failure-ledger"
    trace = LoopTrace(trace_root, "full")
    # Authored failure-state input tests durable serialization, not agent execution.
    state: dict[str, Any] = {"status": "model_error", "counts": {"model_requests": 1, "model_responses": 0},
                             "usage": {}, "usage_complete": False, "fingerprint": "failure-contract",
                             "pending": "model", "last_model_error": reason}
    trace.emit("model_error", {"error_type": type(sdk_error).__name__, "reason": reason})
    trace.snapshot(state)
    saved = json.loads((trace_root / "checkpoint.json").read_text())
    result = LoopResult("", saved["status"], [], saved["counts"], trace_root, error_reason=saved["last_model_error"])
    with pytest.raises(AgentLoopError) as caught:
        result.require_passed("idea")
    assert caught.value.status == "model_error"
    assert caught.value.reason == reason
    assert result.counts == {"model_requests": 1, "model_responses": 0}
    assert "HTTP 402" in str(caught.value) and "账户余额" in str(caught.value)
    _write_agent_failure_diagnostic(run=run, node_key="idea", agent="idea", phase="draft", exc=caught.value)
    archived = (run.subdir("events") / "agent_events.jsonl").read_text()
    diagnostic = json.loads(archived)["diagnostic"]
    assert diagnostic["code"] == "provider_payment_required"
    assert diagnostic["details"] == reason
    assert "HTTP 402" in diagnostic["message"]
    assert not (run.subdir("idea") / "idea_proposal.v1.md").exists()
    for text in (str(caught.value), json.dumps(saved), archived, (trace_root / "events.jsonl").read_text()):
        assert "private-" not in text


@pytest.mark.parametrize("status,code", [(401, "provider_authentication_failed"), (403, "provider_authentication_failed"),
                                         (429, "provider_rate_limited"), (500, "model_request_failed")])
def test_status_guidance_is_host_authored_and_rejects_message_shaped_codes(status: int, code: str) -> None:
    response = httpx.Response(status, request=httpx.Request("POST", "https://example.invalid"))
    error = APIStatusError("private-message", response=cast(Any, response),
                           body={"error": {"code": "private-message-with.dots", "message": "private-body"}})
    reason = model_failure_reason(error, LLMConfig(provider="openai", model="configured-model"))
    assert reason["code"] == code
    assert reason["http_status"] == status
    assert "api_error_code" not in reason
    assert "重试" in model_failure_message(reason)
    assert "private-" not in json.dumps(reason) + model_failure_message(reason)


def test_timeout_and_connection_failure_keep_distinct_actions() -> None:
    request = httpx.Request("POST", "https://example.invalid")
    config = LLMConfig(provider="openai", model="configured-model")
    for error in (APITimeoutError(request=cast(Any, request)), TimeoutError("private-timeout")):
        reason = model_failure_reason(error, config)
        assert reason["code"] == "provider_timeout"
        assert "超时" in model_failure_message(reason)
    reason = model_failure_reason(APIConnectionError(request=cast(Any, request)), config)
    assert reason["code"] == "provider_connection_failed"
    assert "无法连接" in model_failure_message(reason)


def test_completion_recovery_reason_keeps_its_existing_contract() -> None:
    error = LLMCompletionError(code="empty_final_content", provider="deepseek", model="configured-model",
                               finish_reason="stop", empty_final=True, usage={"total_tokens": 5})
    reason = model_failure_reason(error, LLMConfig(provider="deepseek", model="configured-model"))
    assert reason == error.reason
    assert "usage" not in reason


def test_other_terminal_failures_remain_failures_without_model_diagnostics(tmp_path: Path) -> None:
    result = LoopResult("", "budget_exhausted", [], {}, tmp_path)
    with pytest.raises(AgentLoopError, match="budget_exhausted") as caught:
        result.require_passed("idea")
    assert caught.value.reason is None
