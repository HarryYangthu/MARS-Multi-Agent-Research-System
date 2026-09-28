"""Real registry/configuration/HTTP inspection; no execution substitutes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from typing import Any

import httpx
import pytest
import yaml

from app.bridge.capability_catalog_service import _skills, _tool
from app.harness.tools.code import repo_reader_tool, write_file_tool
from app.harness.tools.config import ToolConfig
from app.harness.tools.registry import ToolRegistry
from tests.unit.test_cli_runtime_client import LiveBackend, ROOT, backend as backend
from tests.unit.test_research_run_admission import files


def _http(backend: LiveBackend) -> httpx.Client:
    return httpx.Client(base_url=backend.origin, headers={"X-MARS-Desktop-Token": backend.token},
                        trust_env=False, timeout=20)


def test_actual_http_catalog_does_not_execute_or_certify(backend: LiveBackend) -> None:
    before = files(backend.runtime)
    with _http(backend) as client:
        response = client.get("/api/capabilities")
        assert response.status_code == 200, response.text
        actual = response.json()
        assert client.get("/api/capabilities").json() == actual
    assert actual["probes_started"] is False and actual["research_started"] is False
    assert actual["certification_validation_available"] is False
    assert len(actual["context_sha256"]) == 64
    assert not actual["agent_configuration_drift"]
    tools = {item["name"]: item for item in actual["tools"]}
    assert tools["code.repo_reader"]["registered"]
    assert tools["code.repo_reader"]["contract_adapter"] == "requires_host_scope"
    assert tools["search.arxiv_search"]["contract_adapter"] == "unsupported"
    assert tools["idea.research_delegate"]["origin"] == "runtime_bound"
    assert tools["idea.research_delegate"]["effective_spec_present"] is False
    assert not any(item["kind"] == "mcp_binding" for item in actual["tools"])
    assert {item["contract_adapter"] for item in actual["skills"]} == {"unsupported", "requires_host_scope"}
    for item in actual["tools"] + actual["skills"]:
        assert item["dependency_status"] == "unknown" and item["certification_status"] == "not_run"
        assert item["execution_authorized"] is False
    assert files(backend.runtime) == before
    assert backend.token not in response.text and str(backend.runtime) not in response.text


def test_actual_http_requires_owner_session(backend: LiveBackend) -> None:
    with httpx.Client(base_url=backend.origin, trust_env=False) as client:
        assert client.get("/api/capabilities").status_code == 401


def test_live_tool_changes_report_drift_without_reset_or_secret_output(backend: LiveBackend) -> None:
    path = backend.runtime / "configs/tools.yaml"
    original = path.read_bytes()
    with _http(backend) as client:
        first = client.get("/api/capabilities").json()
        prior = next(row for row in first["tools"] if row["name"] == "code.repo_reader")
        raw = yaml.safe_load(original)
        settings = raw["tools"]["code.repo_reader"]
        secret = secrets.token_urlsafe(40)
        settings.update({"enabled": False, "timeout_seconds": 0.123,
                         "description": secret, "command_allowlist": [["/private/credential-path", secret]]})
        try:
            path.write_text(yaml.safe_dump(raw))
            response = client.get("/api/capabilities")
            assert response.status_code == 200
            actual = response.json()
            changed = next(row for row in actual["tools"] if row["name"] == "code.repo_reader")
            assert changed["configured_enabled"] is False and changed["dispatch_enabled"] is False
            assert changed["drift_status"] == "detected" and "policy.timeout_seconds" in changed["drift_fields"]
            assert changed["effective_spec_sha256"] == prior["effective_spec_sha256"]
            assert changed["configured_sha256"] != prior["configured_sha256"]
            assert actual["context_sha256"] != first["context_sha256"]
            assert secret not in response.text and "/private/credential-path" not in response.text
            assert all(not role["policy_intersection"] for role in changed["roles"])
        finally:
            path.write_bytes(original)
        assert client.get("/api/capabilities").json() == first


def test_live_agent_changes_remain_distinct_from_cached_owner(backend: LiveBackend) -> None:
    path = backend.runtime / "configs/agents.yaml"
    original = path.read_bytes()
    with _http(backend) as client:
        first = client.get("/api/capabilities").json()
        raw = yaml.safe_load(original)
        raw["coding"]["enabled"] = False
        raw["coding"]["tools"] = []
        try:
            path.write_text(yaml.safe_dump(raw))
            actual = client.get("/api/capabilities").json()
            assert actual["agent_configuration_drift"]
            assert actual["effective_agents_sha256"] == first["effective_agents_sha256"]
            row = next(item for item in actual["tools"] if item["name"] == "code.repo_reader")
            role = next(item for item in row["roles"] if item["role"] == "coding")
            assert role["configured_enabled"] is False and role["effective_enabled"] is True
            assert role["configured_tool_granted"] is False and role["effective_tool_granted"] is True
            assert role["execution_authorized"] is False
        finally:
            path.write_bytes(original)


def test_invalid_host_config_returns_safe_error(backend: LiveBackend) -> None:
    path = backend.runtime / "configs/tools.yaml"
    original = path.read_bytes()
    secret = secrets.token_urlsafe(40)
    try:
        path.write_text("tools: [\n" + secret)
        with _http(backend) as client:
            response = client.get("/api/capabilities")
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "capability_catalog_unavailable"
        assert secret not in response.text and str(path) not in response.text
    finally:
        path.write_bytes(original)


def test_real_handler_identity_not_tool_name_defines_contract_projection() -> None:
    registry = ToolRegistry()
    registry.register("code.repo_reader", repo_reader_tool)
    configuration = {"code.repo_reader": ToolConfig()}
    actual = _tool("code.repo_reader", registry, configuration, [], {})
    assert actual.contract_adapter == "requires_host_scope"
    # Two real implementations are registered, but neither is executed.
    registry.register("code.repo_reader", write_file_tool, override=True)
    changed = _tool("code.repo_reader", registry, configuration, [], {})
    assert changed.contract_adapter == "unsupported"
    assert changed.handler_sha256 != actual.handler_sha256


def test_bridge_declaration_is_not_a_registered_spec() -> None:
    row = _tool("run.catalog_test", ToolRegistry(), {"run.catalog_test": ToolConfig(bridge_only=True)}, [], {})
    assert row.declared and row.origin == "bridge_only"
    assert row.registered is False and row.effective_spec_present is False
    assert row.contract_adapter == "unsupported" and row.drift_status == "unknown"


def test_undeclared_real_registration_reports_actual_dispatch_default() -> None:
    registry = ToolRegistry()
    registry.register("catalog.read", repo_reader_tool)
    row = _tool("catalog.read", registry, {}, [], {})
    assert row.declared is False and row.configured_enabled is None
    assert row.registered and row.dispatch_enabled
    assert row.contract_adapter == "unsupported" and row.execution_authorized is False


def _skill_input(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    path = tmp_path / "skills.yaml"
    raw: dict[str, Any] = {"version": 1, "skills": {"inspection": {
        "version": "1.0.0", "instructions": "instructions.md", "required_tools": ["code.repo_reader"],
        "projects": [], "acceptance": {"output_schemas": ["code_spec.v1"], "required_tool_successes": ["code.repo_reader"]}}}}
    path.write_text(yaml.safe_dump(raw))
    (tmp_path / "instructions.md").write_text("Human-authored instruction; this is not execution evidence.")
    return path, raw


def test_real_skill_bytes_change_content_fingerprint_not_certification(tmp_path: Path) -> None:
    path, raw = _skill_input(tmp_path)
    registry = ToolRegistry()
    registry.register("code.repo_reader", repo_reader_tool)
    tools = (_tool("code.repo_reader", registry, {}, [], {}),)
    first = _skills(path, raw, tools)[0]
    (tmp_path / "instructions.md").write_text("Changed real local instructions.")
    second = _skills(path, raw, tools)[0]
    assert first.definition_valid and second.definition_valid
    assert first.content_sha256 != second.content_sha256
    assert first.definition_sha256 == second.definition_sha256
    assert second.enabled is None and second.certification_status == "not_run"
    assert second.contract_adapter == "requires_host_scope"
    assert "Changed real" not in second.model_dump_json()


@pytest.mark.parametrize("damage", ["missing", "outside_symlink", "invalid_acceptance"])
def test_invalid_real_skill_is_reported_without_readiness(tmp_path: Path, damage: str) -> None:
    registry_root = tmp_path / "registry"
    registry_root.mkdir()
    path, raw = _skill_input(registry_root)
    source = registry_root / "instructions.md"
    if damage == "missing":
        source.unlink()
    elif damage == "outside_symlink":
        source.unlink()
        outside = tmp_path / "private-secret.txt"
        outside.write_text("PRIVATE_INSTRUCTION_SENTINEL")
        source.symlink_to(outside)
    else:
        raw["skills"]["inspection"]["acceptance"]["required_tool_successes"] = ["not.granted"]
        path.write_text(yaml.safe_dump(raw))
    row = _skills(path, raw, ())[0]
    assert not row.definition_valid and row.definition_reason == "invalid_definition"
    assert row.content_sha256 is None and row.contract_adapter == "unknown"
    assert "PRIVATE_INSTRUCTION_SENTINEL" not in row.model_dump_json()


def test_host_override_and_mcp_listing_never_start_command(tmp_path: Path) -> None:
    # Actual production registration of a declared MCP binding, with a real
    # command tripwire. The process must never be launched by passive inspection.
    command = tmp_path / "mcp-tripwire.py"
    marker = tmp_path / "started"
    command.write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('started')\n")
    raw = yaml.safe_load((ROOT / "configs/tools.yaml").read_text())
    secret = secrets.token_urlsafe(40)
    raw["tools"]["mcp.catalog_probe"] = {"enabled": True, "mcp_kind": "filesystem", "mcp_tool": "probe",
        "mcp_env": [], "allowed_agents": ["coding"], "mutation_level": "read", "timeout_seconds": 2,
        "input_schema": {"type": "object"}, "output_schema": {"type": "object"}, "description": secret}
    path = tmp_path / "tools.yaml"
    path.write_text(yaml.safe_dump(raw))
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TEMP", "TMP") if key in os.environ}
    env.update({"PYTHONPATH": str(ROOT / "backend"), "MARS_RUNTIME_ROOT": str(ROOT),
        "MARS_TOOLS_CONFIG_PATH": str(path), "MARS_MCP_FILESYSTEM_COMMAND": f"{sys.executable} {command} {secret}"})
    script = """
import json, os, sys, yaml
from pathlib import Path
from app.bridge.capability_catalog_service import capability_catalog
first = capability_catalog().model_dump(mode='json')
path = Path(os.environ['MARS_TOOLS_CONFIG_PATH'])
raw = yaml.safe_load(path.read_text())
raw['tools']['mcp.catalog_probe']['mcp_tool'] = 'different_remote_tool'
path.write_text(yaml.safe_dump(raw))
second = capability_catalog().model_dump(mode='json')
del raw['tools']['mcp.catalog_probe']
path.write_text(yaml.safe_dump(raw))
third = capability_catalog().model_dump(mode='json')
sys.stdout.write(json.dumps([first, second, third]))
"""
    process = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stderr.replace(secret, "[REDACTED]")
    actual, changed, removed = json.loads(process.stdout)
    row = next(item for item in actual["tools"] if item["name"] == "mcp.catalog_probe")
    assert row["kind"] == "mcp_binding" and row["declared"] and row["registered"]
    assert row["dependency_status"] == "unknown" and row["certification_status"] == "not_run"
    assert row["contract_adapter"] == "unsupported"
    second = next(item for item in changed["tools"] if item["name"] == "mcp.catalog_probe")
    third = next(item for item in removed["tools"] if item["name"] == "mcp.catalog_probe")
    assert row["effective_transport"] == second["effective_transport"] == third["effective_transport"] == "mcp_stdio"
    assert second["effective_binding_sha256"] == third["effective_binding_sha256"] == row["effective_binding_sha256"]
    assert second["drift_fields"] == third["drift_fields"] == ["mcp_binding"]
    assert second["drift_status"] == third["drift_status"] == "detected"
    assert third["declared"] is False and third["kind"] == "mcp_binding" and third["registered"] is True
    assert changed["context_sha256"] != actual["context_sha256"]
    assert not marker.exists()
    assert secret not in process.stdout + process.stderr
    assert str(tmp_path) not in process.stdout
