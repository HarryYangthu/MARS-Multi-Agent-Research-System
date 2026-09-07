"""Resume an actual failed SDK request; no model, tool or service substitute."""
from __future__ import annotations

import json
import socket
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from app.agents.base import RunRequest
from app.agents.idea.agent import IdeaAgent
from app.harness.agent_loop.trace import atomic_json
from app.harness.llm.model_registry import get_agent_config
from scripts.idea_live_resume import audit_resumptions, exclusive_run, load_resume, record_resumption


def test_exclusive_run_rejects_a_second_holder_and_releases_on_exception(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="authored control flow"):
        with exclusive_run(tmp_path):
            with pytest.raises(ValueError, match="another evaluation"):
                with exclusive_run(tmp_path):
                    pytest.fail("a second lock holder was admitted")
            raise RuntimeError("authored control flow")
    with exclusive_run(tmp_path):
        assert (tmp_path / "evaluation.lock").is_file()


@pytest.mark.asyncio
async def test_real_failed_request_resume_retains_counters_and_verifiable_source_journal(tmp_path: Path) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        config = replace(get_agent_config("idea"), model_provider="local_vllm", model_name="unavailable-local-model",
                         api_key_env="", base_url_env="", base_url=f"http://127.0.0.1:{sock.getsockname()[1]}/v1",
                         request_timeout_seconds=0.3, max_retries=0, tools=(),
                         raw={"loop": {"max_model_calls": 3, "trace": "full"}})
        agent = IdeaAgent(agent_config=config)
        request = RunRequest(project="pimc", user_request="Actual connection-refusal resume test",
                             extra={"run_root": str(tmp_path), "run_id": tmp_path.name})
        initial = {"run_id": tmp_path.name, "loop_policy": asdict(agent.loop_policy)}
        atomic_json(tmp_path / "input/request.json", initial)
        atomic_json(tmp_path / "summary.json", {"run_id": tmp_path.name, "status": "prepared"})
        context = await agent.build_context(request)
        with pytest.raises(RuntimeError, match="model_error"):
            await agent.run_loop(request, context)
        loaded, _, checkpoint, before = load_resume(tmp_path)
        assert loaded == initial and before["counts"]["model_requests"] == 1
        journal = record_resumption(tmp_path, checkpoint, {"source_commit": "authored-journal-label", "source_dirty": False})
        assert json.loads((journal / "checkpoint.json").read_text()) == before
        request.extra["resume_invocation"] = checkpoint.parent.name
        with pytest.raises(RuntimeError, match="model_error"):
            await agent.run_loop(request, context)
    after = json.loads(checkpoint.read_text())
    assert after["counts"]["model_requests"] == after["counts"]["sdk_attempts"] == 2
    assert after["counts"]["model_responses"] == 0 and after["candidate"] == ""
    assert after["usage_complete"] is False
    rows, errors = audit_resumptions(tmp_path, checkpoint.parent)
    assert len(rows) == 1 and not errors
    # Real disk tampering must invalidate the preserved evidence.
    with (checkpoint.parent / "events.jsonl").open("r+b") as handle:
        handle.write(b"x")
    assert "pre-resumption events were changed" in audit_resumptions(tmp_path, checkpoint.parent)[1]


def test_missing_checkpoint_cannot_start_a_resumed_evaluation(tmp_path: Path) -> None:
    atomic_json(tmp_path / "input/request.json", {"run_id": tmp_path.name})
    atomic_json(tmp_path / "summary.json", {"run_id": tmp_path.name})
    with pytest.raises(ValueError, match="exactly one"):
        load_resume(tmp_path)
