"""Generic workspace and Step2 measurement on the toy project: real git, real evaluation commands, no model calls."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from mars import project
from mars.step2 import check_gates, field
from mars.workspace import EvalJob, Workspace, worse_by

QUADRATIC = "def features(x):\n    return [1.0, x, x * x]\n"


def workspace(root: Path) -> Workspace:
    report, ctx = project.inspect(root)
    assert report["ready"]["local"], [c for c in report["checks"] if c["status"] == "fail"]
    ws = Workspace(ctx)
    ws.ensure_base()
    return ws


def candidate(ws: Workspace, name: str, code: str = QUADRATIC) -> Path:
    path = ws.create_candidate(name)
    (path / "model.py").write_text(code, encoding="utf-8")
    ws.commit(path, name)
    return path


def test_inspection_reads_the_declaration_only(toy: Path) -> None:
    report, ctx = project.inspect(toy)
    binding = report["binding"]
    assert report["ready"] == {"local": True, "remote": False}
    assert [d["id"] for d in binding["datasets"]] == ["clean", "noisy"] and binding["metrics"]["primary"] == "val_mse"
    assert binding["checks"] == ["build"] and not (toy / "workspace/bindings").exists()  # read-only
    assert ctx.binding["config"]["prompts"]["domain"].startswith("示例项目")
    assert ctx.binding["documents"][0]["kind"] == "project_context"


@pytest.mark.parametrize("change, message", [
    ({"schema_version": 1}, "schema_version 必须为 2"),
    ({"evaluate": {"command": "{python} run.py {gpu}"}}, "未知占位符"),
    ({"metrics": {"primary": "loss", "fields": {"val_mse": {"better": "lower"}}}}, "metrics.primary"),
    ({"metrics": {"primary": "val_mse", "fields": {"val_mse": {}}}}, "better"),
    ({"datasets": [{"name": "a/b", "data": "data/clean.csv"}]}, "datasets[].name"),
    ({"api_key": "x"}, "凭据"),
])
def test_invalid_declarations_are_reported(toy: Path, change: dict, message: str) -> None:
    path = toy / ".mars/project.yaml"
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    path.write_text(yaml.safe_dump({**spec, **change}, allow_unicode=True), encoding="utf-8")
    report, ctx = project.inspect(toy)
    assert ctx is None and not report["ready"]["local"]
    assert any(message in c["message"] for c in report["checks"] if c["status"] == "fail")


def test_evaluation_runs_the_project_command_and_caches(toy: Path) -> None:
    ws = workspace(toy)
    base = ws.evaluate(EvalJob(ws.base, "clean", 200, 1, baseline=True))
    assert base["ok"] and base["metrics"]["params"] == 2 and base["curve"]
    again = ws.evaluate(EvalJob(ws.base, "clean", 200, 1, baseline=True))
    assert again["cache_identity"] == base["cache_identity"] and again["run_dir"] == base["run_dir"]
    cand = candidate(ws, "quad")
    result = ws.evaluate(EvalJob(cand, "clean", 200, 1))
    assert result["ok"] and result["metrics"]["params"] == 3
    assert worse_by(ws.metrics["fields"]["val_mse"], result["metrics"]["val_mse"], base["metrics"]["val_mse"]) < 0
    # candidate-only edits do not change the baseline's identity
    assert ws.baseline_commit("HEAD", cand) == ws.baseline_commit()
    tuned = ws.evaluate(EvalJob(cand, "clean", 200, 1, overrides={"learning_rate": 0.1}))
    assert tuned["cache_identity"] != result["cache_identity"]
    with pytest.raises(ValueError, match="不允许的配置键"):
        ws.evaluate(EvalJob(cand, "clean", 200, 1, overrides={"batch_size": 8}))
    with pytest.raises(ValueError, match="未在本项目声明"):
        ws.evaluate(EvalJob(cand, "other", 200, 1))


def test_failed_command_and_missing_primary_metric_are_failures(toy: Path) -> None:
    ws = workspace(toy)
    bad = candidate(ws, "broken", "def features(x):\n    raise ValueError('nope')\n")
    result = ws.evaluate(EvalJob(bad, "clean", 50, 1))
    assert not result["ok"] and "nope" in result["error"]
    assert not (Path(result["run_dir"]) / "mars_result.json").exists()  # failures are not cached
    assert ws.checks(bad, "implement")["build"]["ok"] is False


def test_scope_and_project_checks(toy: Path) -> None:
    ws = workspace(toy)
    cand = ws.create_candidate("scope")
    (cand / "model.py").write_text(QUADRATIC, encoding="utf-8")
    (cand / "train.py").write_text("print('changed')\n", encoding="utf-8")
    assert ws.check_scope(cand) == ["修改了不允许的文件：train.py"]
    subprocess.run(["git", "checkout", "train.py"], cwd=cand, check=True)
    assert ws.check_scope(cand) == []
    assert ws.checks(cand, "implement") == ws.checks(cand, "measure") == {"build": {"ok": True, "params": 3, "message": ""}}


def test_check_gates_and_field_paths() -> None:
    results = {"size": {"layers": {"a": {"ratio": 0.6}, "b": {"ratio": 0.8}}}, "build": {"params": 3}}
    assert field(results["size"], "layers.*.ratio") == [0.6, 0.8]
    gates = check_gates([{"check": "size", "field": "layers.*.ratio", "max": 0.7, "stop": True},
                         {"check": "build", "field": "params", "max": 4}], results)
    assert gates["all"] is False and gates["stop"] is True and gates["gates"][1]["ok"] is True
    missing = check_gates([{"check": "build", "field": "size", "min": 1}], results)
    assert missing["all"] is False and missing["stop"] is False  # a missing value fails its gate


def test_pack_change_blocks_resume_and_folder_can_move(toy: Path, tmp_path: Path) -> None:
    ws = workspace(toy)
    ws.evaluate(EvalJob(ws.base, "clean", 50, 1, baseline=True))
    snapshot = ws.workspace / "binding.json"
    moved = tmp_path / "moved project"
    toy.rename(moved)
    ctx = project.ExecutionContext.restore(project.Scope(moved), moved / snapshot.relative_to(toy), validate=True)
    again = Workspace(ctx)
    result = again.evaluate(EvalJob(again.base, "clean", 50, 1, baseline=True))
    assert result["ok"] and Path(result["run_dir"]).is_relative_to(moved)  # cached result follows the folder
    (moved / ".mars/pack/check.py").write_text("print('{\"ok\": true}')\n", encoding="utf-8")
    with pytest.raises(ValueError, match=".mars/pack 已改变"):
        ctx.validate()


def test_measure_screen_and_verify_with_declared_gates(toy: Path) -> None:
    from mars.run import Run
    from mars.step2 import Evolver
    task = yaml.safe_load((toy / "tasks/research.yaml").read_text(encoding="utf-8"))
    task["budget"].update(prequick={"dataset": "clean", "steps": 100, "gates": [{"metric": "val_mse", "max": 1.0}]},
                          screen={"steps": 200, "seeds": [1, 2], "margin_floor": 0.0001}, verify={"steps": 300, "seed": 1})
    (toy / "tasks/small.yaml").write_text(yaml.safe_dump(task, allow_unicode=True), encoding="utf-8")
    run = Run.open(toy / "runs/demo", toy / "tasks/small.yaml", project_root=toy)
    evolver = Evolver(run)
    cand = candidate(run.kernel, "demo-P00-i1")
    evidence = evolver._measure({"id": "P00"}, cand, "i1", {})
    assert evidence["check_gates"]["all"] and evidence["prequick"]["passed"]
    assert set(evidence["screen"]) == {"clean", "noisy"} and evidence["screen_passed"]
    assert evidence["screen"]["clean"]["worse_by"]["val_mse"] < 0 and evidence["verify"]["passed"]
    assert evidence["verify"]["clean"]["gates"] == {"val_mse": True}
    baseline = run.load("step2/baseline_screen.json")
    assert len(baseline["clean"]["primary_seeds"]) == 2 and baseline["clean"]["margin"] >= 0.0001
    wide = candidate(run.kernel, "demo-P00-i2", "def features(x):\n    return [1.0, x, x*x, x**3, x**4, x**5]\n")
    stopped = evolver._measure({"id": "P00"}, wide, "i2", {})
    assert stopped["stopped_at"] == "checks" and stopped["check_gates"]["gates"][0]["values"] == [6]


def test_route_notes_reach_the_next_decision_once(toy: Path, monkeypatch) -> None:
    from mars import api
    from mars.run import Run
    from mars.schemas import Decision
    from mars.step2 import Evolver
    run = Run.open(toy / "runs/demo", toy / "tasks/research.yaml", project_root=toy)
    (run.run_dir / "step2/U00").mkdir(parents=True)
    api.add_route_note("demo", "U00", "下一轮试试三次项", project_root=toy)
    assert json.loads((run.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()[-1])["route"] == "U00"
    prompts = []

    def answer(role, messages, schema, tag=""):
        prompts.append(messages[0]["content"])
        return Decision(verdict="continue", mechanism_assessment="m", evidence_for=[], evidence_against=[],
                        alternative_explanations=[], rationale="r",
                        operator_notes=[{"note": "复述", "adopted": True, "reason": "与证据一致"}])
    monkeypatch.setattr(run.llm, "structured", answer)
    evolver = Evolver(run)
    route = {"id": "U00", "proposal": {"mechanism_hypothesis": "h"}, "history": [], "refuted_streak": 0, "iteration": 0}
    record = {"iteration": 1, "implementation": {"turns": []}, "evidence": {}}
    first = evolver._decide(route, record)
    assert "下一轮试试三次项" in prompts[0] and "示例项目" in prompts[0]  # notes and the project's domain text
    assert first["operator_notes"][0] == {"note": "下一轮试试三次项", "adopted": True, "reason": "与证据一致"}
    evolver._decide(route, record)
    assert "下一轮试试三次项" not in prompts[1].split("# 证据说明")[0]  # answered notes are not read again


def test_prepare_run_rejects_undeclared_metrics_and_checks(toy: Path) -> None:
    task = yaml.safe_load((toy / "tasks/research.yaml").read_text(encoding="utf-8"))
    for targets, message in (({"gates": [{"metric": "loss"}]}, "metrics.fields"), ({"checks": [{"check": "size", "field": "x"}]}, "项目 checks")):
        (toy / "tasks/bad.yaml").write_text(yaml.safe_dump({**task, "targets": targets}, allow_unicode=True), encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            project.prepare_run(toy / "runs/bad", toy / "tasks/bad.yaml", toy)
