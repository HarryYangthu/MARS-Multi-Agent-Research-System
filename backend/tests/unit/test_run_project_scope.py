"""Real file isolation, schema contracts and actual git apply; no service doubles."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import pytest

from app.agents.base import RunRequest
from app.agents.experiment.agent import ExperimentAgent
from app.bridge.research_contract_service import default_research_budget, freeze_research_task
from app.bridge.research_project_scope import prepare_project_scope
from app.bridge.research_run_service import persist_run_research_contract, research_execution_admission
from app.harness.discovery.snapshots import SnapshotPolicy, verify_snapshot
from app.harness.gates.baseline_compatibility import gate_check, static_check
from app.harness.persistence import atomic_write_json
from app.harness.project_workspace import folder_project, project_root
from app.harness.runtime.project_scope import ProjectScope, bind_project_scope, current_project_scope, validated_diff_paths
from app.harness.runtime.research_contract import ProjectContract
from app.harness.tools.code import (
    apply_patch_tool, delete_file_tool, lint_tool, patch_generator_tool, repo_reader_tool,
    rollback_patch_tool, test_runner_tool as run_code_tests_tool, write_file_tool,
)
from app.harness.tools.project_repo import load_project_repo, resolve_allowed_path, validate_repo_writable
from app.harness.tools.registry import ToolContext, get_registry
from app.storage.run_store import RunHandle, RunStore


def create_bound_run(root: Path, label: str = "A", *, project_id: str = "pimc",
                     knowledge: bool = True) -> tuple[RunHandle, Path]:
    root = root.resolve()
    code = root / "source"
    (code / "src" / "protected").mkdir(parents=True)
    (code / "baseline.py").write_text(f"BASELINE = {label!r}\n")
    (code / "entry.py").write_text("raise RuntimeError('This preparation must not execute commands')\n")
    (code / "dependency.py").write_text(f"DEPENDENCY = {label!r}\n")
    (code / "src" / "candidate.py").write_text("VALUE = 1\n")
    (code / "src" / "protected" / "interface.py").write_text("CONTRACT = 'unchanged'\n")
    (code / "AGENTS.md").write_text(f"Only project {label} rules; preserve baseline.py.\n")
    (code / ".env").write_text("PRIVATE_SOURCE_SECRET=should_not_copy\n")
    (code / ".git").mkdir()
    (code / ".git" / "config").write_text("must not copy git history")
    (code / "private_data").mkdir()
    (code / "private_data" / "records.json").write_text('{"private": true}')
    note = root / "knowledge.md"
    note.write_text(f"{label} measured quantity is MSE; no recorded observations.\n")
    contract = ProjectContract.model_validate({"project_id": project_id, "display_name": f"Study {label}",
        "paths": {"code": str(code), "knowledge": [str(note)] if knowledge else [],
                  "data": [str(code / "private_data")], "output": str(root / "output")},
        "commands": [{"name": purpose, "purpose": purpose, "executable": sys.executable,
            "arguments": ["entry.py"], "entrypoint_files": ["entry.py"]} for purpose in ("check", "train", "evaluate")],
        "metrics": [{"name": "MSE", "unit": "unitless", "direction": "minimize", "target": 0.0, "tolerance": 0.0}],
        "baseline_files": ["baseline.py"], "allowed_paths": ["src", "baseline.py", "AGENTS.md"],
        "protected_paths": ["src/protected"], "execution": {"kind": "local", "device": "cpu"}})
    frozen = freeze_research_task(contract, goal="Compare actual arithmetic errors", mode="manual", budget=default_research_budget())
    run = RunStore(root / "runs").create(task=label, project=project_id)
    persist_run_research_contract(run, frozen)
    atomic_write_json(run.root / "input/run_request_options.v1.json", {
        "schema_id": "run_request_options.v1", "extra": {"research_task_sha256": frozen.task_sha256}})
    return run, code


def prepare(run: RunHandle, **kwargs: Any) -> ProjectScope:
    return prepare_project_scope(run, candidate_id="candidate_1",
        snapshot_policy=SnapshotPolicy(allowed_paths=("*",)), **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", ["request", "journal", "broken_link"])
async def test_missing_contract_cannot_load_legacy_project_context(tmp_path: Path, marker: str) -> None:
    from app.harness.runtime.run_graph import RunGraph
    from app.storage.run_state_store import RunStateStore

    run, _ = create_bound_run(tmp_path)
    contract = run.root / "input/research_task.v1.json"
    task_hash = run.meta["research_task_sha256"]
    contract.unlink()
    extra: dict[str, Any] = {"run_root": str(run.root), "run_id": run.run_id}
    if marker == "request":
        extra["research_task_sha256"] = task_hash
    elif marker == "journal":
        graph = RunGraph()
        graph.add_node("idea")
        RunStateStore(run).write(graph=graph, request={"extra": {"research_task_sha256": task_hash}},
                                status="created", expected_revision=0)
    else:
        contract.symlink_to(tmp_path / "absent.json")
    with pytest.raises(ValueError, match="host-bound project scope"):
        await ExperimentAgent().build_context(RunRequest(project=run.project, user_request="context check", extra=extra))


def context(scope: ProjectScope, **kwargs: Any) -> ToolContext:
    return ToolContext(run_id=scope.run_id, project=scope.project, agent="coding",
                       extra={"run_root": str(scope.run_root)}, **kwargs)


def diff(path: str, before: str = "VALUE = 1", after: str = "VALUE = 2") -> str:
    return f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-{before}\n+{after}\n"


def test_full_source_read_snapshot_is_separate_from_write_scope_and_data(tmp_path: Path) -> None:
    run, code = create_bound_run(tmp_path)
    scope = prepare(run)
    assert scope.candidate_root != code
    assert {"baseline.py", "dependency.py", "entry.py", "src/candidate.py", "AGENTS.md"} <= set(scope.readable_files)
    assert not (scope.candidate_root / ".env").exists()
    assert not (scope.candidate_root / ".git").exists()
    assert not (scope.candidate_root / "private_data").exists()
    assert scope.resolve_file("dependency.py", must_exist=True).read_text() == "DEPENDENCY = 'A'\n"
    assert verify_snapshot(scope.snapshot_root).manifest.snapshot_id == scope.snapshot_id
    assert (code / "src/candidate.py").stat().st_ino != (scope.candidate_root / "src/candidate.py").stat().st_ino
    with pytest.raises(ValueError, match="write scope"):
        scope.resolve_file("dependency.py", write=True)
    record = json.loads((run.root / "context/project_scope/candidate_1.json").read_text())
    assert record["data"] == [{"reference": "data_0", "access": "not_granted", "fingerprint": None}]
    assert record["knowledge"][0]["sha256"] == hashlib.sha256((tmp_path / "knowledge.md").read_bytes()).hexdigest()
    assert record["execution_admission"] == "blocked"
    admission = research_execution_admission(run)
    assert admission is not None and not admission.ready
    assert prepare(run) == scope


@pytest.mark.parametrize("path", ["baseline.py", "src/protected/interface.py", "AGENTS.md"])
def test_protected_files_win_over_allowed_paths(tmp_path: Path, path: str) -> None:
    run, code = create_bound_run(tmp_path)
    scope = prepare(run)
    original = (code / path).read_bytes()
    with bind_project_scope(scope):
        result = asyncio.run(write_file_tool({"path": path, "content": "changed"}, context(scope)))
        assert not result.ok and "protected" in str(result.error)
        gate = asyncio.run(gate_check("code.write_file", {"path": path, "content": "changed"}, context(scope)))
        assert gate.action == "block"
    assert (scope.candidate_root / path).read_bytes() == original
    assert (code / path).read_bytes() == original


@pytest.mark.parametrize("path", ["../other", "/etc/passwd", "src/../baseline.py", "src\\file.py", "src//file.py", ".env", ".mars_candidate_workspace.json", "private_data/records.json"])
def test_read_and_write_escapes_are_rejected(tmp_path: Path, path: str) -> None:
    run, _code = create_bound_run(tmp_path)
    scope = prepare(run)
    for write in (False, True):
        with pytest.raises(ValueError):
            scope.resolve_file(path, write=write)


@pytest.mark.parametrize("place", ["source_file", "source_directory", "candidate_file", "candidate_directory", "metadata_directory"])
def test_real_symlink_paths_fail_closed(tmp_path: Path, place: str) -> None:
    run, code = create_bound_run(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("PRIVATE = 'outside'\n")
    if place.startswith("source"):
        link = code / "src/link"
        link.symlink_to(outside if place.endswith("file") else tmp_path, target_is_directory=place.endswith("directory"))
        with pytest.raises(ValueError, match="symbolic"):
            prepare(run)
        return
    scope = prepare(run)
    if place == "metadata_directory":
        renamed = scope.metadata_root.with_name("saved_metadata")
        scope.metadata_root.rename(renamed)
        scope.metadata_root.symlink_to(renamed, target_is_directory=True)
        with pytest.raises(ValueError, match="symbolic"):
            current = scope.resolve_file("src/candidate.py")
        return
    link = scope.candidate_root / "src/link"
    link.symlink_to(outside if place.endswith("file") else tmp_path, target_is_directory=place.endswith("directory"))
    name = "src/link" if place.endswith("file") else "src/link/outside.py"
    for write in (False, True):
        with pytest.raises(ValueError, match="symbolic"):
            scope.resolve_file(name, write=write, must_exist=True)
    assert outside.read_text() == "PRIVATE = 'outside'\n"


def test_snapshot_policy_cannot_omit_baselines_or_read_dependencies_declared_as_entrypoints(tmp_path: Path) -> None:
    run, _code = create_bound_run(tmp_path)
    with pytest.raises(ValueError, match="baseline or declared command dependency"):
        prepare_project_scope(run, candidate_id="one", snapshot_policy=SnapshotPolicy(allowed_paths=("src",)))


def test_frozen_baseline_changes_and_candidate_protection_changes_are_rejected(tmp_path: Path) -> None:
    run, code = create_bound_run(tmp_path)
    scope = prepare(run)
    (scope.candidate_root / "baseline.py").write_text("bad baseline")
    with pytest.raises(ValueError, match="protected"):
        prepare(run)
    (code / "baseline.py").write_text("changed original")
    with pytest.raises(ValueError, match="changed"):
        prepare(run)


@pytest.mark.parametrize("kind", ["symlink", "unsupported", "overflow"])
def test_knowledge_must_be_safely_bound_not_silently_dropped(tmp_path: Path, kind: str) -> None:
    run, _code = create_bound_run(tmp_path)
    note = tmp_path / "knowledge.md"
    if kind == "symlink":
        note.unlink()
        note.symlink_to(tmp_path / "source/baseline.py")
    elif kind == "unsupported":
        note.unlink()
        note.mkdir()
        (note / "document.pdf").write_bytes(b"real temporary unsupported format")
    else:
        note.write_text("x" * 4096)
    with pytest.raises(ValueError):
        prepare_project_scope(run, candidate_id="one", snapshot_policy=SnapshotPolicy(allowed_paths=("*",), max_file_bytes=2048))


def test_contextvar_isolates_concurrent_same_name_projects_and_rejects_identity_mismatch(tmp_path: Path) -> None:
    run_a, _ = create_bound_run(tmp_path / "a", "A")
    run_b, _ = create_bound_run(tmp_path / "b", "B")
    a, b = prepare(run_a), prepare(run_b)

    async def exercise(scope: ProjectScope, label: str) -> None:
        with bind_project_scope(scope):
            await asyncio.sleep(0)
            assert current_project_scope(scope.project, scope.run_id) == scope
            assert project_root("pimc") == scope.metadata_root
            assert folder_project("pimc") is None
            repository = load_project_repo("pimc")
            assert resolve_allowed_path(repository, "dependency.py", require_exists=True).read_text() == f"DEPENDENCY = {label!r}\n"
            with pytest.raises(ValueError, match="not admitted"):
                validate_repo_writable(repository)
            # Caller extras cannot reroute a host-bound capability.
            ctx = context(scope, project_repo_root=str(tmp_path))
            ctx.extra["project_repo_root"] = str(tmp_path)
            result = await repo_reader_tool({"path": "dependency.py"}, ctx)
            assert result.ok and isinstance(result.output, dict) and label in result.output["content"]
            with pytest.raises(ValueError, match="different run"):
                current_project_scope("pimc", "other_run")
            assert (await gate_check("code.repo_reader", {"path": "dependency.py"}, replace(ctx, run_id="other"))).action == "block"
            await asyncio.sleep(0)
            assert current_project_scope("pimc") == scope

    async def both() -> None:
        await asyncio.gather(exercise(a, "A"), exercise(b, "B"))
    asyncio.run(both())
    assert current_project_scope("pimc") is None
    with bind_project_scope(a), pytest.raises(ValueError, match="replace"):
        with bind_project_scope(b):
            pass


def test_real_patch_write_delete_and_rollback_touch_only_candidate(tmp_path: Path) -> None:
    run, code = create_bound_run(tmp_path)
    scope = prepare(run)

    async def exercise() -> None:
        with bind_project_scope(scope):
            ctx = context(scope)
            applied = await apply_patch_tool({"diff": diff("src/candidate.py")}, ctx)
            assert applied.ok, applied.error
            assert (scope.candidate_root / "src/candidate.py").read_text() == "VALUE = 2\n"
            assert (code / "src/candidate.py").read_text() == "VALUE = 1\n"
            assert (scope.snapshot_root / "src/candidate.py").read_text() == "VALUE = 1\n"
            restored = await rollback_patch_tool({"rollback_ref": applied.rollback_ref}, ctx)
            assert restored.ok and (scope.candidate_root / "src/candidate.py").read_text() == "VALUE = 1\n"
            written = await write_file_tool({"path": "src/new.py", "content": "NEW = True\n"}, ctx)
            assert written.ok and written.rollback_ref
            deleted = await delete_file_tool({"path": "src/new.py"}, ctx)
            assert deleted.ok and not (scope.candidate_root / "src/new.py").exists()
            assert (await rollback_patch_tool({"rollback_ref": deleted.rollback_ref}, ctx)).ok
            assert (await rollback_patch_tool({"rollback_ref": written.rollback_ref}, ctx)).ok
            assert not (scope.candidate_root / "src/new.py").exists()
            for tool in (run_code_tests_tool, lint_tool):
                result = await tool({}, ctx)
                assert not result.ok and "not admitted" in str(result.error)
    asyncio.run(exercise())


@pytest.mark.parametrize("patch", [
    diff("baseline.py"), diff("../escape.py"),
    'diff --git a/src/new.py b/baseline.py\nsimilarity index 100%\nrename from src/new.py\nrename to baseline.py\n',
    'diff --git a/src/link b/src/link\nnew file mode 120000\n--- /dev/null\n+++ b/src/link\n@@ -0,0 +1 @@\n+/etc/passwd\n',
    'diff --git a/src/file b/src/file\nGIT binary patch\nliteral 3\nabc\n',
    '--- a/src/x\n+++ b/src/x\n@@ -1 +1 @@\n-a\n+b\n--- a/baseline.py\n+++ b/baseline.py\n',
])
def test_ambiguous_or_protected_diff_is_denied_before_git(tmp_path: Path, patch: str) -> None:
    run, _code = create_bound_run(tmp_path)
    scope = prepare(run)
    with bind_project_scope(scope):
        for tool in (patch_generator_tool, apply_patch_tool):
            result = asyncio.run(tool({"diff": patch}, context(scope)))
            assert not result.ok
        assert static_check(project="pimc", tool_name="code.apply_patch", args={"diff": patch}).blocking
    assert (scope.candidate_root / "baseline.py").read_text() == "BASELINE = 'A'\n"


def test_diff_parser_counts_content_and_accepts_actual_unified_variants() -> None:
    assert validated_diff_paths(diff("src/a.py")) == ("src/a.py",)
    assert validated_diff_paths('--- /dev/null\n+++ b/src/new.py\n@@ -0,0 +1 @@\n+text\n') == ("src/new.py",)
    assert validated_diff_paths('--- a/src/delete.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-text\n') == ("src/delete.py",)
    # These are hunk contents, not a second file named baseline.py.
    assert validated_diff_paths('--- a/src/x\n+++ b/src/x\n@@ -1 +1 @@\n--- a/baseline.py\n+++ b/baseline.py\n') == ("src/x",)


def test_gate5_dispatch_blocks_protection_but_does_not_impose_pimc_interface(tmp_path: Path) -> None:
    run, _ = create_bound_run(tmp_path)
    scope = prepare(run)
    with bind_project_scope(scope):
        result = asyncio.run(get_registry().dispatch("code.write_file", {"path": "baseline.py", "content": "changed"}, context(scope)))
        assert not result.ok
        assert static_check(project="pimc", tool_name="code.write_file", args={
            "path": "src/second_domain.py", "content": "def forward(self, x, other_parameter):\n    return x\n"}).blocking is False


def test_rollback_foreign_run_and_protected_tail_cannot_partially_write(tmp_path: Path) -> None:
    run, _ = create_bound_run(tmp_path)
    scope = prepare(run)
    with bind_project_scope(scope):
        ctx = context(scope)
        written = asyncio.run(write_file_tool({"path": "src/candidate.py", "content": "VALUE = 2\n"}, ctx))
        assert written.rollback_ref
        path = Path(written.rollback_ref)
        data = json.loads(path.read_text())
        data["snapshots"].append({"path": "baseline.py", "existed": False, "content": "", "sha256": ""})
        path.write_text(json.dumps(data))
        denied = asyncio.run(rollback_patch_tool({"rollback_ref": str(path)}, ctx))
        assert not denied.ok and "protected" in str(denied.error)
        assert (scope.candidate_root / "src/candidate.py").read_text() == "VALUE = 2\n"
        data["snapshots"].pop()
        data["run_id"] = "another"
        path.write_text(json.dumps(data))
        assert not asyncio.run(rollback_patch_tool({"rollback_ref": str(path)}, ctx)).ok
        outside = tmp_path / "outside.json"
        outside.write_text(json.dumps(data))
        assert not asyncio.run(rollback_patch_tool({"rollback_ref": str(outside)}, ctx)).ok
        assert not asyncio.run(apply_patch_tool({"patch_path": str(outside)}, ctx)).ok
        assert not asyncio.run(apply_patch_tool({"diff": diff("src/candidate.py"), "version": "../../escape"}, ctx)).ok


def test_base_agent_uses_bound_rules_and_declared_knowledge_without_same_name_context(tmp_path: Path) -> None:
    run, _ = create_bound_run(tmp_path, "SECOND_DOMAIN")
    scope = prepare(run)
    agent = ExperimentAgent()
    request = RunRequest(project="pimc", user_request="Inspect inputs without any model call",
        extra={"context_sources": {"agent_resources": False}})
    with bind_project_scope(scope):
        pack = asyncio.run(agent.build_context(request))
        assert "SECOND_DOMAIN" in pack.project and "MSE" in pack.project
        assert "stream_label" not in pack.project and "-26" not in pack.project
        assert "experiment_code_repositories" not in pack.upstream
        assert "approved_memory" not in pack.upstream and pack.metadata["memory"]["state"] == "not_bound"
        assert request.extra["run_root"] == str(scope.run_root)
        assert "project_knowledge" in pack.metadata
        with pytest.raises(ValueError, match="differs"):
            asyncio.run(agent.build_context(RunRequest(project="pimc", user_request="bad route", extra={"run_root": str(tmp_path)})))
    with pytest.raises(ValueError, match="host-bound"):
        asyncio.run(agent.build_context(request))


def test_missing_declared_knowledge_never_inherits_pimc_context(tmp_path: Path) -> None:
    run, _ = create_bound_run(tmp_path, "SECOND_DOMAIN", knowledge=False)
    scope = prepare(run)
    with bind_project_scope(scope):
        pack = asyncio.run(ExperimentAgent().build_context(RunRequest(project="pimc", user_request="Inspect", extra={
            "context_sources": {"agent_resources": False}})))
    assert "SECOND_DOMAIN" in pack.project
    assert "stream_label" not in pack.project and "-26" not in pack.project
    assert "project_knowledge" not in pack.metadata


def test_case_variants_cannot_bypass_protected_subtree(tmp_path: Path) -> None:
    run, _ = create_bound_run(tmp_path)
    scope = prepare(run)
    with pytest.raises(ValueError, match="protected"):
        scope.resolve_file("src/PROTECTED/interface.py", write=True)


def test_hardlinked_candidate_cannot_modify_external_file(tmp_path: Path) -> None:
    run, _ = create_bound_run(tmp_path)
    scope = prepare(run)
    outside = tmp_path / "outside.py"
    outside.write_text("OUTSIDE = True\n")
    (scope.candidate_root / "src/hardlink.py").hardlink_to(outside)
    with bind_project_scope(scope):
        result = asyncio.run(write_file_tool({"path": "src/hardlink.py", "content": "damaged"}, context(scope)))
        assert not result.ok and "hard link" in str(result.error)
    assert outside.read_text() == "OUTSIDE = True\n"


def test_persisted_context_tampering_and_unrelated_knowledge_snapshot_are_rejected(tmp_path: Path) -> None:
    run, _ = create_bound_run(tmp_path)
    scope = prepare(run)
    agent = ExperimentAgent()
    request = RunRequest(project="pimc", user_request="Read known references", extra={"context_sources": {"agent_resources": False}})
    with bind_project_scope(scope):
        asyncio.run(agent.build_context(request))
        snapshot = run.root / "input/project_knowledge.v1.json"
        from app.harness.agent_loop.trace import digest
        raw = json.loads(snapshot.read_text())
        raw["content"] = "A different project's material"
        raw["sha256"] = digest(raw["content"])
        snapshot.write_text(json.dumps(raw))
        with pytest.raises(ValueError, match="differs"):
            asyncio.run(agent.build_context(request))
    (scope.metadata_root / "AGENTS.md").write_text("Changed rules")
    with pytest.raises(ValueError, match="fingerprint"):
        with bind_project_scope(scope):
            pass


def test_bound_patch_ignores_ambient_git_worktree_and_parent_repo(tmp_path: Path) -> None:
    import os
    import subprocess
    from app.settings import repo_root
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    run, code = create_bound_run(tmp_path)
    scope = prepare(run)
    outside = tmp_path / "ambient"
    (outside / "src").mkdir(parents=True)
    (outside / "src/candidate.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "init", "-q", str(outside)], check=True)
    script = '''import asyncio, sys
from pathlib import Path
from app.harness.tools.code import _git_apply
result = asyncio.run(_git_apply(Path(sys.argv[1]), sys.argv[2], check_only=False, isolated=True))
assert result.ok, result.error
'''
    result = subprocess.run([sys.executable, "-c", script, str(scope.candidate_root), diff("src/candidate.py")],
        cwd=repo_root(), env={**os.environ, "PYTHONPATH": str(repo_root() / "backend"),
            "GIT_DIR": str(outside / ".git"), "GIT_WORK_TREE": str(outside)}, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert (scope.candidate_root / "src/candidate.py").read_text() == "VALUE = 2\n"
    assert (code / "src/candidate.py").read_text() == "VALUE = 1\n"
    assert (outside / "src/candidate.py").read_text() == "VALUE = 1\n"


def test_agent_context_storage_symlink_cannot_write_outside_its_run(tmp_path: Path) -> None:
    run, _ = create_bound_run(tmp_path)
    scope = prepare(run)
    outside = tmp_path / "outside-context"
    outside.mkdir()
    (run.root / "context/resources").symlink_to(outside, target_is_directory=True)
    with bind_project_scope(scope), pytest.raises(ValueError, match="symbolic"):
        asyncio.run(ExperimentAgent().build_context(RunRequest(project="pimc", user_request="Inspect", extra={
            "context_sources": {"agent_resources": False}})))
    assert list(outside.iterdir()) == []


def test_agent_run_rejects_another_scope_context_before_any_model_call(tmp_path: Path) -> None:
    run_a, _ = create_bound_run(tmp_path / "a", "A")
    run_b, _ = create_bound_run(tmp_path / "b", "B")
    a, b = prepare(run_a), prepare(run_b)
    agent = ExperimentAgent()
    with bind_project_scope(a):
        pack = asyncio.run(agent.build_context(RunRequest(project="pimc", user_request="Inspect", extra={
            "context_sources": {"agent_resources": False}})))
    with bind_project_scope(b), pytest.raises(ValueError, match="context does not belong"):
        asyncio.run(agent.run_loop(RunRequest(project="pimc", user_request="Wrong context"), pack))


@pytest.mark.parametrize("mutation", ["protected_change", "readonly_change", "readonly_delete", "new_ungranted", "new_private", "new_directory", "mode", "marker"])
def test_independent_full_candidate_audit_rejects_changes_without_trusting_tool_claims(tmp_path: Path, mutation: str) -> None:
    from app.harness.runtime.project_scope import verify_candidate_scope
    run, _ = create_bound_run(tmp_path)
    scope = prepare(run)
    candidate = scope.candidate_root
    if mutation == "protected_change":
        (candidate / "baseline.py").write_text("not baseline\n")
    elif mutation == "readonly_change":
        (candidate / "dependency.py").write_text("unauthorized edit\n")
    elif mutation == "readonly_delete":
        (candidate / "dependency.py").unlink()
    elif mutation == "new_ungranted":
        (candidate / "new.py").write_text("ungranted = True\n")
    elif mutation == "new_private":
        (candidate / "src/.ssh").mkdir()
        (candidate / "src/.ssh/foo.md").write_text("private")
    elif mutation == "new_directory":
        (candidate / "ungranted_empty").mkdir()
    elif mutation == "mode":
        (candidate / "dependency.py").chmod(0o755)
    else:
        marker = candidate / ".mars_candidate_workspace.json"
        raw = json.loads(marker.read_text())
        raw["snapshot_id"] = "other"
        marker.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        verify_candidate_scope(scope)
    with pytest.raises(ValueError):
        prepare(run)


def test_independent_candidate_audit_records_real_authorized_diff_hashes(tmp_path: Path) -> None:
    from app.harness.runtime.project_scope import verify_candidate_scope
    run, _ = create_bound_run(tmp_path)
    scope = prepare(run)
    assert verify_candidate_scope(scope) == ()
    file = scope.candidate_root / "src/candidate.py"
    old = hashlib.sha256(file.read_bytes()).hexdigest()
    file.write_text("VALUE = 42\n")
    created = scope.candidate_root / "src/new.py"
    created.write_text("NEW = 1\n")
    changes = verify_candidate_scope(scope)
    assert [(item.path, item.kind) for item in changes] == [("src/candidate.py", "modified"), ("src/new.py", "created")]
    assert changes[0].before_sha256 == old
    assert changes[0].after_sha256 == hashlib.sha256(file.read_bytes()).hexdigest()
    file.unlink()
    assert verify_candidate_scope(scope)[0].kind == "deleted"
    assert prepare(run) == scope


@pytest.mark.parametrize("nested", [".ssh/foo.md", ".env-dir/foo.md", "context/.ssh/foo.md", ".ENV-DIR/foo.md", "safe/.git/config.md"])
def test_knowledge_sensitive_directory_components_are_blocked(tmp_path: Path, nested: str) -> None:
    run, _ = create_bound_run(tmp_path)
    knowledge = tmp_path / "knowledge.md"
    knowledge.unlink()
    knowledge.mkdir()
    target = knowledge / nested
    target.parent.mkdir(parents=True)
    target.write_text("PRIVATE_MATERIAL_MUST_NOT_BE_FROZEN\n")
    with pytest.raises(ValueError, match="excluded private/control"):
        prepare(run)
    assert not (run.root / "context/project_scope").exists()


def test_candidate_audit_rejects_external_symlink_and_hardlink_anywhere(tmp_path: Path) -> None:
    from app.harness.runtime.project_scope import verify_candidate_scope
    run, _ = create_bound_run(tmp_path)
    scope = prepare(run)
    outside = tmp_path / "outside.py"
    outside.write_text("external data")
    link = scope.candidate_root / "src/alias.py"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="symbolic"):
        verify_candidate_scope(scope)
    link.unlink()
    link.hardlink_to(outside)
    with pytest.raises(ValueError, match="hard link"):
        verify_candidate_scope(scope)


def test_unicode_normalization_alias_cannot_bypass_protected_scope(tmp_path: Path) -> None:
    run, _ = create_bound_run(tmp_path)
    scope = prepare(run)
    protected = scope.candidate_root / "src/caf\u00e9"
    protected.mkdir()
    (protected / "reference.py").write_text("REFERENCE = True\n")
    scoped = replace(scope, protected_paths=(*scope.protected_paths, "src/caf\u00e9"))
    with bind_project_scope(scoped):
        result = asyncio.run(write_file_tool({"path": "src/cafe\u0301/reference.py", "content": "damaged"}, context(scoped)))
        assert not result.ok and "protected" in str(result.error)
    assert (protected / "reference.py").read_text() == "REFERENCE = True\n"


@pytest.mark.parametrize("kind", ["source_data_alias", "knowledge_secret_alias"])
def test_input_hardlink_alias_cannot_copy_undeclared_private_content(tmp_path: Path, kind: str) -> None:
    run, code = create_bound_run(tmp_path)
    if kind == "source_data_alias":
        (code / "src/data_alias.py").hardlink_to(code / "private_data/records.json")
    else:
        private = tmp_path / "private/.ssh/private.md"
        private.parent.mkdir(parents=True)
        private.write_text("AUTHORED_PRIVATE_TEST_MARKER\n")
        note = tmp_path / "knowledge.md"
        note.unlink()
        note.hardlink_to(private)
    with pytest.raises(ValueError, match="hard link"):
        prepare(run)
    assert not (run.root / "context/project_scope").exists()
