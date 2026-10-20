"""Actual Git/path checks and metadata construction; no tool or model substitutes."""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import time
from types import FrameType

import pytest
import yaml

from app.agents.base import RunRequest
from app.agents.coding.agent import CodingAgent
from app.bridge.research_branch import research_branch_scope
from app.harness.context.repo_index import repository_index
from app.harness.llm.model_registry import get_agent_config
from app.harness.project_workspace import open_folder
from app.harness.tools.code import repo_reader_tool, write_file_tool
from app.harness.tools.config import ToolConfig, load_tool_configs, tool_config
from app.harness.tools.git_branch import GitWorkspaceError, git
from app.harness.tools.project_repo import load_project_repo, resolve_allowed_path
from app.harness.tools.registry import ToolContext, get_registry
from app.settings import reset_settings_cache
from app.storage.run_store import RunHandle


@pytest.fixture
def indexed_research(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[RunHandle, Path]]:
    monkeypatch.setenv("MARS_FOLDER_PROJECTS_REGISTRY", str(tmp_path / "registry.json"))
    reset_settings_cache()
    project = open_folder(str(tmp_path / "project"), create=True)
    source = tmp_path / "source"
    (source / "src").mkdir(parents=True)
    (source / "baseline").mkdir()
    for index in range(48):
        (source / "src" / f"module_{index:03}.py").write_text(f"VALUE = {index}\n")
    (source / "baseline/ref.py").write_text("BASELINE = True\n")
    (source / "outside_allowed.py").write_text("NOT_ALLOWED = True\n")
    (source / "src/private.py").write_text("IGNORED = True\n")
    (source / "src/blob.bin").write_bytes(b"non-text\x00")
    git(source, "init", "-b", "baseline")
    git(source, "add", ".")
    git(source, "-c", "user.name=Test", "-c", "user.email=test@localhost", "-c", "commit.gpgsign=false",
        "commit", "-m", "Actual metadata index fixture")
    (project.metadata_root / "repo_link.yaml").write_text(yaml.safe_dump({"repo_path": str(source),
        "repo_mode": "local_path", "read_only": True, "protected_paths": ["baseline/"],
        "allowed_paths": ["src/", "baseline/"], "ignore_patterns": [".git/", ".env*", "src/private.py"]}))
    root = tmp_path / "run"
    root.mkdir()
    run = RunHandle("index_research", root, project.name, "index", "coding", "2026-10-19T00:00:00Z")
    try:
        yield run, source
    finally:
        reset_settings_cache()


def _trace_commands(path: Path) -> list[list[str]]:
    commands = [row["argv"] for line in path.read_text().splitlines()
                if (row := json.loads(line)).get("event") == "start"]
    return [command[3:] if command[1:2] == ["-C"] else command[1:] for command in commands]


def _binding_commands(commands: list[list[str]]) -> list[list[str]]:
    return [command for command in commands if command[0] in {"branch", "cat-file", "merge-base"}]


def test_one_actual_binding_validation_per_index_and_public_paths_still_validate(
        indexed_research: tuple[RunHandle, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run, _ = indexed_research
    with research_branch_scope(run, "coding"):
        optimized_trace = tmp_path / "index.trace.jsonl"
        monkeypatch.setenv("GIT_TRACE2_EVENT", str(optimized_trace))
        started = time.perf_counter()
        result = repository_index(run.project, 120)
        optimized_seconds = time.perf_counter() - started
        optimized = _trace_commands(optimized_trace)
        bindings = _binding_commands(optimized)
        assert bindings == [["branch", "--show-current"], ["cat-file", "-e", bindings[1][2]],
                            ["merge-base", "--is-ancestor", bindings[2][2], "HEAD"]]
        expected = ["baseline/ref.py"] + [f"src/module_{index:03}.py" for index in range(48)]
        assert result["files"] == expected
        assert result["contents_loaded"] is False and result["complete"] is False
        reference_trace = tmp_path / "public-paths.trace.jsonl"
        monkeypatch.setenv("GIT_TRACE2_EVENT", str(reference_trace))
        started = time.perf_counter()
        repo = load_project_repo(run.project)
        reference_names = result["files"][:8]
        for name in reference_names:
            assert resolve_allowed_path(repo, name, require_exists=True, require_text=True).is_file()
        reference_seconds = time.perf_counter() - started
        reference = _trace_commands(reference_trace)
        assert len(_binding_commands(reference)) == 3 * (1 + len(reference_names))
        benchmark = {"indexed_files": len(expected), "optimized_git_processes": len(optimized),
            "reference_files_checked_with_public_resolver": len(reference_names),
            "per_file_public_validation_git_processes": len(reference),
            "optimized_binding_validation_commands": len(bindings),
            "per_file_binding_validation_commands": len(_binding_commands(reference)),
            "optimized_seconds": optimized_seconds, "per_file_public_validation_seconds": reference_seconds,
            "actual_git_trace": str(optimized_trace), "reference_git_trace": str(reference_trace),
            "git_executable": os.environ.get("MARS_GIT_EXECUTABLE"),
            "measurement_scope": "temporary real Git fixture, not deployed end-to-end latency"}
        if destination := os.environ.get("MARS_TEST_INDEX_BENCHMARK"):
            Path(destination).write_text(json.dumps(benchmark, indent=2) + "\n")


def test_dirty_snapshot_index_validates_both_frozen_commits_once(
        indexed_research: tuple[RunHandle, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run, source = indexed_research
    (source / "src/module_000.py").write_text("VALUE = 999\n")
    with research_branch_scope(run, "coding"):
        trace = tmp_path / "dirty-index.trace.jsonl"
        monkeypatch.setenv("GIT_TRACE2_EVENT", str(trace))
        result = repository_index(run.project, 120)
        commands = _binding_commands(_trace_commands(trace))
        assert len(result["files"]) == 49
        assert [command[0] for command in commands] == ["branch", "cat-file", "merge-base", "cat-file", "merge-base"]
        assert commands[1][2] != commands[3][2]


@pytest.mark.asyncio
async def test_index_thread_preserves_branch_binding_and_content_tools_revalidate(
        indexed_research: tuple[RunHandle, Path], tmp_path: Path) -> None:
    run, source = indexed_research
    with research_branch_scope(run, "coding"):
        repo = load_project_repo(run.project)
        (repo.root / "src/only_this_run.py").write_text("CANDIDATE_ONLY = True\n")
        outside = tmp_path / "outside.py"
        outside.write_text("OUTSIDE = True\n")
        (repo.root / "src/link.py").symlink_to(outside)
        result = await asyncio.to_thread(repository_index, run.project, 120)
        assert "src/only_this_run.py" in result["files"]
        assert not (source / "src/only_this_run.py").exists()
        assert "src/link.py" not in result["files"]
        ctx = ToolContext(run_id=run.run_id, project=run.project, agent="coding", extra={"run_root": str(run.root)})
        assert (await repo_reader_tool({"path": "src/only_this_run.py"}, ctx)).ok
        for name in ("../outside.py", "src/private.py", "src/link.py", "outside_allowed.py"):
            assert not (await repo_reader_tool({"path": name}, ctx)).ok
        assert not (await write_file_tool({"path": "baseline/ref.py", "content": "changed\n"}, ctx)).ok
        expected_branch = git(repo.root, "branch", "--show-current")
        git(repo.root, "switch", "-c", "unbound_branch")
        try:
            with pytest.raises(GitWorkspaceError, match="其他分支"):
                await asyncio.to_thread(repository_index, run.project, 120)
            assert not (await repo_reader_tool({"path": "src/module_000.py"}, ctx)).ok
            assert not (await write_file_tool({"path": "src/module_000.py", "content": "changed\n"}, ctx)).ok
        finally:
            git(repo.root, "switch", expected_branch)
            (repo.root / "src/link.py").unlink()


def test_context_tool_config_is_loaded_once_and_next_context_and_dispatch_observe_changes(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = get_registry()
    config_path = (tmp_path / "tools.yaml").resolve()
    config_path.write_text("tools:\n  code.repo_reader: {enabled: true, allowed_agents: [coding]}\n"
                           "  code.write_file: {enabled: false, allowed_agents: [coding]}\n")
    monkeypatch.setenv("MARS_TOOLS_CONFIG_PATH", str(config_path))
    config = replace(get_agent_config("coding"), tools=("code.repo_reader", "code.write_file", "unregistered.read") * 12)
    agent = CodingAgent(agent_config=config)
    # Observation only: the Python audit hook does not replace any reader or tool.
    direct_reads: list[str] = []
    def observe(event: str, arguments: tuple[object, ...]) -> None:
        if event != "open" or not arguments or str(arguments[0]) != str(config_path):
            return
        frame: FrameType | None = sys._getframe(1)
        callers: list[str] = []
        while frame is not None:
            callers.append(frame.f_code.co_name)
            frame = frame.f_back
        if "_prepare_runtime_context" in callers and "tool_config" not in callers:
            direct_reads.append(str(arguments[0]))
    sys.addaudithook(observe)
    request = RunRequest(project="pimc", user_request="Metadata only", extra={"run_root": str(tmp_path / "run"),
        "context_sources": {"agent_resources": False, "memory": False}})
    metadata = {"context_sources": {"agent_resources": False, "memory": False}}
    agent._prepare_runtime_context(request, {}, metadata)
    assert len(direct_reads) == 1
    actual = load_tool_configs()
    assert [name for name in config.tools if actual.get(name, ToolConfig()).enabled] == [
        name for name in config.tools if tool_config(name).enabled]
    config_path.write_text("tools:\n  code.repo_reader: {enabled: false, allowed_agents: [coding]}\n"
                           "  code.write_file: {enabled: true, allowed_agents: [coding]}\n")
    agent._prepare_runtime_context(request, {}, metadata)
    assert len(direct_reads) == 2 and not load_tool_configs()["code.repo_reader"].enabled
    result = asyncio.run(registry.dispatch("code.repo_reader", {"path": "never_read.py"},
        ToolContext(run_id="metadata", project="pimc", agent="coding", extra={"run_root": str(tmp_path / "run")})))
    assert not result.ok and result.status == "disabled"
