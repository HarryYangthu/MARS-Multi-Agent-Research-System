from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.bridge.commander_tools import (
    TOOLS,
    ToolContext as CommanderToolContext,
    execute_tool as execute_commander_tool,
)
from app.bridge.orchestrator import Orchestrator, RunRequest
from app.harness.runtime.event_bus import InProcessEventBus
from app.harness.tools import code as code_tools
from app.harness.llm.model_registry import list_agent_configs
from app.harness.tools.config import CommandSpec, ToolConfig, load_tool_configs
from app.harness.tools.registry import (
    ToolContext,
    ToolPolicy,
    ToolResult,
    ToolSpec,
    reset_for_tests,
)
from app.storage.run_store import RunStore


@pytest.mark.asyncio
async def test_dispatch_records_tool_call(tmp_path: Path) -> None:
    reg = reset_for_tests()
    run_root = tmp_path / "runs" / "r1"
    (run_root / "events").mkdir(parents=True)

    async def fake_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return ToolResult(ok=True, output={"echo": args["x"]})

    reg.register("test.echo", fake_tool)
    result = await reg.dispatch(
        "test.echo",
        {"x": 1},
        ToolContext(
            run_id="r1",
            project="moe-pimc",
            agent="bridge",
            extra={"run_root": str(run_root)},
        ),
    )

    assert result.ok is True
    audit = run_root / "events" / "tool_calls.jsonl"
    assert audit.exists()
    assert '"tool": "test.echo"' in audit.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_disabled_tool_returns_standard_status(tmp_path: Path) -> None:
    reg = reset_for_tests()
    run_root = tmp_path / "runs" / "r1"
    (run_root / "events").mkdir(parents=True)

    result = await reg.dispatch(
        "search.web_search",
        {"q": "pim"},
        ToolContext(
            run_id="r1",
            project="moe-pimc",
            agent="bridge",
            extra={"run_root": str(run_root)},
        ),
    )

    assert result.ok is False
    assert result.status == "disabled"


@pytest.mark.asyncio
async def test_schema_invalid_args_are_rejected_before_tool() -> None:
    reg = reset_for_tests()
    result = await reg.dispatch(
        "code.write_file",
        {"path": "libs/generated.py"},
        ToolContext(run_id="r1", project="moe-pimc", agent="coding"),
    )

    assert result.ok is False
    assert result.status == "error"
    assert "schema validation" in str(result.error)


@pytest.mark.asyncio
async def test_large_refactor_diff_requires_approval_before_tool() -> None:
    reg = reset_for_tests()
    diff = "\n".join(
        f"diff --git a/libs/f{i}.py b/libs/f{i}.py\n--- /dev/null\n+++ b/libs/f{i}.py\n@@ -0,0 +1 @@\n+x"
        for i in range(6)
    )

    result = await reg.dispatch(
        "code.patch_generator",
        {"diff": diff},
        ToolContext(run_id="r1", project="moe-pimc", agent="coding"),
    )

    assert result.ok is False
    assert result.status == "requires_approval"
    assert result.requires_approval is True


@pytest.mark.asyncio
async def test_apply_patch_applies_after_gate_and_repo_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _make_project_repo(tmp_path, read_only=False)
    (repo / "libs" / "Model.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    monkeypatch.setattr(code_tools, "repo_root", lambda: tmp_path)

    reg = reset_for_tests()
    diff = """diff --git a/libs/Model.py b/libs/Model.py
--- a/libs/Model.py
+++ b/libs/Model.py
@@ -1 +1 @@
-VALUE = 1
+VALUE = 2
"""
    result = await reg.dispatch(
        "code.apply_patch",
        {"version": "v1", "diff": diff, "files": [{"path": "libs/Model.py"}]},
        ToolContext(
            run_id="r1",
            project="demo",
            agent="coding",
            extra={
                "run_root": str(tmp_path / "runs" / "r1"),
                "project_repo_root": str(repo),
            },
        ),
    )

    assert result.ok is True
    assert (repo / "libs" / "Model.py").read_text(encoding="utf-8") == "VALUE = 2\n"


@pytest.mark.asyncio
async def test_code_tools_follow_repo_link_local_path_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "workspace" / "repos" / "demo_local_path"
    project_dir = tmp_path / "projects" / "demo"
    (repo / "libs").mkdir(parents=True)
    (repo / "data").mkdir(parents=True)
    project_dir.mkdir(parents=True)
    (repo / "libs" / "model.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "data" / "ignored.py").write_text("VALUE = 0\n", encoding="utf-8")
    (project_dir / "repo_link.yaml").write_text(
        "\n".join(
            [
                "project: demo",
                "repo_mode: local_path",
                "local_path: ../../workspace/repos/demo_local_path",
                "read_only: false",
                "allowed_paths:",
                "  - libs/",
                "  - data/",
                "protected_paths: []",
                "ignore_patterns:",
                "  - data/",
            ]
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    monkeypatch.setattr(code_tools, "repo_root", lambda: tmp_path)

    reg = reset_for_tests()
    ctx = ToolContext(
        run_id="r1",
        project="demo",
        agent="coding",
        extra={"run_root": str(tmp_path / "runs" / "r1")},
    )

    read = await reg.dispatch("code.repo_reader", {"path": "libs/model.py"}, ctx)
    assert read.ok is True
    assert read.output["content"] == "VALUE = 1\n"

    generated = await reg.dispatch(
        "code.patch_generator",
        {"path": "libs/model.py", "content": "VALUE = 2\n"},
        ctx,
    )
    assert generated.ok is True

    applied = await reg.dispatch(
        "code.apply_patch",
        {"version": "v1", "diff": generated.output["diff"]},
        ctx,
    )
    assert applied.ok is True
    assert (repo / "libs" / "model.py").read_text(encoding="utf-8") == "VALUE = 2\n"

    lint = await reg.dispatch("code.lint", {}, ctx)
    assert lint.ok is True

    ignored = await reg.dispatch("code.repo_reader", {"path": "data/ignored.py"}, ctx)
    assert ignored.ok is False
    assert "ignored" in str(ignored.error)


@pytest.mark.asyncio
async def test_write_file_records_events_and_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _make_project_repo(tmp_path, read_only=False)
    run_root = tmp_path / "runs" / "r1"
    run_root.mkdir(parents=True)
    monkeypatch.setattr(code_tools, "repo_root", lambda: tmp_path)

    reg = reset_for_tests()
    ctx = ToolContext(
        run_id="r1",
        project="demo",
        agent="coding",
        extra={
            "run_root": str(run_root),
            "project_repo_root": str(repo),
        },
    )
    result = await reg.dispatch(
        "code.write_file",
        {"path": "libs/generated.py", "content": "VALUE = 1\n"},
        ctx,
    )

    assert result.ok is True
    assert result.rollback_ref
    assert (repo / "libs" / "generated.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    events = (run_root / "events" / "tool_events.jsonl").read_text(encoding="utf-8")
    assert '"event": "tool.started"' in events
    assert '"event": "tool.completed"' in events
    records = list((run_root / "coding" / "tool_applications").glob("*.json"))
    assert any(
        json.loads(path.read_text(encoding="utf-8")).get("rollback_ref") == result.rollback_ref
        for path in records
    )

    rollback = await reg.dispatch(
        "code.rollback_patch",
        {"rollback_ref": result.rollback_ref},
        ToolContext(
            run_id="r1",
            project="demo",
            agent="bridge",
            extra={
                "run_root": str(run_root),
                "project_repo_root": str(repo),
            },
        ),
    )

    assert rollback.ok is True
    assert not (repo / "libs" / "generated.py").exists()
    events_after_rollback = (run_root / "events" / "tool_events.jsonl").read_text(encoding="utf-8")
    assert '"event": "tool.rolled_back"' in events_after_rollback


@pytest.mark.asyncio
async def test_delete_file_requires_approval_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _make_project_repo(tmp_path, read_only=False)
    target = repo / "libs" / "delete_me.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.setattr(code_tools, "repo_root", lambda: tmp_path)

    reg = reset_for_tests()
    result = await reg.dispatch(
        "code.delete_file",
        {"path": "libs/delete_me.py"},
        ToolContext(
            run_id="r1",
            project="demo",
            agent="coding",
            extra={"project_repo_root": str(repo)},
        ),
    )

    assert result.ok is False
    assert result.status == "requires_approval"
    assert target.exists()


@pytest.mark.asyncio
async def test_apply_patch_rejects_read_only_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_project_repo(tmp_path, read_only=True)
    repo = tmp_path / "workspace" / "repos" / "demo"
    monkeypatch.setattr(code_tools, "repo_root", lambda: tmp_path)

    reg = reset_for_tests()
    result = await reg.dispatch(
        "code.apply_patch",
        {
            "version": "v1",
            "diff": "diff --git a/libs/x.py b/libs/x.py\n--- /dev/null\n+++ b/libs/x.py\n@@ -0,0 +1 @@\n+x\n",
            "files": [{"path": "libs/x.py"}],
        },
        ToolContext(
            run_id="r1",
            project="demo",
            agent="coding",
            extra={
                "run_root": str(tmp_path / "runs" / "r1"),
                "project_repo_root": str(repo),
            },
        ),
    )

    assert result.ok is False
    assert "read_only" in str(result.error)


@pytest.mark.asyncio
async def test_lint_blocks_non_allowlisted_config_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _make_project_repo(tmp_path, read_only=False)
    monkeypatch.setattr(code_tools, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        code_tools,
        "check_commands",
        lambda kind: (
            CommandSpec(id="bad", label="Bad command", argv=("bash", "-lc", "echo hi")),
        ),
    )
    monkeypatch.setattr(
        code_tools,
        "tool_config",
        lambda name: ToolConfig(command_allowlist=(("python", "-m", "compileall"),)),
    )

    reg = reset_for_tests()
    result = await reg.dispatch(
        "code.lint",
        {},
        ToolContext(
            run_id="r1",
            project="demo",
            agent="coding",
            extra={"project_repo_root": str(repo)},
        ),
    )

    assert result.ok is False
    assert "not allowlisted" in str(result.error)


@pytest.mark.asyncio
async def test_tool_audit_redacts_default_and_spec_keys(tmp_path: Path) -> None:
    reg = reset_for_tests()
    run_root = tmp_path / "runs" / "r1"
    (run_root / "events").mkdir(parents=True)

    async def fake_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return ToolResult(ok=True, output={"received": True})

    reg.register(
        "test.redact",
        fake_tool,
        spec=ToolSpec(
            name="test.redact",
            namespace="test",
            description="redaction test tool",
            policy=ToolPolicy(redaction=("research_token",)),
        ),
    )
    result = await reg.dispatch(
        "test.redact",
        {
            "api_key": "raw-key",
            "research_token": "raw-token",
            "nested": {"password": "raw-pass"},
        },
        ToolContext(
            run_id="r1",
            project="moe-pimc",
            agent="bridge",
            extra={"run_root": str(run_root)},
        ),
    )

    assert result.ok is True
    audit = (run_root / "events" / "tool_calls.jsonl").read_text(encoding="utf-8")
    events = (run_root / "events" / "tool_events.jsonl").read_text(encoding="utf-8")
    combined = audit + events
    assert "raw-key" not in combined
    assert "raw-token" not in combined
    assert "raw-pass" not in combined
    assert "[redacted]" in combined


def test_commander_canonical_tools_are_registered() -> None:
    for name in (
        "run.create",
        "run.start",
        "run.status",
        "run.feedback_loop",
        "artifact.read",
        "artifact.review",
        "metrics.evaluate",
        "diagnosis.failure_analysis",
        "user.approval",
    ):
        assert name in TOOLS


def test_tool_catalogue_has_v1_specs_and_config_entries() -> None:
    reg = reset_for_tests()
    configs = load_tool_configs()
    registered = set(reg.names())
    catalogue = {spec.name: spec for spec in reg.specs(include_bridge_only=True)}

    required = {
        "search.arxiv_search",
        "search.web_search",
        "code.apply_patch",
        "code.write_file",
        "code.delete_file",
        "code.rollback_patch",
        "execution.batch_runner",
        "run.status",
        "artifact.review",
    }
    assert required.issubset(catalogue)
    assert sorted(name for name in catalogue if name not in configs) == []
    assert sorted(
        name for name, cfg in configs.items() if name not in registered and not cfg.bridge_only
    ) == []

    for spec in catalogue.values():
        assert spec.name
        assert spec.namespace
        assert spec.description
        assert isinstance(spec.input_schema, dict)
        assert isinstance(spec.output_schema, dict)
        assert spec.policy.mutation_level in {"read", "write"}
        assert spec.policy.timeout_seconds > 0
        assert spec.policy.allowed_agents

    assert configs["search.arxiv_search"].enabled is True
    assert configs["search.web_search"].enabled is False
    assert catalogue["search.arxiv_search"].policy.network is True
    assert catalogue["search.web_search"].policy.network is True
    assert catalogue["run.status"].bridge_only is True


def test_agent_tool_references_are_registered_or_bridge_only() -> None:
    reg = reset_for_tests()
    configs = load_tool_configs()
    missing: list[str] = []
    for agent in list_agent_configs():
        for tool_name in agent.tools:
            cfg = configs.get(tool_name)
            if reg.has(tool_name) or (cfg is not None and cfg.bridge_only):
                continue
            missing.append(f"{agent.name}:{tool_name}")

    assert missing == []


@pytest.mark.asyncio
async def test_agent_permission_blocks_before_tool_runs(tmp_path: Path) -> None:
    reg = reset_for_tests()
    run_root = tmp_path / "runs" / "r1"
    (run_root / "events").mkdir(parents=True)
    called = False

    async def fake_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        nonlocal called
        called = True
        return ToolResult(ok=True, output={"should": "not run"})

    reg.register(
        "test.idea_only",
        fake_tool,
        spec=ToolSpec(
            name="test.idea_only",
            namespace="test",
            description="agent permission test tool",
            policy=ToolPolicy(allowed_agents=("idea",)),
        ),
    )
    result = await reg.dispatch(
        "test.idea_only",
        {},
        ToolContext(
            run_id="r1",
            project="moe-pimc",
            agent="coding",
            extra={"run_root": str(run_root)},
        ),
    )

    assert called is False
    assert result.ok is False
    assert result.status == "not_allowed"
    audit = (run_root / "events" / "tool_calls.jsonl").read_text(encoding="utf-8")
    assert '"status": "not_allowed"' in audit


@pytest.mark.asyncio
async def test_commander_canonical_tool_uses_registry_audit(tmp_path: Path) -> None:
    reset_for_tests()
    store = RunStore(tmp_path / "runs")
    orchestrator = Orchestrator(run_store=store, bus=InProcessEventBus())
    session = orchestrator.create_session(
        RunRequest(task="commander-tools", project="moe-pimc", entrypoint="pipeline")
    )
    from app.bridge.commander_session import CommanderSession

    commander_session = CommanderSession(
        conv_id="conv_tools",
        project="moe-pimc",
        linked_run_id=session.run.run_id,
    )
    result = await execute_commander_tool(
        "run.status",
        {},
        CommanderToolContext(
            orchestrator=orchestrator,
            session=commander_session,
            run_store=store,
        ),
    )

    assert result["ok"] is True
    assert result["tool_status"] == "success"
    assert result["tool_call_id"]
    events = (session.run.root / "events" / "tool_events.jsonl").read_text(encoding="utf-8")
    assert '"tool": "run.status"' in events
    assert '"event": "tool.completed"' in events


def test_mutating_code_tools_are_not_invoked_outside_registry() -> None:
    app_root = Path(__file__).resolve().parents[2] / "app"
    allowed_files = {
        app_root / "harness" / "tools" / "code" / "__init__.py",
        app_root / "harness" / "tools" / "registry.py",
    }
    forbidden_calls = (
        "apply_patch_tool(",
        "write_file_tool(",
        "delete_file_tool(",
        "rollback_patch_tool(",
    )
    offenders: list[str] = []
    for path in app_root.rglob("*.py"):
        if path in allowed_files:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for token in forbidden_calls:
            if token in text:
                offenders.append(f"{path.relative_to(app_root)}:{token}")

    assert offenders == []


def _make_project_repo(tmp_path: Path, *, read_only: bool) -> Path:
    project_dir = tmp_path / "projects" / "demo"
    repo = tmp_path / "workspace" / "repos" / "demo"
    (repo / "libs").mkdir(parents=True)
    project_dir.mkdir(parents=True)
    (project_dir / "repo_link.yaml").write_text(
        "\n".join(
            [
                "project: demo",
                "repo_mode: local_path",
                f"repo_path: {repo}",
                f"read_only: {str(read_only).lower()}",
                "allowed_paths:",
                "  - libs/",
                "protected_paths: []",
            ]
        ),
        encoding="utf-8",
    )
    return repo
