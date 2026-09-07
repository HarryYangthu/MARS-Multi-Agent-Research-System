"""Actual unavailable/exiting MCP processes must not fabricate tool results."""
from pathlib import Path
import shlex
import sys
from fastapi.testclient import TestClient
import pytest
from app.harness.tools.mcp_adapters import MCPTransportError, adapter_status, call_mcp_tool, list_mcp_tools
from app.main import app


@pytest.mark.asyncio
async def test_missing_mcp_executable_fails_before_any_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARS_MCP_GIT_COMMAND", str(tmp_path / "absent-mcp"))
    status = adapter_status("git")
    assert status.configured and not status.available
    with pytest.raises(MCPTransportError):
        await list_mcp_tools("git")
    with pytest.raises(MCPTransportError):
        await call_mcp_tool("git", tool_name="git_status", arguments={})


@pytest.mark.asyncio
async def test_actual_non_protocol_process_cannot_initialize(monkeypatch: pytest.MonkeyPatch) -> None:
    command = shlex.join([sys.executable, "-c", "raise SystemExit(3)"])
    monkeypatch.setenv("MARS_MCP_GIT_COMMAND", command)
    with pytest.raises(MCPTransportError):
        await list_mcp_tools("git", timeout_seconds=2)


def test_mcp_api_returns_failure_for_missing_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARS_MCP_GIT_COMMAND", str(tmp_path / "absent-mcp"))
    client = TestClient(app)
    listed = client.get("/api/tools/adapters/git/tools")
    called = client.post("/api/tools/adapters/git/call", json={"tool_name": "git_status", "arguments": {}})
    assert listed.status_code >= 400 and called.status_code >= 400
