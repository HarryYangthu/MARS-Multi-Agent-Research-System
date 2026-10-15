"""Real material persistence and refused TCP requests; no model/service doubles."""
from __future__ import annotations

import json
from pathlib import Path
import socket
from typing import Any

import httpx
import pytest

from app.harness.agent_loop.executor import LoopInput
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.trace import LoopTrace
from app.harness.agent_loop.zcode.config import ZCodeConfig
from app.harness.agent_loop.zcode.executor import ZCodeLoopExecutor
from app.harness.agent_loop.zcode.gateway import ZCodeGateway
from app.harness.context.runtime_assembly import assemble_materials
from app.harness.context.runtime_pack import read_material
from app.harness.llm.accounting import RunModelBudget
from app.harness.llm.openai_provider import LocalVllmProvider
from app.harness.llm.provider_base import LLMConfig, Message
from app.harness.tools.registry import ToolContext, get_registry


async def reject_unrequested_submission(text: str, history: list[dict[str, Any]]) -> list[str]:
    raise AssertionError("These checks must not submit a model artifact")


def request_for(root: Path, *, endpoint: str = "http://127.0.0.1:1", retries: int = 0) -> LoopInput:
    metadata: dict[str, Any] = {"runtime_policy": {"optional_sections": []}}
    messages = assemble_materials([Message("user", "Resume the existing approved task")],
        {"human_revision_request": "Revision requested for the current draft. Feedback: 保留上游并修复。"}, metadata)
    return LoopInput(messages=messages, provider=LocalVllmProvider(base_url=endpoint),
        config=LLMConfig("local_vllm", "unavailable", max_tokens=16, max_retries=retries,
                         retry_base_delay_seconds=0, request_timeout_seconds=1),
        registry=get_registry(), tool_context=ToolContext("material-check", "local", "coding",
                                                         extra={"run_root": str(root)}),
        tools=(), policy=AgentLoopPolicy(), trace_root=root / "agent_traces/coding/check",
        validate=reject_unrequested_submission, final_schema={"type": "object"}, context_metadata=metadata)


def test_new_zcode_checkpoint_has_readable_exact_revision_material(tmp_path: Path) -> None:
    request = request_for(tmp_path)
    state = ZCodeLoopExecutor()._state(request, "material-input")
    for ref, descriptor in state["context_metadata"]["materials"].items():
        source = read_material(tmp_path, ref, 0, 10000)
        assert source["content"] in [message.content for message in request.messages]
        assert source["truncated"] is False
        if descriptor["source"] == "human_revision_request":
            assert source["content"].endswith("Feedback: 保留上游并修复。")
    # Recreating the same input cannot change the persisted content.
    files = {path: path.read_bytes() for path in (tmp_path / "context/materials").glob("*.json")}
    ZCodeLoopExecutor()._state(request, "material-input")
    assert all(path.read_bytes() == content for path, content in files.items())
    assert state["counts"]["model_requests"] == 0


def test_zcode_does_not_overwrite_corrupted_revision_material(tmp_path: Path) -> None:
    request = request_for(tmp_path)
    ZCodeLoopExecutor()._state(request, "material-input")
    ref = next(ref for ref, item in request.context_metadata["materials"].items()
               if item["source"] == "human_revision_request")
    path = tmp_path / f"context/materials/{ref}.json"
    path.write_text('{"sha256":"changed","message":{}}', encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="material hash mismatch"):
        ZCodeLoopExecutor()._state(request, "material-input")
    assert path.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("retries", [0, 2])
async def test_gateway_obeys_bounded_retries_and_accounts_real_refused_connections(
    tmp_path: Path, retries: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Reserve a real non-listening local socket, preventing another server from
    # occupying the port. Every model request encounters actual connection refusal.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        endpoint = f"http://127.0.0.1:{unavailable.getsockname()[1]}/v1"
        request = request_for(tmp_path, endpoint=endpoint, retries=retries)
        config = ZCodeConfig.load()
        state = ZCodeLoopExecutor(config)._state(request, "refused-connection")
        trace = LoopTrace(request.trace_root, "full")
        gateway = ZCodeGateway(request, config, "local-retry-check", state, trace)
        await gateway.start()
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=30) as client:
                response = await client.post(gateway.endpoint + "/v1/chat/completions",
                    headers={"Authorization": "Bearer local-retry-check"},
                    json={"messages": [{"role": "user", "content": "Actual connection failure check"}]})
            assert response.status_code == 502
            assert state["status"] == "model_error"
            assert state["counts"]["sdk_attempts"] == retries + 1
            assert state["counts"]["model_responses"] == state["counts"]["tool_dispatches"] == 0
            events = [json.loads(line) for line in (request.trace_root / "events.jsonl").read_text().splitlines()]
            assert len([e for e in events if e["kind"] == "sdk_retry_scheduled"]) == retries
            ledger = RunModelBudget(tmp_path).recovery_snapshot()
            rows = list(ledger["requests"].values())
            assert len(rows) == 1
            assert rows[0]["observed_attempts"] == rows[0]["charged_attempts"] == retries + 1
            assert rows[0]["attempts_complete"] is True and rows[0]["status"] == "failed"
        finally:
            await gateway.close()
            await request.provider.close()
