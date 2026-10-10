"""Pure wire parsing and actual process/MCP/file boundaries; no service doubles."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Iterator

import httpx
import pytest
import yaml

from app.bridge.research_branch import research_branch_scope
from app.harness.agent_loop.executor import LoopInput
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.trace import LoopTrace
from app.harness.agent_loop.zcode.config import ZCodeConfig, model_config, runtime_environment, runtime_identity
from app.harness.agent_loop.zcode.executor import ZCodeLoopExecutor, session_identity
from app.harness.agent_loop.zcode.gateway import ZCodeGateway, protocol_tool_name, stream_envelope
from app.harness.agent_loop.zcode.protocol import ZCodeClient
from app.harness.llm.openai_provider import LocalVllmProvider
from app.harness.llm.accounting import RunModelBudget
from app.harness.llm.provider_base import LLMConfig, Message
from app.harness.project_workspace import open_folder
from app.harness.tools.git_branch import git
from app.harness.tools.registry import ToolContext, get_registry
from app.settings import reset_settings_cache
from app.storage.run_store import RunHandle


@contextmanager
def environment(values: dict[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    reset_settings_cache()
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        reset_settings_cache()


def test_wire_parser_keeps_actual_tool_ids_finish_and_usage() -> None:
    # Parsing a supplied envelope is a pure test, not a model execution claim.
    supplied = {"id": "reply1", "model": "glm-5.3", "created": 1,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call1", "type": "function", "function": {"name": "mcp__mars__read", "arguments": '{"path":"main.py"}'}}]},
            "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}}
    chunks = [json.loads(row[6:]) for row in stream_envelope(supplied).splitlines()
              if row.startswith("data: {")]
    assert chunks[0]["choices"][0]["delta"]["tool_calls"][0]["id"] == "call1"
    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"
    assert chunks[-1]["usage"] == supplied["usage"]
    assert protocol_tool_name("mars_code__repo_reader") == "mcp__mars__mars_code_repo_reader"
    assert session_identity({"session": {"sessionId": "sess_identity"}}) == "sess_identity"
    assert session_identity({"id": "unrelated"}) == ""


def test_private_config_contains_only_loopback_token_and_runtime_hash(tmp_path: Path) -> None:
    provider = model_config("glm-5.3", 8192, "http://127.0.0.1:10000", "private-local-token", context_window=128000)
    env = runtime_environment(tmp_path, provider=provider)
    stored = json.loads((tmp_path / "provider.json").read_text())
    rule = stored["config"]["providerConfigRules"]["providerRules"][0]["config"]
    assert rule["access"]["apiKey"] == "private-local-token"
    assert rule["api"]["baseUrl"] == "http://127.0.0.1:10000/v1"
    assert not any("API_KEY" in key for key in env)
    assert (tmp_path / "provider.json").stat().st_mode & 0o777 == 0o600
    binary = tmp_path / "runtime"; binary.write_text("runtime source")
    first = runtime_identity((str(binary),))
    binary.write_text("updated runtime source")
    assert runtime_identity((str(binary),)) != first


def test_private_home_preserves_personal_plugins_and_discards_startup_overrides(tmp_path: Path) -> None:
    personal_home = tmp_path / "personal"
    personal_config = personal_home / ".zcode/cli/config.json"
    personal_config.parent.mkdir(parents=True)
    original = json.dumps({"plugins": {"enabledPlugins": {"personal-plugin": True}}})
    personal_config.write_text(original)
    with environment({"HOME": str(personal_home), "USERPROFILE": str(personal_home),
                      "ZCODE_PLUGIN_ROOT": str(personal_home / "plugins"), "ZCODE_ENV": "beta",
                      "NODE_OPTIONS": "--trace-warnings"}):
        private_root = tmp_path / "invocation"
        env = runtime_environment(private_root, provider={})
        assert os.environ["HOME"] == str(personal_home)
        assert env["HOME"] == env["USERPROFILE"] == str(private_root / "home")
        assert env["APPDATA"].startswith(env["HOME"])
        assert env["XDG_CONFIG_HOME"].startswith(env["HOME"])
        assert not {"ZCODE_PLUGIN_ROOT", "ZCODE_ENV", "NODE_OPTIONS"} & env.keys()
        owned = json.loads((Path(env["HOME"]) / ".zcode/cli/config.json").read_text())
        assert owned["plugins"]["enabled"] is False
        assert owned["mcp"]["servers"] == {}
        assert personal_config.read_text() == original


@pytest.mark.asyncio
async def test_official_runtime_actual_stdio_handshake_and_process_cleanup(tmp_path: Path) -> None:
    config = ZCodeConfig.load()
    try:
        command = config.resolve_command()
    except ValueError:
        pytest.skip("Official ZCode runtime is not installed")
    provider = model_config("glm-5.3", 8192, "http://127.0.0.1:1", "protocol-check-only", context_window=128000)
    client = ZCodeClient(command=command, cwd=tmp_path, env=runtime_environment(tmp_path, provider=provider),
                         timeout=config.rpc_timeout_seconds, max_line_bytes=config.max_protocol_line_bytes)
    try:
        await client.start()
        capabilities = await client.call("runtime/capabilities", {})
        assert isinstance(capabilities, dict)
        assert client.process is not None and client.process.returncode is None
    finally:
        await client.close()
    assert client.process is not None and client.process.returncode is not None
    # ZCode uses Node's real homedir for CLI settings and its session database.
    # Verify that the child, not the parent Python process, resolves it privately.
    if Path(command[0]).stem == "node":
        env = runtime_environment(tmp_path, provider=provider)
        result = subprocess.run([command[0], "-e", "process.stdout.write(require('node:os').homedir())"],
                                env=env, check=True, capture_output=True, text=True)
        assert result.stdout == env["HOME"]


@pytest.mark.asyncio
async def test_real_mcp_auth_write_scope_stop_and_budget(tmp_path: Path) -> None:
    with environment({"MARS_FOLDER_PROJECTS_REGISTRY": str(tmp_path / "registry.json")}):
        project = open_folder(str(tmp_path / "project"), create=True)
        source = tmp_path / "repo"; source.mkdir()
        (source / "main.py").write_text("VALUE = 1\n")
        (source / "baseline").mkdir(); (source / "baseline/reference.py").write_text("VALUE = 1\n")
        git(source, "init", "-b", "baseline"); git(source, "add", ".")
        git(source, "-c", "user.name=Test", "-c", "user.email=test@localhost", "-c", "commit.gpgsign=false",
            "commit", "-m", "Actual test baseline")
        (project.metadata_root / "repo_link.yaml").write_text(yaml.safe_dump({
            "repo_path": str(source), "read_only": True, "allowed_paths": ["main.py"], "protected_paths": ["baseline/"]}))
        root = tmp_path / "run"; root.mkdir()
        run = RunHandle("zcode_boundary", root, project.name, "coding boundary", "coding", "2026-10-06T00:00:00Z")

        async def validate(text: str, history: list[dict[str, Any]]) -> list[str]:
            # This test never submits a candidate or substitutes model output.
            return ["No candidate admission in this boundary test"]

        request = LoopInput(messages=[Message("user", "Actual MCP boundary check")],
            provider=LocalVllmProvider(base_url="http://127.0.0.1:1"), config=LLMConfig("local_vllm", "unavailable"),
            registry=get_registry(), tool_context=ToolContext(run.run_id, project.name, "coding", extra={"run_root": str(root)}),
            tools=("code.repo_reader", "code.write_file"), policy=AgentLoopPolicy(), trace_root=root / "trace", validate=validate)
        config = replace(ZCodeConfig.load(), max_tool_calls=3, max_model_calls=1)
        state = ZCodeLoopExecutor(config)._state(request, "boundary")
        gateway = ZCodeGateway(request, config, "private-boundary-token", state, LoopTrace(request.trace_root, "full"))
        with research_branch_scope(run, "coding"):
            await gateway.start()
            try:
                async with httpx.AsyncClient() as client:
                    def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
                        return {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
                    endpoint = gateway.endpoint + "/mcp"
                    auth = {"Authorization": "Bearer private-boundary-token"}
                    denied = await client.post(endpoint, json=call("mars_code__write_file", {"path": "main.py", "content": "VALUE = 9\n"}))
                    assert denied.status_code == 401 and (source / "main.py").read_text() == "VALUE = 1\n"
                    outside = await client.post(endpoint, headers=auth,
                        json=call("mars_code__write_file", {"path": "other.py", "content": "VALUE = 9\n"}))
                    assert outside.json()["result"]["isError"] and not (source / "other.py").exists()
                    written = await client.post(endpoint, headers=auth,
                        json=call("mars_code__write_file", {"path": "main.py", "content": "VALUE = 2\n"}))
                    assert not written.json()["result"]["isError"]
                    assert (source / "main.py").read_text() == "VALUE = 2\n"
                    assert (source / "baseline/reference.py").read_text() == "VALUE = 1\n"
                    # Budget rejects before the unavailable model endpoint; it
                    # must neither call a model nor produce a success receipt.
                    state["counts"]["model_requests"] = config.max_model_calls
                    limited = await client.post(gateway.endpoint + "/v1/chat/completions", headers=auth,
                        json={"messages": [{"role": "user", "content": "Not admitted"}]})
                    assert limited.status_code == 429 and state["counts"]["sdk_attempts"] == 0
                    stopped = await client.post(endpoint, headers=auth,
                        json=call("mars_code__write_file", {"path": "main.py", "content": "VALUE = 3\n"}))
                    assert stopped.status_code == 409 and (source / "main.py").read_text() == "VALUE = 2\n"
            finally:
                await gateway.close()
        assert git(source, "show", "baseline:main.py") == "VALUE = 1"


@pytest.mark.asyncio
async def test_real_gateway_reports_shared_budget_block_without_sending_a_model_request(tmp_path: Path) -> None:
    root = tmp_path / "run"
    resource_dir = root / "resources"
    resource_dir.mkdir(parents=True)
    configuration = RunModelBudget(root).configuration
    configuration["limits"]["max_model_requests"] = 1
    (resource_dir / "model_budget.policy.yaml").write_text(yaml.safe_dump(configuration))
    budget = RunModelBudget(root)
    config = LLMConfig("local_vllm", "unavailable", max_tokens=16, max_retries=0)
    reservation = budget.reserve([Message("user", "Actual reservation")], config, {"invocation_id": "old"})
    budget.settle(reservation, usage=None, complete=False, outcome="cancelled")
    before = budget.path.read_bytes()

    async def validate(text: str, history: list[dict[str, Any]]) -> list[str]:
        return ["No candidate admission in this boundary test"]

    request = LoopInput(messages=[Message("user", "Must be blocked before network")],
        provider=LocalVllmProvider(base_url="http://127.0.0.1:1"), config=config,
        registry=get_registry(), tool_context=ToolContext("budget-check", "unbound", "coding",
            extra={"run_root": str(root)}), tools=(), policy=AgentLoopPolicy(),
        trace_root=root / "trace", validate=validate, correlation={"invocation_id": "new"})
    zconfig = ZCodeConfig.load()
    state = ZCodeLoopExecutor(zconfig)._state(request, "budget-boundary")
    gateway = ZCodeGateway(request, zconfig, "private-budget-token", state, LoopTrace(request.trace_root, "full"))
    await gateway.start()
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(gateway.endpoint + "/v1/chat/completions",
                headers={"Authorization": "Bearer private-budget-token"},
                json={"messages": [{"role": "user", "content": "Not admitted"}]})
        assert response.status_code == 429
        error = response.json()["error"]
        assert error["type"] == "mars_resource_budget"
        assert error["request_sent"] is False
        assert "used=1, limit=1, required=1" in error["message"]
        assert state["status"] == "blocked" and state["usage_complete"] is True
        assert state["counts"]["sdk_attempts"] == state["counts"]["model_responses"] == 0
        assert budget.path.read_bytes() == before
    finally:
        await gateway.close()
        await request.provider.close()
