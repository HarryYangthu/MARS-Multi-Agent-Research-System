from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.agents.base import Artifact, ContextPack, RunRequest
from app.agents.coding.agent import CodingAgent
from app.agents.coding.opencode_adapter import (
    OpenCodeResult,
    _opencode_command,
    _opencode_edited_paths,
    _opencode_referenced_paths,
    _worktree_diff_for_allowed_changes,
    _worktree_diff_for_opencode_edits,
)
from app.harness.tools.project_repo import ProjectRepo
from app.harness.llm.model_registry import AgentConfig


def _agent_config(post_training: dict[str, object]) -> AgentConfig:
    return AgentConfig(
        name="coding",
        enabled=True,
        output_schema="code_spec.v1",
        model_provider="deepseek",
        model_name="deepseek-chat",
        temperature=0.1,
        max_tokens=8192,
        debate_enabled=False,
        debate_rounds=1,
        debate_participants=(),
        tools=(),
        raw={"post_training": post_training},
    )


def test_disabled_post_training_uses_configured_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    import app.settings as settings_mod

    settings_mod._settings = None
    agent = CodingAgent(
        agent_config=_agent_config({"enabled": False, "mode": "load_only"})
    )
    _, llm_cfg = agent._select_provider()
    assert llm_cfg.provider == "deepseek"
    assert llm_cfg.model == "deepseek-chat"


def test_endpoint_post_training_overrides_coding_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARS_MOCK_MODE", "auto")
    monkeypatch.setenv("LOCAL_VLLM_API_KEY", "EMPTY")
    import app.agents.coding.agent as coding_mod
    import app.settings as settings_mod

    settings_mod._settings = None
    monkeypatch.setattr(coding_mod, "_endpoint_reachable", lambda _endpoint: True)
    agent = CodingAgent(
        agent_config=_agent_config(
            {
                "enabled": True,
                "mode": "endpoint",
                "endpoint_provider": "local_vllm",
                "custom_endpoint": "http://127.0.0.1:8001/v1",
                "model": "mars-coding-posttrain",
                "api_key_env": "LOCAL_VLLM_API_KEY",
            }
        )
    )

    provider, llm_cfg = agent._select_provider()

    assert provider.name == "local_vllm"
    assert llm_cfg.provider == "local_vllm"
    assert llm_cfg.model == "mars-coding-posttrain"
    assert llm_cfg.response_schema == "code_spec.v1"
    assert llm_cfg.extra["post_training"]["source"] == "config"


def test_unreachable_post_training_endpoint_uses_configured_real_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARS_MOCK_MODE", "never")
    monkeypatch.setenv("MARS_RUNTIME_MODE", "development")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    import app.agents.coding.agent as coding_mod
    import app.settings as settings_mod

    settings_mod._settings = None
    monkeypatch.setattr(coding_mod, "_endpoint_reachable", lambda _endpoint: False)
    agent = CodingAgent(
        agent_config=_agent_config(
            {
                "enabled": True,
                "mode": "endpoint",
                "endpoint_provider": "local_vllm",
                "custom_endpoint": "http://127.0.0.1:8001/v1",
                "model": "mars-coding-posttrain",
                "api_key_env": "LOCAL_VLLM_API_KEY",
            }
        )
    )

    provider, llm_cfg = agent._select_provider()

    assert provider.name == "deepseek"
    assert llm_cfg.provider == "deepseek"
    assert llm_cfg.model == "deepseek-chat"


@pytest.mark.asyncio
async def test_opencode_unavailable_falls_back_to_native_llm(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MARS_CODING_BACKEND", "opencode")
    monkeypatch.setenv("MARS_RUNTIME_MODE", "development")
    monkeypatch.setenv("MARS_MOCK_MODE", "never")
    import app.agents.coding.opencode_adapter as opencode_mod
    import app.settings as settings_mod

    settings_mod._settings = None
    monkeypatch.setattr(opencode_mod.OpenCodeAdapter, "is_available", lambda _self: False)
    agent = CodingAgent(agent_config=_agent_config({"enabled": False, "mode": "load_only"}))

    async def fake_draft_via_llm(
        request: RunRequest,
        context: ContextPack,
        *,
        debate_role: str | None = None,
    ) -> Artifact:
        metadata = {
            "schema": "code_spec.v1",
            "project": request.project,
            "agent": "coding",
            "upstream_artifact": "experiment/experiment_plan.approved.md",
            "target_lang": "python",
            "baseline_compat": {"preserved": True, "rationale": "test"},
            "files_changed": [],
            "new_dependencies": [],
            "test_coverage": {
                "unit_tests_added": 0,
                "baseline_smoke_test": "not_run",
            },
        }
        assert context.metadata["coding_backend_fallback"] == "opencode_unavailable"
        return Artifact(text="---\nschema: code_spec.v1\n---\n", schema_id="code_spec.v1", metadata=metadata, body="")

    monkeypatch.setattr(agent, "_draft_via_llm", fake_draft_via_llm)
    artifact = await agent.draft(
        RunRequest(
            project="pimc",
            user_request="test",
            extra={"agent_dir": str(tmp_path / "coding")},
        ),
        ContextPack(system="", project="", task=""),
    )

    assert artifact.metadata["schema"] == "code_spec.v1"
    assert (tmp_path / "coding" / "coding_backend_fallback.md").exists()


def test_opencode_command_uses_current_cli_file_attachment(tmp_path: Path) -> None:
    packet = tmp_path / "opencode_task_packet.coding.json"
    command = _opencode_command(
        executable="/usr/local/bin/opencode",
        packet_path=packet,
        project_root=tmp_path,
        prompt="run the task",
    )

    assert command[:3] == ["/usr/local/bin/opencode", "run", "run the task"]
    assert "--print" not in command
    assert "--dir" in command
    assert str(tmp_path) in command
    assert "--auto" in command
    assert f"--file={packet}" in command


def test_opencode_worktree_fallback_diff_uses_edited_files_only(tmp_path: Path) -> None:
    repo = ProjectRepo(
        project="pimc",
        root=tmp_path.resolve(),
        repo_mode="local_path",
        read_only=False,
        allowed_paths=("libs/", "tests/"),
        protected_paths=("libs/model.py:Paper_Total_0327",),
        ignore_patterns=(),
    )
    (tmp_path / "libs").mkdir()
    (tmp_path / "tests").mkdir()
    model_path = tmp_path / "libs" / "model.py"
    test_path = tmp_path / "tests" / "test_order_aware_routing.py"
    model_path.write_text("class OrderAwareRouter:\n    pass\n", encoding="utf-8")
    test_path.write_text("def test_router():\n    assert True\n", encoding="utf-8")
    stdout = "\n".join(
        [
            _opencode_tool_event("read", tmp_path / "libs" / "config.py"),
            _opencode_tool_event("edit", model_path),
            _opencode_tool_event("write", test_path),
        ]
    )

    assert _opencode_edited_paths(stdout, repo) == [
        "libs/model.py",
        "tests/test_order_aware_routing.py",
    ]
    diff = _worktree_diff_for_opencode_edits(repo, stdout)

    assert diff.files_changed == [
        {"path": "libs/model.py", "type": "added", "risk": "high"},
        {
            "path": "tests/test_order_aware_routing.py",
            "type": "added",
            "risk": "medium",
        },
    ]
    assert diff.insertions == 4
    assert diff.deletions == 0
    assert "diff --git a/libs/model.py b/libs/model.py" in diff.text
    assert "--- /dev/null" in diff.text
    assert "+++ b/libs/model.py" in diff.text
    assert "diff --git a/tests/test_order_aware_routing.py b/tests/test_order_aware_routing.py" in diff.text
    assert "libs/config.py" not in diff.text
    apply_root = tmp_path / "apply_target"
    apply_root.mkdir()
    apply_check = subprocess.run(
        ["git", "-C", str(apply_root), "apply", "--check"],
        input=diff.text,
        capture_output=True,
        text=True,
        check=False,
    )
    assert apply_check.returncode == 0, apply_check.stderr


def test_allowed_worktree_fallback_scans_allowed_text_files(tmp_path: Path) -> None:
    repo = ProjectRepo(
        project="pimc",
        root=tmp_path.resolve(),
        repo_mode="local_path",
        read_only=False,
        allowed_paths=("libs/", "tests/"),
        protected_paths=(),
        ignore_patterns=(),
    )
    (tmp_path / "libs").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "libs" / "model.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "docs" / "notes.md").write_text("ignore me\n", encoding="utf-8")

    diff = _worktree_diff_for_allowed_changes(repo)

    assert diff.files_changed == [
        {"path": "libs/model.py", "type": "added", "risk": "medium"}
    ]
    assert diff.insertions == 1
    assert "docs/notes.md" not in diff.text


def test_opencode_referenced_paths_expand_allowed_directories(tmp_path: Path) -> None:
    repo = ProjectRepo(
        project="pimc",
        root=tmp_path.resolve(),
        repo_mode="local_path",
        read_only=False,
        allowed_paths=("libs/", "tests/"),
        protected_paths=(),
        ignore_patterns=(),
    )
    (tmp_path / "libs").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "libs" / "model.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_order.py").write_text("def test_x(): pass\n", encoding="utf-8")
    stdout = "\n".join(
        [
            _opencode_bash_event("python -m pytest tests/ -v"),
            _opencode_bash_event("git diff HEAD -- libs/model.py"),
        ]
    )

    assert _opencode_referenced_paths(stdout, repo) == [
        "libs/model.py",
        "tests/test_order.py",
    ]


@pytest.mark.asyncio
async def test_opencode_failed_result_blocks_coding_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MARS_CODING_BACKEND", "opencode")
    monkeypatch.setenv("MARS_RUNTIME_MODE", "development")
    monkeypatch.setenv("MARS_MOCK_MODE", "never")
    import app.agents.coding.opencode_adapter as opencode_mod
    import app.settings as settings_mod

    settings_mod._settings = None
    monkeypatch.setattr(opencode_mod.OpenCodeAdapter, "is_available", lambda _self: True)

    async def fake_run(
        self: object,
        request: RunRequest,
        context: ContextPack,
    ) -> OpenCodeResult:
        return OpenCodeResult(
            backend="opencode",
            status="failed",
            task_packet_path="coding/opencode_task_packet.coding.json",
            transcript_path="coding/opencode_transcript.coding.md",
            checks=[{"name": "opencode.exit_code", "status": "1"}],
            error="bad cli args",
        )

    monkeypatch.setattr(opencode_mod.OpenCodeAdapter, "run", fake_run)
    agent = CodingAgent(agent_config=_agent_config({"enabled": False, "mode": "load_only"}))

    with pytest.raises(RuntimeError, match="OpenCode backend failed"):
        await agent.draft(
            RunRequest(
                project="pimc",
                user_request="test",
                extra={"agent_dir": str(tmp_path / "coding")},
            ),
            ContextPack(system="", project="", task=""),
        )


def _opencode_tool_event(tool: str, path: Path) -> str:
    return (
        '{"type":"tool_use","part":{"type":"tool","tool":"'
        + tool
        + '","state":{"status":"completed","input":{"filePath":"'
        + path.as_posix()
        + '"}}}}'
    )


def _opencode_bash_event(command: str) -> str:
    return (
        '{"type":"tool_use","part":{"type":"tool","tool":"bash",'
        '"state":{"status":"completed","input":{"command":'
        + repr(command).replace("'", '"')
        + "}}}}"
    )
