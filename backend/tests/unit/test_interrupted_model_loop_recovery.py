"""Kill and resume the real loop during real connection-refusal backoff."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import multiprocessing
from pathlib import Path
import socket
import time
from typing import Any

import pytest
import yaml

from app.bridge.run_model_recovery import recover_interrupted_run_model_requests
from app.bridge.task_runtime import bind_task, resumable_task
from app.harness.agent_loop.executor import LoopInput, NativeAgentLoop
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.interrupted_model_resume import checkpoint_interrupted_model_receipt
from app.harness.agent_loop.trace import audit_trace
from app.harness.llm.openai_provider import CustomEndpointProvider
from app.harness.llm.provider_base import LLMConfig, Message
from app.harness.schema.validator import validate_document
from app.harness.tools.registry import ToolContext, ToolRegistry
from app.storage.run_store import RunHandle
from tests.unit.test_commander_model_recovery import _policy, _state


async def _validate(text: str, _observations: list[dict[str, Any]]) -> list[str]:
    return [error.message for error in validate_document(text, expected_schema="code_spec.v1").errors]


def _loop_process(root: str, invocation: str, correlation: dict[str, str], endpoint: str, resume: bool) -> None:
    async def execute() -> None:
        path = Path(root)
        provider = CustomEndpointProvider(api_key="real-connection-refusal-only", base_url=endpoint)
        try:
            await NativeAgentLoop().run(LoopInput(
                messages=[Message("user", "Exercise actual interrupted generation recovery; do not fabricate output.")],
                provider=provider, config=LLMConfig(provider="openai", model="test-model", max_tokens=64,
                    max_retries=1, retry_base_delay_seconds=20, request_timeout_seconds=0.2),
                registry=ToolRegistry(), tools=(),
                tool_context=ToolContext(run_id=path.name, project="test7", agent="coding", extra={"run_root": root}),
                policy=AgentLoopPolicy(max_model_calls=4), trace_root=path / "agent_traces/coding" / invocation,
                validate=_validate, correlation=correlation, resume=resume))
        finally:
            await provider.close()
    asyncio.run(execute())


def _wait_for_failed_attempt(trace: Path, model_requests: int) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            state = json.loads((trace / "checkpoint.json").read_text())
            events = [json.loads(line) for line in (trace / "events.jsonl").read_text().splitlines()]
            if (state["counts"]["model_requests"] == model_requests
                    and any(event["kind"] == "sdk_attempt_failed" and event.get("request") == model_requests
                            for event in events) and audit_trace(trace).get("consistent")):
                return
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(0.01)
    raise AssertionError("Actual loop did not reach real connection-refusal backoff")


def test_actual_loop_resumes_with_a_new_generation_and_retains_original_request_charges(tmp_path: Path) -> None:
    _policy(tmp_path)
    run = RunHandle(run_id=tmp_path.name, root=tmp_path, project="test7", task="actual-recovery",
                    entrypoint="coding", created_at="2026-10-21T00:00:00Z")
    task = bind_task(run, "coding", goal="Real refused-connection recovery", upstream={}, output_schema="code_spec.v1")
    correlation = {"trace_id": run.run_id, "node_id": "coding", "task_id": task.task_id,
                   "parent_task_id": task.parent_task_id, "invocation_id": task.invocation_id}
    trace = tmp_path / "agent_traces/coding" / task.invocation_id
    context = multiprocessing.get_context("spawn")
    children = []
    with socket.socket() as unserved:
        unserved.bind(("127.0.0.1", 0))  # Real bound non-listening socket, no service substitute.
        endpoint = f"http://127.0.0.1:{unserved.getsockname()[1]}/v1"
        try:
            first = context.Process(target=_loop_process,
                args=(str(tmp_path), task.invocation_id, correlation, endpoint, False))
            children.append(first)
            first.start()
            _wait_for_failed_attempt(trace, 1)
            first.terminate()
            first.join(timeout=10)
            assert first.exitcode is not None and first.exitcode != 0
            initial = _state(tmp_path)
            original_request, original_row = next(iter(initial["requests"].items()))
            checkpoint_before = json.loads((trace / "checkpoint.json").read_text())
            assert recover_interrupted_run_model_requests(run)["status"] == "recovered"
            assert resumable_task(run, "coding").invocation_id == task.invocation_id
            second = context.Process(target=_loop_process,
                args=(str(tmp_path), task.invocation_id, correlation, endpoint, True))
            children.append(second)
            second.start()
            _wait_for_failed_attempt(trace, 2)
            second.terminate()
            second.join(timeout=10)
            assert second.exitcode is not None and second.exitcode != 0
            final = _state(tmp_path)
            assert len(final["requests"]) == 2
            assert final["requests"][original_request]["status"] == "abandoned"
            assert final["requests"][original_request]["charged_tokens"] == original_row["charged_tokens"]
            assert final["requests"][original_request]["charged_attempts"] == original_row["charged_attempts"]
            state = json.loads((trace / "checkpoint.json").read_text())
            assert state["counts"]["model_requests"] == 2 and state["counts"]["model_responses"] == 0
            assert state["counts"]["tool_dispatches"] == 0 and state["candidate"] == ""
            assert state["history"] == checkpoint_before["history"] and not state["usage_complete"]
            events = [json.loads(line) for line in (trace / "events.jsonl").read_text().splitlines()]
            assert sum(event["kind"] == "interrupted_model_reconciled" for event in events) == 1
        finally:
            for child in children:
                if child.is_alive():
                    child.terminate()
                child.join(timeout=10)


def _request(root: Path, endpoint: str, *, retries: int = 0) -> tuple[RunHandle, LoopInput]:
    _policy(root)
    run = RunHandle(run_id=root.name, root=root, project="test7", task="network-recovery",
                    entrypoint="coding", created_at="2026-10-21T00:00:00Z")
    task = bind_task(run, "coding", goal="Real network failure recovery", upstream={}, output_schema="code_spec.v1")
    correlation = {"trace_id": run.run_id, "node_id": "coding", "task_id": task.task_id,
                   "parent_task_id": task.parent_task_id, "invocation_id": task.invocation_id}
    request = LoopInput(messages=[Message("user", "Exercise real transport failure; no success output is supplied.")],
        provider=CustomEndpointProvider(api_key="real-refused-only", base_url=endpoint),
        config=LLMConfig(provider="openai", model="test-model", max_tokens=64,
            max_retries=retries, retry_base_delay_seconds=20 if retries else 0, request_timeout_seconds=0.2),
        registry=ToolRegistry(), tools=(),
        tool_context=ToolContext(run_id=run.run_id, project=run.project, agent="coding", extra={"run_root": str(root)}),
        policy=AgentLoopPolicy(max_model_calls=4), trace_root=root / "agent_traces/coding" / task.invocation_id,
        validate=_validate, correlation=correlation)
    return run, request


@pytest.mark.asyncio
@pytest.mark.parametrize("request_limit", [1, 32])
async def test_actual_settled_transport_failure_can_resume_without_resetting_budget(
    tmp_path: Path, request_limit: int,
) -> None:
    with socket.socket() as unserved:
        unserved.bind(("127.0.0.1", 0))
        endpoint = f"http://127.0.0.1:{unserved.getsockname()[1]}/v1"
        run, request = _request(tmp_path, endpoint)
        path = tmp_path / "resources/model_budget.policy.yaml"
        policy = yaml.safe_load(path.read_text())
        policy["limits"]["max_model_requests"] = request_limit
        path.write_text(yaml.safe_dump(policy))
        first = await asyncio.wait_for(NativeAgentLoop().run(request), timeout=10)
        assert first.status == "model_error" and first.counts["model_responses"] == 0
        before = _state(tmp_path)
        identifier, charged = next(iter(before["requests"].items()))
        assert charged["status"] == "failed"
        checkpoint = json.loads((request.trace_root / "checkpoint.json").read_text())
        receipt = checkpoint_interrupted_model_receipt(request.trace_root, checkpoint, run_root=tmp_path)
        assert receipt and receipt["recovery_kind"] == "settled_model_failure"
        assert resumable_task(run, "coding")
        resumed = replace(request, resume=True,
            provider=CustomEndpointProvider(api_key="real-refused-only", base_url=endpoint))
        result = await asyncio.wait_for(NativeAgentLoop().run(resumed), timeout=10)
        after = _state(tmp_path)
        assert after["requests"][identifier] == charged
        assert result.counts["model_requests"] == 2 and result.counts["model_responses"] == 0
        assert result.counts["tool_dispatches"] == 0
        if request_limit == 1:
            assert result.status == "budget_exhausted" and len(after["requests"]) == 1
        else:
            assert result.status == "model_error" and len(after["requests"]) == 2


async def _wait_for_loop_backoff(task: asyncio.Task[Any], trace: Path, number: int) -> None:
    deadline = asyncio.get_running_loop().time() + 10
    while asyncio.get_running_loop().time() < deadline:
        if task.done():
            await task
            raise AssertionError("Loop ended before the actual transport backoff")
        try:
            state = json.loads((trace / "checkpoint.json").read_text())
            events = [json.loads(line) for line in (trace / "events.jsonl").read_text().splitlines()]
            if (state["counts"]["model_requests"] == number
                    and events[-1].get("kind") == "sdk_retry_scheduled" and audit_trace(trace)["consistent"]):
                return
        except (OSError, ValueError, KeyError):
            pass
        await asyncio.sleep(0.01)
    raise AssertionError("Actual loop did not enter connection-failure backoff")


@pytest.mark.asyncio
async def test_actual_cancelled_model_generation_resumes_without_replaying_a_tool(tmp_path: Path) -> None:
    with socket.socket() as unserved:
        unserved.bind(("127.0.0.1", 0))
        endpoint = f"http://127.0.0.1:{unserved.getsockname()[1]}/v1"
        run, request = _request(tmp_path, endpoint, retries=1)
        for number in (1, 2):
            current = request if number == 1 else replace(request, resume=True,
                provider=CustomEndpointProvider(api_key="real-refused-only", base_url=endpoint))
            task = asyncio.create_task(NativeAgentLoop().run(current))
            try:
                await _wait_for_loop_backoff(task, request.trace_root, number)
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            checkpoint = json.loads((request.trace_root / "checkpoint.json").read_text())
            assert checkpoint["status"] == "interrupted" and checkpoint["pending"] == "model"
            receipt = checkpoint_interrupted_model_receipt(request.trace_root, checkpoint, run_root=tmp_path)
            assert receipt and receipt["recovery_kind"] == "settled_model_failure"
            assert resumable_task(run, "coding")
        rows = list(_state(tmp_path)["requests"].values())
        assert len(rows) == 2
        assert all(row["status"] == "cancelled" and row["charged_tokens"] == row["reserved_tokens"]
                   and row["usage"] is None and not row["usage_complete"] for row in rows)
        assert checkpoint["counts"]["model_requests"] == 2 and checkpoint["counts"]["tool_dispatches"] == 0
