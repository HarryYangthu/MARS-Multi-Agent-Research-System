"""Project scope of the public API: same run ids in two projects stay separate; views read only the selected project."""
from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from mars import api
from mars.project import ExecutionContext, Scope, prepare_run

from conftest import make_toy


def prepare(root: Path, run_id: str = "same-run") -> ExecutionContext:
    prepare_run(root / f"runs/{run_id}", root / "tasks/research.yaml", root)
    return ExecutionContext.restore(Scope(root), root / f"runs/{run_id}/execution.json", validate=True)


def test_public_scope_signatures() -> None:
    names = ("start_run", "start_refine", "implement", "resume", "stop", "stop_route", "list_runs", "status", "events",
             "implementation", "route_summaries", "iteration_diff", "codingagent_summary", "baseline_dir", "search_space",
             "evaluate", "execution_environments", "active_jobs", "steps_per_epoch")
    assert api.API_VERSION == 3
    for name in names:
        parameter = inspect.signature(getattr(api, name)).parameters["project_root"]
        assert parameter.default is None and parameter.kind is inspect.Parameter.KEYWORD_ONLY


def test_without_project_there_is_no_workspace(monkeypatch) -> None:
    monkeypatch.delenv("MARS_DEFAULT_PROJECT", raising=False)
    with pytest.raises(ValueError, match="未指定项目"):
        api.list_runs()


def test_same_run_ids_status_events_and_approvals_are_scoped(toy: Path, tmp_path: Path) -> None:
    roots = [toy, make_toy(tmp_path / "second")]
    contexts = [prepare(root) for root in roots]
    assert contexts[0].workspace != contexts[1].workspace
    for root, proposal in zip(roots, ("P00", "P99")):
        run = root / "runs/same-run"
        (run / "events.jsonl").write_text(json.dumps({"ts": "2026-11-04 00:00:00", "stage": "test", "message": root.name}) + "\n")
        (run / "step1/r0").mkdir(parents=True)
        (run / "step1/r0/ranking.json").write_text(json.dumps([{"id": proposal, "score": 1}]))
        assert api.status("same-run", project_root=root)["pending_approval"][0]["id"] == proposal
        assert api.events("same-run", project_root=root)[0]["message"] == root.name
        assert [r["run_id"] for r in api.list_runs(project_root=root)] == ["same-run"]
        with pytest.raises(ValueError, match="当前轮次"):
            api.resume("same-run", approve=["P99" if proposal == "P00" else "P00"], project_root=root)
    (roots[0] / ".mars/project.yaml").write_text("invalid: changed after start\n")
    assert api.status("same-run", project_root=roots[0])["phase"] == "awaiting_approval"
    restored = ExecutionContext.restore(Scope(roots[0]), roots[0] / "runs/same-run/execution.json", validate=True)
    assert restored.binding == contexts[0].binding  # a started run keeps its binding


def test_evaluation_api_is_bound_to_its_project(toy: Path, tmp_path: Path) -> None:
    base = Path(api.baseline_dir(project_root=toy))
    result = api.evaluate(base, "clean", 50, 1, baseline=True, project_root=toy)
    assert result["ok"] and result["metrics"]["params"] == 2
    other = make_toy(tmp_path / "other")
    with pytest.raises(ValueError, match="工作区"):
        api.evaluate(base, "clean", 50, project_root=other)
    space = api.search_space(project_root=toy)
    assert space["keys"] == [{"key": "learning_rate", "type": "float", "low": 0.005, "high": 0.2, "log": True, "declared": True}]
    assert [o["id"] for o in api.execution_environments(project_root=toy)["options"]] == ["local", "remote"]
    with pytest.raises(ValueError, match="steps_per_epoch"):
        api.steps_per_epoch(project_root=toy)


def test_workbench_views_use_the_run_binding(toy: Path) -> None:
    from mars.workspace import Workspace
    context = prepare(toy)
    ws = Workspace(context)
    ws.ensure_base()
    candidate = ws.create_candidate("same-run-P00-i1")
    (candidate / "model.py").write_text("def features(x):\n    return [1.0, x, x * x]\n")
    commit = ws.commit(candidate, "quadratic")
    record = {"iteration": 1, "branch": "cand/same-run-P00-i1", "base_commit": ws.baseline_commit(), "commit": commit,
              "task": {"summary": "quadratic"}, "evidence": {}}
    folder = toy / "runs/same-run/step2/P00/i1"
    folder.mkdir(parents=True)
    (folder / "record.json").write_text(json.dumps(record))
    found = api.iteration_diff("same-run", "P00", 1, project_root=toy)
    assert "+    return [1.0, x, x * x]" in found["files"][0]["patch"]
    assert api.route_summaries("same-run", project_root=toy)[0]["iterations"][0]["candidate_dir"] == str(candidate)
    (toy / ".mars/project.yaml").write_text("schema_version: 2\nrepo: {path: /missing}\n")
    assert api._kernel(toy, workdir=candidate).context.binding["id"] == context.binding["id"]
    api.stop_route("same-run", "P00", project_root=toy)
    assert (toy / "runs/same-run/step2/P00/STOP").is_file()
    with pytest.raises(ValueError):
        api.iteration_diff("same-run", "../../B", 1, project_root=toy)


def test_foreign_artifact_symlink_is_not_read(toy: Path, tmp_path: Path) -> None:
    prepare(toy)
    foreign = tmp_path / "foreign-events.jsonl"
    foreign.write_text('{"message":"must not be read"}\n')
    os.symlink(foreign, toy / "runs/same-run/events.jsonl")
    with pytest.raises(ValueError, match="产物链接"):
        api.events("same-run", project_root=toy)


def test_stop_only_signals_selected_project(toy: Path, tmp_path: Path) -> None:
    roots = [toy, make_toy(tmp_path / "second")]
    for root in roots:
        prepare(root)
    code = "import os,sys,time;from mars.osutil import lock;f=open(sys.argv[1],'a+');lock(f);f.truncate(0);f.write(str(os.getpid()));f.flush();time.sleep(120)"
    children = [subprocess.Popen([sys.executable, "-c", code, str(root / "runs/same-run/run.lock")], cwd=api.ROOT) for root in roots]
    try:
        until = time.monotonic() + 10
        while time.monotonic() < until and not all(api.status("same-run", project_root=root)["running"] for root in roots):
            time.sleep(0.05)
        assert api.stop("same-run", wait_seconds=2, project_root=roots[0])
        children[0].wait(timeout=5)
        assert not api.status("same-run", project_root=roots[0])["running"]
        assert api.status("same-run", project_root=roots[1])["running"]
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)


def test_old_snapshots_are_view_only(toy: Path) -> None:
    run = toy / "runs/old"
    run.mkdir(parents=True)
    (run / "contract.yaml").write_text("title: old\n")
    (run / "execution.json").write_text(json.dumps({"snapshot_version": 1, "project_root": str(toy), "binding": {"id": "x", "config": {"editable_files": ["libs/*"]}}}))
    assert api.status("old", project_root=toy)["phase"] == "starting"
    assert api._editable_files(toy, "old") == ["libs/*"]
    with pytest.raises(ValueError, match="只能查看"):
        api.resume("old", project_root=toy)
