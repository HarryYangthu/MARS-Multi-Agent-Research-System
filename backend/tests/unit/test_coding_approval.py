"""Approval with actual Git branches, guarded tools and persisted artifacts."""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
import json
from pathlib import Path

from fastapi import HTTPException
import pytest
import yaml

from app.api import dependencies
from app.api.artifacts import _apply_patch_or_raise, approve_artifact
from app.bridge.agent_registry import AgentRegistry
from app.bridge.coding_approval import coding_candidate_errors, verify_written_code
from app.bridge.orchestrator import Orchestrator, RunRequest, RunSession
from app.bridge.research_branch import research_branch_scope
from app.harness.project_workspace import open_folder
from app.harness.runtime.state_machine import NodeState
from app.harness.schema.frontmatter_parser import dumps
from app.harness.tools.git_branch import git
from app.harness.tools.registry import ToolContext, get_registry
from app.settings import reset_settings_cache
from app.storage.artifact_store import ArtifactStore
from app.storage.run_store import RunHandle, RunStore


@pytest.fixture
def research(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Orchestrator, RunSession, Path]]:
    # Configuration points to real local directories and real services.
    monkeypatch.setenv("MARS_FOLDER_PROJECTS_REGISTRY", str(tmp_path / "registry.json"))
    reset_settings_cache()
    project = open_folder(str(tmp_path / "project"), create=True)
    source = tmp_path / "code"
    source.mkdir()
    git(source, "init", "-b", "baseline")
    (source / "main.py").write_text("VALUE = 1\n")
    git(source, "add", ".")
    git(source, "-c", "user.name=Test", "-c", "user.email=test@localhost", "-c", "commit.gpgsign=false", "commit", "-m", "Real baseline")
    (project.metadata_root / "repo_link.yaml").write_text(yaml.safe_dump({"repo_path": str(source),
        "read_only": True, "allowed_paths": ["main.py", "new.py"], "protected_paths": [], "ignore_patterns": [".git/"]}))
    previous = dependencies._run_store, dependencies._orchestrator
    store = RunStore(tmp_path / "runs")
    orch = Orchestrator(run_store=store, registry=AgentRegistry())
    dependencies._run_store, dependencies._orchestrator = store, orch
    session = orch.create_session(RunRequest(task="human-authored-coding-review", project=project.name,
        entrypoint="coding", standalone=True, auto_approve=False))
    try:
        yield orch, session, source
    finally:
        dependencies._run_store, dependencies._orchestrator = previous
        reset_settings_cache()


def spec(run: RunHandle, paths: list[tuple[str, str]]) -> str:
    return dumps({"schema": "code_spec.v1", "project": run.project, "agent": "coding",
        "target_lang": "python", "baseline_compat": {"preserved": True},
        "files_changed": [{"path": path, "type": kind} for path, kind in paths]},
        "Human-authored verification input; no model execution or scientific result is represented.")


async def write(run: RunHandle, path: str = "main.py", content: str = "VALUE = 2\n") -> None:
    with research_branch_scope(run, "coding"):
        result = await get_registry().dispatch("code.write_file", {"path": path, "content": content},
            ToolContext(run_id=run.run_id, project=run.project, agent="coding", extra={"run_root": str(run.root)}))
        assert result.ok, result.error


@pytest.mark.asyncio
async def test_written_implementation_ignores_older_unapplied_patch(research: tuple[Orchestrator, RunSession, Path]) -> None:
    _, session, source = research
    run = session.run
    await write(run)
    ArtifactStore(run).write(text=spec(run, [("main.py", "modified")]))
    patch = run.root / "coding/patch.v1.diff"
    patch.write_text("--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 999\n")
    receipts = list((run.root / "coding/tool_applications").glob("*.json"))
    await _apply_patch_or_raise(run.run_id, "v1")
    await _apply_patch_or_raise(run.run_id, "v1")
    assert (source / "main.py").read_text() == "VALUE = 2\n"
    assert list((run.root / "coding/tool_applications").glob("*.json")) == receipts
    assert git(source, "show", "baseline:main.py") == "VALUE = 1"
    assert not (run.root / "coding/patch.v1.approved.json").exists()


@pytest.mark.asyncio
async def test_identical_rewrite_requires_reuse_declaration_and_never_replays_patch(
    research: tuple[Orchestrator, RunSession, Path],
) -> None:
    _, session, source = research
    run = session.run
    await write(run, content="VALUE = 1\n")
    store = ArtifactStore(run)
    store.write(text=spec(run, [("main.py", "added")]))
    (run.root / "coding/patch.v1.diff").write_text("-VALUE = 1\n+VALUE = 999\n")
    receipts = sorted((run.root / "coding/tool_applications").glob("*.json"))
    with pytest.raises(HTTPException, match="复用"):
        await _apply_patch_or_raise(run.run_id, "v1")
    corrected = store.write(text=spec(run, []))
    assert corrected.version == "v2"
    # A patch reference must not change which Markdown version is verified.
    await _apply_patch_or_raise(run.run_id, "v1", artifact_version="v2")
    assert (source / "main.py").read_text() == "VALUE = 1\n"
    assert sorted((run.root / "coding/tool_applications").glob("*.json")) == receipts
    assert not (run.root / "coding/code_spec.approved.md").exists()


@pytest.mark.asyncio
async def test_host_validation_returns_actual_net_files_before_review(
    research: tuple[Orchestrator, RunSession, Path],
) -> None:
    from app.agents.base import RunRequest as AgentRequest
    from app.agents.coding.agent import CodingAgent
    _, session, source = research
    run = session.run
    await write(run, content="VALUE = 1\n")
    request = AgentRequest(project=run.project, user_request="Reuse the verified implementation",
                           candidate_validator=lambda text: coding_candidate_errors(run, text))
    with research_branch_scope(run, "coding"):
        errors = await CodingAgent().validate_candidate(request, spec(run, [("main.py", "added")]), [])
        assert any('"files_changed": []' in error and "复用" in error for error in errors)
        assert await CodingAgent().validate_candidate(request, spec(run, []), []) == []
        # Identical content is still verified; a later external edit must block.
        (source / "main.py").write_text("VALUE = 9\n")
        assert coding_candidate_errors(run, spec(run, []))


@pytest.mark.asyncio
async def test_legacy_explanatory_patch_fails_without_tool_dispatch(
    research: tuple[Orchestrator, RunSession, Path],
) -> None:
    _, session, source = research
    run = session.run
    ArtifactStore(run).write(text=spec(run, [("main.py", "modified")]))
    (run.root / "coding/patch.v1.diff").write_text(" ...\n-VALUE = 1\n+VALUE = 2\n")
    with pytest.raises(HTTPException, match="只是代码变更说明") as caught:
        await _apply_patch_or_raise(run.run_id, "v1")
    assert caught.value.status_code == 409
    assert not list((run.root / "coding/tool_applications").glob("*.json"))
    assert (source / "main.py").read_text() == "VALUE = 1\n"


@pytest.mark.asyncio
async def test_real_code_checks_use_the_selected_git_in_child_commands(
    research: tuple[Orchestrator, RunSession, Path],
) -> None:
    from app.harness.runtime.git_runtime import resolve_git
    _, session, source = research
    # Real test executable, actual Git selection and actual governed subprocess.
    selected = resolve_git().executable
    with research_branch_scope(session.run, "coding"):
        (source / "test_git_runtime.py").write_text(
            "from pathlib import Path\nimport shutil, subprocess\n"
            "def test_actual_child_git():\n"
            f"    assert Path(shutil.which('git')).resolve() == Path({selected!r}).resolve()\n"
            "    assert subprocess.run(['git', '--version'], capture_output=True).returncode == 0\n")
        result = await get_registry().dispatch("code.test_runner", {"command_id": "pytest_quick"},
            ToolContext(run_id=session.run.run_id, project=session.run.project, agent="coding",
                        extra={"run_root": str(session.run.root)}))
    assert result.ok, json.dumps(result.output, indent=2)
    assert "1 passed" in str(result.output)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["external_edit", "partial", "extra", "wrong_type", "wrong_project"])
async def test_partial_or_changed_code_is_not_approved(research: tuple[Orchestrator, RunSession, Path], case: str) -> None:
    _, session, source = research
    run = session.run
    await write(run)
    paths = [("main.py", "modified")]
    if case == "external_edit":
        (source / "main.py").write_text("VALUE = 3\n")
    elif case == "partial":
        paths.append(("new.py", "added"))
    elif case == "extra":
        await write(run, "new.py", "NEW = True\n")
    elif case == "wrong_type":
        paths = [("main.py", "added")]
    text = spec(run, paths)
    if case == "wrong_project":
        text = text.replace(run.project, "other-project")
    ArtifactStore(run).write(text=text)
    before = (source / "main.py").read_bytes()
    with pytest.raises(HTTPException) as refused:
        await _apply_patch_or_raise(run.run_id, "v1")
    assert refused.value.status_code == 409
    assert (source / "main.py").read_bytes() == before
    assert not (run.root / "coding/code_spec.approved.md").exists()


@pytest.mark.asyncio
async def test_patch_only_approval_uses_run_branch_and_gate(research: tuple[Orchestrator, RunSession, Path]) -> None:
    _, session, source = research
    run = session.run
    ArtifactStore(run).write(text=spec(run, [("main.py", "modified")]))
    (run.root / "coding/patch.v1.diff").write_text("diff --git a/main.py b/main.py\n--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n")
    await _apply_patch_or_raise(run.run_id, "v1")
    assert (source / "main.py").read_text() == "VALUE = 2\n"
    assert git(source, "show", "baseline:main.py") == "VALUE = 1"
    with research_branch_scope(run, "coding"):
        assert verify_written_code(run, (run.root / "coding/code_spec.v1.md").read_text())


@pytest.mark.asyncio
async def test_real_approval_after_clean_shutdown_advances_original_run(research: tuple[Orchestrator, RunSession, Path]) -> None:
    orch, session, source = research
    run = session.run
    await write(run)
    ref = ArtifactStore(run).write(text=spec(run, [("main.py", "modified")]))
    session.graph.restore_state("coding", NodeState.WAITING_REVIEW)
    orch._persist_state(session, status="waiting_review")
    async def wait_for_shutdown() -> None:
        await asyncio.Event().wait()
    assert orch._spawn_owned(session, "review-lifecycle", wait_for_shutdown)
    await orch.shutdown_owned_runs()
    recovered = Orchestrator(run_store=orch.run_store, registry=AgentRegistry())
    dependencies._orchestrator = recovered
    result = await approve_artifact(run.run_id, "coding", "code_spec", "v1")
    assert result.version == "approved" and result.text == ref.path.read_text()
    assert recovered.session(run.run_id).graph.state("coding") == NodeState.DONE
    assert recovered.session(run.run_id).termination is None
    assert (source / "main.py").read_text() == "VALUE = 2\n"
    assert len(orch.run_store.list()) == 1
    assert not (run.root / "agent_traces").exists()
