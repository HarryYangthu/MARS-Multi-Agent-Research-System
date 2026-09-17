"""Resume guards exercised against checkpoints from actual refused connections."""
from __future__ import annotations

import asyncio
import json
import socket
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from app.harness.agent_loop.executor import (
    CONTEXT_FORMAT_VERSION, LoopInput, NativeAgentLoop, generation_fingerprint,
)
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.llm.openai_provider import LocalVllmProvider
from app.harness.llm.provider_base import LLMConfig, Message
from app.harness.schema.validator import validate_document
from app.harness.tools.registry import ToolContext, ToolRegistry


async def _validate(text: str, observations: list[dict[str, Any]]) -> list[str]:
    result = validate_document(text, expected_schema="proposal.v1")
    return [error.message for error in result.errors]


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["author", "reviewer"])
async def test_generation_changes_refuse_real_failed_checkpoint_before_new_request(
    tmp_path: Path, role: str,
) -> None:
    # A bound, non-listening real socket produces transport failure; there is
    # no service substitute, fabricated model output, or authored checkpoint.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        endpoint = f"http://127.0.0.1:{reserved.getsockname()[1]}/v1"
        provider = LocalVllmProvider(base_url=endpoint)
        reviewer = LocalVllmProvider(base_url=endpoint)
        config = LLMConfig(provider="local_vllm", model="unavailable-local-model", max_tokens=64,
                           request_timeout_seconds=0.3, max_retries=0, retry_base_delay_seconds=0)
        request = LoopInput(
            messages=[Message("user", "Actual connection-refusal configuration guard.")],
            provider=provider, config=config, registry=ToolRegistry(), tools=(),
            tool_context=ToolContext(run_id="resume-guard", project="pimc", agent="idea",
                                     extra={"run_root": str(tmp_path)}),
            policy=AgentLoopPolicy(mode="reflection", max_model_calls=3),
            trace_root=tmp_path / "trace", validate=_validate,
            review_provider=reviewer, review_config=replace(config),
        )
        try:
            result = await asyncio.wait_for(NativeAgentLoop().run(request), timeout=10)
            assert result.status == "model_error"
            paths = [request.trace_root / filename
                     for filename in ("checkpoint.json", "events.jsonl", "facts.json")]
            paths.append(tmp_path / "resources/model_budget.v1.json")
            before = {path: path.read_bytes() for path in paths}
            checkpoint = json.loads(before[paths[0]])
            assert checkpoint["context_format_version"] == CONTEXT_FORMAT_VERSION
            assert checkpoint["counts"]["model_requests"] == checkpoint["counts"]["sdk_attempts"] == 1
            assert checkpoint["counts"]["model_responses"] == 0 and checkpoint["candidate"] == ""
            changes: list[dict[str, Any]] = [
                {"temperature": 0.2}, {"max_tokens": 65}, {"top_p": 0.8},
                {"thinking_enabled": True}, {"reasoning_effort": "low"},
                {"json_mode": True}, {"response_schema": "proposal.v1"},
                {"max_retries": 1}, {"request_timeout_seconds": 0.4},
                {"retry_base_delay_seconds": 0.1}, {"extra": {"native_observation_history": True}},
            ]
            for change in changes:
                changed = replace(config, **change)
                resumed = (replace(request, resume=True, config=changed) if role == "author"
                           else replace(request, resume=True, review_config=changed))
                with pytest.raises(ValueError, match="identical inputs/configuration"):
                    await NativeAgentLoop().run(resumed)
                assert {path: path.read_bytes() for path in paths} == before

            alternate = LocalVllmProvider(base_url=endpoint + "/changed")
            try:
                resumed = (replace(request, resume=True, provider=alternate) if role == "author"
                           else replace(request, resume=True, review_provider=alternate))
                with pytest.raises(ValueError, match="identical inputs/configuration"):
                    await NativeAgentLoop().run(resumed)
                assert {path: path.read_bytes() for path in paths} == before
            finally:
                await alternate.close()
        finally:
            await provider.close()
            await reviewer.close()


def test_generation_identity_does_not_depend_on_credentials_or_observer() -> None:
    config = LLMConfig(provider="local_vllm", model="local-model")
    first = LocalVllmProvider(base_url="http://127.0.0.1:1/v1", api_key="first-not-a-credential")
    second = LocalVllmProvider(base_url="http://127.0.0.1:1/v1", api_key="second-not-a-credential")
    changed = replace(config, attempt_observer=lambda _kind, _data: None)
    assert generation_fingerprint(config, first) == generation_fingerprint(changed, second)
