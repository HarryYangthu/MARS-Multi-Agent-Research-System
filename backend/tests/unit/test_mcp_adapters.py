from __future__ import annotations

from pathlib import Path
import sys

from fastapi.testclient import TestClient
import pytest

from app.harness.tools.mcp_adapters import adapter_status, call_mcp_tool, list_mcp_tools
from app.main import app


def _fake_mcp_server(tmp_path: Path) -> Path:
    server = tmp_path / "fake_mcp_server.py"
    server.write_text(
        """
import json
import sys

for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if "id" not in msg:
        continue
    if method == "initialize":
        result = {
            "protocolVersion": msg["params"]["protocolVersion"],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake", "version": "1"},
        }
    elif method == "tools/list":
        result = {"tools": [{"name": "status", "description": "status tool"}]}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": msg["params"]["name"]}]}
    else:
        result = {}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\\n")
    sys.stdout.flush()
""".strip(),
        encoding="utf-8",
    )
    return server


@pytest.mark.asyncio
async def test_stdio_mcp_transport_lists_and_calls_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = _fake_mcp_server(tmp_path)
    monkeypatch.setenv("MARS_MCP_GIT_COMMAND", f"{sys.executable} {server}")

    status = adapter_status("git")
    listed = await list_mcp_tools("git")
    called = await call_mcp_tool("git", tool_name="status", arguments={})

    assert status.configured is True
    assert status.available is True
    assert listed["tools"] == [{"name": "status", "description": "status tool"}]
    assert called["content"] == [{"type": "text", "text": "status"}]


def test_mcp_adapter_api_lists_and_calls_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = _fake_mcp_server(tmp_path)
    monkeypatch.setenv("MARS_MCP_GIT_COMMAND", f"{sys.executable} {server}")
    client = TestClient(app)

    listed = client.get("/api/tools/adapters/git/tools")
    called = client.post(
        "/api/tools/adapters/git/call",
        json={"tool_name": "status", "arguments": {}},
    )

    assert listed.status_code == 200
    assert listed.json()["result"]["tools"] == [{"name": "status", "description": "status tool"}]
    assert called.status_code == 200
    assert called.json()["result"]["content"] == [{"type": "text", "text": "status"}]
