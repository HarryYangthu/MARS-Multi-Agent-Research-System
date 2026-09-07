"""Deadline contracts and actual unavailable-endpoint failures for all LLM callers."""
from __future__ import annotations

import asyncio
import json
import socket
from dataclasses import replace
from pathlib import Path

import pytest
from openai import APIConnectionError

from app.agents.base import ContextPack, RunRequest
from app.agents.idea.agent import IdeaAgent
from app.agents.debate.debate_runner import DebateMode, run_debate
from app.bridge.commander import Commander
from app.bridge.commander_session import CommanderSession
from app.bridge.orchestrator import Orchestrator
from app.harness.llm.model_registry import get_agent_config
from app.harness.llm.provider_base import LLMConfig, llm_call_deadline_seconds
from app.storage.run_store import RunStore


def test_llm_deadline_covers_all_attempts_backoff_and_grace() -> None:
    config = LLMConfig(provider="deepseek", model="configured-model", request_timeout_seconds=120,
                       max_retries=3, retry_base_delay_seconds=1)
    assert llm_call_deadline_seconds(config, minimum_seconds=90) == 492
    assert llm_call_deadline_seconds(config, minimum_seconds=600) == 600
    config.max_retries = 999
    assert llm_call_deadline_seconds(config) == 492
    config.max_retries = -1
    assert llm_call_deadline_seconds(config) == 125


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", ["idea", "commander", "debate"])
async def test_actual_unavailable_endpoint_terminates_without_a_successful_answer(
    caller: str, tmp_path: Path,
) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        config = replace(get_agent_config("idea"), model_provider="local_vllm", model_name="unavailable-local-model",
                         api_key_env="", base_url_env="", base_url=f"http://127.0.0.1:{port}/v1",
                         request_timeout_seconds=0.3, max_retries=1, retry_base_delay_seconds=0,
                         thinking_enabled=False, reasoning_effort=None, debate_participants=(), tools=(),
                         raw={"loop": {"mode": "react", "trace": "full", "max_model_calls": 1}})
        request = RunRequest(project="pimc", user_request="Actual transport failure contract",
                             extra={"run_root": str(tmp_path)})
        if caller == "idea":
            agent = IdeaAgent(agent_config=config)
            context = await agent.build_context(request)
            with pytest.raises(RuntimeError, match="model_error"):
                await asyncio.wait_for(agent.run_loop(request, context), timeout=10)
            state = json.loads(next(tmp_path.glob("agent_traces/idea/*/checkpoint.json")).read_text())
            assert state["candidate"] == "" and state["history"] == []
            assert state["counts"]["sdk_attempts"] == 2 and state["counts"]["model_responses"] == 0
            assert state["usage_complete"] is False
        elif caller == "commander":
            store = RunStore(tmp_path / "runs")
            commander = Commander(orchestrator=Orchestrator(run_store=store), agent_config=config)
            session = CommanderSession(conv_id="deadline", project="pimc")
            with pytest.raises(APIConnectionError):
                await asyncio.wait_for(commander.handle_user_message(session, "Actual transport failure"), timeout=10)
            assert not [m for m in session.messages if m.role in {"assistant", "tool"}]
        else:
            progress = tmp_path / "debate.md"
            with pytest.raises(RuntimeError, match="debate role") as caught:
                await asyncio.wait_for(run_debate(
                    agent_name="idea", agent_config=config, request=request,
                    context=ContextPack(system="rules", project="pimc", task="Actual transport failure"),
                    output_schema="proposal.v1", mode=DebateMode.SINGLE_MODEL_SIMULATED,
                    progress_path=str(progress)), timeout=10)
            assert isinstance(caught.value.__cause__, APIConnectionError)
            assert "已完成" not in progress.read_text()
        assert not list(tmp_path.rglob("*.approved.md"))
