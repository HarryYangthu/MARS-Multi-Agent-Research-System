"""Real file tools and actual configuration verify Harness boundaries."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from app.harness.tools.code import repo_reader_tool
from app.harness.tools.config import ToolConfig
from app.harness.tools.mcp_adapters import validate_mcp_config
from app.harness.tools.registry import ToolContext, ToolPolicy, ToolRegistry, ToolSpec, _approval_is_valid


@pytest.mark.asyncio
async def test_output_contract_checks_real_file_read_before_observation(tmp_path: Path) -> None:
    (tmp_path / "source.py").write_text("VALUE = 4\n")
    (tmp_path / "run/events").mkdir(parents=True)
    registry = ToolRegistry()
    registry.register("contract.read", repo_reader_tool, spec=ToolSpec(
        name="contract.read", namespace="contract", description="actual source read",
        output_schema={"type": "object", "required": ["content"], "properties": {"content": {"type": "integer"}}},
    ))
    ctx = ToolContext(run_id="contract-run", project="actual-folder", agent="bridge",
                      extra={"project_repo_root": str(tmp_path), "run_root": str(tmp_path / "run")})
    result = await registry.dispatch("contract.read", {"path": "source.py"}, ctx)
    assert not result.ok and result.status == "output_validation_error"
    assert result.output is None
    assert result.metadata["output_validation_path"] == ["content"]
    assert "output_validation_error" in (tmp_path / "run/events/tool_calls.jsonl").read_text()


@pytest.mark.asyncio
async def test_isolation_policy_blocks_actual_reader(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register("contract.isolated_read", repo_reader_tool, spec=ToolSpec(
        name="contract.isolated_read", namespace="contract", description="requires isolated backend",
        policy=ToolPolicy(require_isolation=True),
    ))
    result = await registry.dispatch("contract.isolated_read", {"path": "absent.py"},
                                    ToolContext(run_id="r", project="p", agent="bridge"))
    assert not result.ok and "OS isolation backend unavailable" in str(result.error)


def test_mcp_host_binding_cannot_silently_grant_write_access() -> None:
    config = ToolConfig(mcp_kind="filesystem", mcp_tool="write_file", mutation_level="write",
                        allowed_agents=("coding",), input_schema={"type": "object"},
                        output_schema={"type": "object"})
    with pytest.raises(ValueError, match="require approval"):
        validate_mcp_config("mcp.fs_write", config)
    validate_mcp_config("mcp.fs_write", replace(config, requires_approval=True))
    with pytest.raises(ValueError, match="mcp.\\* name"):
        validate_mcp_config("code.apply_patch", replace(config, requires_approval=True))
    with pytest.raises(ValueError, match="explicit allowed_agents"):
        validate_mcp_config("mcp.fs_write", replace(config, requires_approval=True, allowed_agents=()))


def test_approval_is_bound_to_project_and_exact_arguments(tmp_path: Path) -> None:
    approval_dir = tmp_path / "events/tool_approvals"
    approval_dir.mkdir(parents=True)
    (approval_dir / "a1.json").write_text(json.dumps({
        "status": "approved", "tool": "mcp.fs_write", "run_id": "r", "project": "p",
        "args": {"path": "allowed.py", "content": "value"},
    }))
    ctx = ToolContext(run_id="r", project="p", agent="bridge", approval_mode="approved", extra={"run_root": str(tmp_path)})
    args = {"_approval_id": "a1", "path": "allowed.py", "content": "value"}
    assert _approval_is_valid(tool_name="mcp.fs_write", args=args, ctx=ctx)
    assert not _approval_is_valid(tool_name="mcp.fs_write", args={**args, "path": "different.py"}, ctx=ctx)
    assert not _approval_is_valid(tool_name="mcp.fs_write", args=args, ctx=replace(ctx, project="other"))
    assert not _approval_is_valid(tool_name="mcp.fs_write", args={**args, "_approval_id": "../a1"}, ctx=ctx)


@pytest.mark.asyncio
async def test_strict_input_schema_accepts_only_trusted_approval_replay(tmp_path: Path) -> None:
    (tmp_path / "source.py").write_text("VALUE = 4\n")
    (tmp_path / "run/events").mkdir(parents=True)
    registry = ToolRegistry()
    registry.register("contract.approved_read", repo_reader_tool, spec=ToolSpec(
        name="contract.approved_read", namespace="contract", description="approval-required actual read",
        policy=ToolPolicy(requires_approval=True),
        input_schema={"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}},
                      "additionalProperties": False},
    ))
    ctx = ToolContext(run_id="r", project="actual-folder", agent="bridge",
                      extra={"project_repo_root": str(tmp_path), "run_root": str(tmp_path / "run")})
    pending = await registry.dispatch("contract.approved_read", {"path": "source.py"}, ctx)
    assert pending.requires_approval
    approval_id = pending.metadata["approval_id"]
    record_path = tmp_path / "run/events/tool_approvals" / f"{approval_id}.json"
    record = json.loads(record_path.read_text())
    record["status"] = "approved"
    record_path.write_text(json.dumps(record))
    approved = await registry.dispatch("contract.approved_read", {"path": "source.py", "_approval_id": approval_id},
                                       replace(ctx, approval_mode="approved"))
    assert approved.ok and approved.output["content"] == "VALUE = 4\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"tool_name": "delete_file"},
    {"run_id": "r", "agent": "bridge", "tool_name": "delete_file"},
    {"run_id": "r", "agent": "system", "tool_name": "delete_file"},
])
async def test_mcp_api_rejects_missing_context_and_privileged_agent(payload: dict[str, str]) -> None:
    from fastapi import HTTPException
    from app.api.tools import call_adapter_mcp_tool
    with pytest.raises(HTTPException) as caught:
        await call_adapter_mcp_tool("filesystem", payload)
    assert caught.value.status_code in {403, 422}
