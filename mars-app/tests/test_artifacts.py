from __future__ import annotations

import json
from pathlib import Path

import pytest

from mars_app import artifacts, core
from mars_app.launcher import validate_contract

CORE_API = Path(__file__).resolve().parents[2] / "mars-core/mars/api.py"


def _run(tmp_path: Path, events: list[dict], name: str = "demo") -> Path:
    run = tmp_path / name
    run.mkdir(parents=True)
    (run / "contract.yaml").write_text("id: t\ntitle: 测试\nbudget: {routes: 1}\n", encoding="utf-8")
    (run / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
    return run


def _event(message: str, stage: str = "step2", index: int = 0) -> dict:
    return {"index": index, "ts": "2026-10-23 10:00:00", "stage": stage, "message": message}


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _item(pid: str, precheck: list[str] | None = None) -> dict:
    return {"proposal": {"id": pid, "title": f"方案 {pid}", "one_sentence": f"{pid} 一句话", "evidence": [{"id": "E00"}]},
            "critiques": [{"verdict": "revise", "issues": [{"severity": "fatal"}, {"severity": "major"}]}],
            "rebuttals": [{"issue": "x", "response": "y"}], "precheck": precheck or []}


GATES = {"gates": [{"metric": "error", "max_worse": 0.0}, {"metric": "band", "max_worse": 0.0}]}


def test_recommendation_uses_complete_verification_and_the_worst_dataset() -> None:
    def row(k, error, band, screen):
        return {"iteration": k, "datasets": ["a", "b"],
                "screen": {d: {"primary": "error", "worse_by": {"error": screen}} for d in ("a", "b")},
                "verify": {d: {"worse_by": {"error": error, "band": band}, "gates": {"error": True, "band": True}} for d in ("a", "b")}}
    good, broken_checkpoint = row(4, 0.002, 0.008, 0.35), row(6, 0.039, 18.0, 0.2)
    routes = [{"id": "P31", "iterations": [good, broken_checkpoint]}]
    assert artifacts.recommended_iteration(routes, GATES) == {"route": "P31", "iteration": 4}
    assert artifacts.iteration_score(good, {"gates": [{"metric": "band", "max_worse": -0.1}]})[1] == pytest.approx(0.108)
    uneven = row(5, -1, -1, -1)
    uneven["verify"]["b"]["worse_by"]["band"] = 0.5
    assert artifacts.iteration_score(good, GATES) < artifacts.iteration_score(uneven, GATES)
    assert artifacts.recommended_iteration([], {}) is None


@pytest.mark.parametrize("invalid", [None, float("nan"), float("inf")])
def test_missing_or_nonfinite_measurements_do_not_win(invalid) -> None:
    complete = {"datasets": ["a", "b"], "verify": {d: {"worse_by": {"error": 0, "band": 0.1}} for d in ("a", "b")}}
    partial = {**complete, "verify": {"a": complete["verify"]["a"], "b": {"worse_by": {"error": 0, "band": invalid}}}}
    assert artifacts.iteration_score(complete, GATES) < artifacts.iteration_score(partial, GATES)
    assert artifacts.iteration_score(complete, GATES) < artifacts.iteration_score({"datasets": ["a", "b"], "verify": {}}, GATES)


def test_followups_are_scoped_to_the_run_and_reports_folder(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    path = reports / "demo/followups.json"
    group = {"kind": "seeds", "route": "P31", "iteration": 4, "rows": []}
    _write(path, {"run_id": "demo", "experiments": [group]})
    assert artifacts.followups(reports, "demo") == [group]
    _write(path, {"run_id": "another-project", "experiments": [group]})
    assert artifacts.followups(reports, "demo") == []
    path.unlink()
    outside = tmp_path / "private.json"
    _write(outside, {"run_id": "demo", "experiments": [group]})
    try:
        path.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    assert artifacts.followups(reports, "demo") == []


def test_derive_state() -> None:
    events = [_event("Step1 第 0 轮开始", "graph"), _event("评审排序完成", "step1", 1)]
    assert artifacts.derive_state("starting", running=False, events=[]) == "new"
    assert artifacts.derive_state("step1", running=True, events=events) == "running"
    assert artifacts.derive_state("awaiting_approval", running=False, events=events) == "waiting_approval"
    assert artifacts.derive_state("step2", running=False, events=events) == "stopped"
    assert artifacts.derive_state("finished", running=False, events=events) == "finished"


def test_alerts() -> None:
    quota = {"ts": "2026-10-24 04:02:27", "message": "P21 第 6 轮决策调用失败", "error": "余额不足", "kind": "model_quota"}
    health = {"last_event_ts": "2026-10-24 04:03:05", "seconds_since_last_event": 33577, "last_failure": quota}
    codes = lambda state, h: [a["code"] for a in artifacts.alerts(state, h)]  # noqa: E731
    assert codes("stopped", health) == ["model_quota", "stopped"]
    assert codes("running", health) == ["model_quota", "stalled"]
    assert codes("finished", health) == []
    # The run kept going for hours after the failure: no longer the current cause.
    recovered = {**health, "last_event_ts": "2026-10-24 09:00:00", "seconds_since_last_event": 60}
    assert codes("running", recovered) == []
    # A progress message that merely mentions failed requests is not a model failure.
    search = {"last_event_ts": "2026-10-23 02:41:34", "seconds_since_last_event": 600,
              "last_failure": {"ts": "2026-10-23 02:41:00", "message": "检索完成：失败请求 18", "kind": "other"}}
    assert codes("running", search) == []
    assert codes("running", {**search, "seconds_since_last_event": 3 * 3600}) == ["stalled"]
    assert codes("waiting_approval", {"last_event_ts": "x", "seconds_since_last_event": 900}) == ["awaiting_approval"]


def test_approval_candidates_and_selection(tmp_path: Path) -> None:
    run = _run(tmp_path, [])
    ranking = [{"id": "P12", "title": "方案 P12", "score": 7.1, "precheck": []},
               {"id": "P11", "title": "方案 P11", "score": 6.9, "precheck": [], "duplicate_of": "P12"},
               {"id": "P10", "title": "方案 P10", "score": 6.0, "precheck": ["参数超限"]}]
    _write(run / "step1/r1/debate.json", [_item("P10"), _item("P11"), _item("P12")])
    _write(run / "step1/r1/judged_proposals.json", [_item("P10", ["参数超限"]), _item("P11"), _item("P12")])
    pending = artifacts.approval(run, "r1", ranking, artifacts.contract(run))
    assert pending["round"] == "r1"
    assert [c["id"] for c in pending["candidates"]] == ["P12", "P11", "P10"]
    assert pending["default_selection"] == ["P12"]  # budget.routes = 1; duplicate and failed precheck skipped
    first = pending["candidates"][0]
    assert first["one_sentence"] == "P12 一句话" and first["critiques"]["fatal"] == 1 and first["rebuttals"] == 1
    assert artifacts.validate_selection(["P12", "P10"], pending) == []
    assert artifacts.validate_selection([], pending) == ["至少选择一个方案"]
    assert "不在本轮排序中的方案：['P00']" in artifacts.validate_selection(["P00"], pending)
    (run / "step1/r1/judged_proposals.json").unlink()  # older runs: fall back to the debate output
    assert artifacts.approval(run, "r1", ranking, {})["candidates"][2]["one_sentence"] == "P10 一句话"


def test_routes_status_and_rows(tmp_path: Path) -> None:
    run = _run(tmp_path, [])
    events = [_event("P00 第 1 轮：开始实现"), _event("P00 第 1 轮完成：active", index=1), _event("P00 第 2 轮：开始实现", index=2)]
    _write(run / "step2/P00/i1/record.json", {
        "iteration": 1, "task": {"summary": "换结构"}, "implementation": {"ok": True},
        "evidence": {"stopped_at": "prequick", "check_gates": {"all": True, "stop": False, "gates": [
                         {"check": "size", "field": "ratio", "max": 0.7, "values": [0.69], "ok": True}]},
                     "prequick": {"passed": False, "metrics": {"error": 3.1}},
                     "screen": {"a": {"metrics": {"error": 3.0}, "worse_by": {"error": 0.2}, "primary": "error", "passed": False}}},
        "decision": {"verdict": "continue", "failed": True, "mechanism_assessment": "决策调用失败：余额不足", "next_change": None}})
    (route,) = artifacts.routes(run, events)
    assert route["status"] == "active" and route["running_iteration"] == 2
    row = route["iterations"][0]
    assert row["check_gates"]["all"] is True and row["datasets"] == ["a"]  # only datasets actually measured
    assert row["screen_gap"] == {"a": 0.2} and row["prequick"]["passed"] is False and row["decision_error"] is True
    assert artifacts.summary(run, {"phase": "step2", "running": True}, events)["routes"][0]["status"] == "active"
    assert artifacts.summary(run, {"phase": "step2", "running": False}, events)["routes"][0]["status"] == "interrupted"



def test_route_events_prefer_structured_fields(tmp_path: Path) -> None:
    run = _run(tmp_path, [])
    (run / "step2/U00").mkdir(parents=True)
    # Structured fields win even when the message wording differs; older runs still parse from the text.
    events = [{**_event("U00 iteration 1 started"), "route": "U00", "iteration": 1, "status": "implementing"},
              {**_event("U00 finished", index=1), "route": "U00", "iteration": 1, "status": "budget_exhausted"}]
    assert artifacts.routes(run, events)[0]["status"] == "budget_exhausted"
    started = [{**_event("anything", index=2), "route": "U00", "iteration": 2, "status": "implementing"}]
    assert artifacts.routes(run, events + started)[0]["running_iteration"] == 2
    assert artifacts.routes(run, [_event("U00 第 1 轮完成：refuted")])[0]["status"] == "refuted"

def test_record_rejects_path_traversal(tmp_path: Path) -> None:
    run = _run(tmp_path, [])
    assert artifacts.record(run, "../secret", 1) is None


@pytest.mark.skipif(not CORE_API.exists(), reason="mars-core checkout not next to mars-app")
def test_core_events_through_real_facade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real mars.api reading real temp files in a project folder: indices continue from `since`."""
    module = core.load(CORE_API.parents[1])
    monkeypatch.setenv("MARS_DEFAULT_PROJECT", str(tmp_path))  # the facade works inside a project folder
    _run(tmp_path / "runs", [{"ts": "t", "stage": "graph", "message": m} for m in ("a", "b", "c")])
    assert [(e["index"], e["message"]) for e in core.events(module, "demo", since=1)] == [(1, "b"), (2, "c")]
    path = tmp_path / "runs/demo/events.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"ts": "t", "stage": "graph", "mes')
    assert [e["index"] for e in core.events(module, "demo", since=2)] == [2]  # half-written line held back
    with path.open("a", encoding="utf-8") as handle:
        handle.write('sage": "d"}\n')
    assert [(e["index"], e["message"]) for e in core.events(module, "demo", since=3)] == [(3, "d")]


def test_validate_contract() -> None:
    assert validate_contract(["x"]) == ["研究约定必须是一个 YAML 映射"]
    problems = validate_contract({"title": "t", "objective": "o", "targets": [], "budget": {}})
    assert "targets 必须是映射" in problems and "缺少字段 budget" in problems
    problems = validate_contract({"title": "t", "objective": "o", "budget": {"routes": 1},
                                  "targets": {"gates": [{"metric": "error", "max_worse": "x"}], "checks": [{"check": "size"}]}})
    assert any("targets.gates[0]" in p for p in problems) and any("targets.checks[0]" in p for p in problems)


def test_route_notes_show_the_decision_answer(tmp_path: Path) -> None:
    run = _run(tmp_path, [])
    route = run / "step2/U00"
    route.mkdir(parents=True)
    (route / "notes.jsonl").write_text('{"ts": "t1", "text": "试试更小的学习率"}\n{"ts": "t2", "text": "别再改初始化"}\n', encoding="utf-8")
    _write(route / "i2/record.json", {"iteration": 2, "decision": {"operator_notes": [
        {"note": "试试更小的学习率", "adopted": True, "reason": "与梯度爆炸的诊断一致"}]}})
    notes = artifacts.route_notes(run, "U00")
    assert notes[0]["answer"]["adopted"] is True and notes[0]["answer"]["iteration"] == 2
    assert "answer" not in notes[1]  # not read by a decision yet
    _write(route / "i3/record.json", {"iteration": 3, "decision": {"operator_notes": [
        {"note": "不要改初始化", "adopted": False, "reason": "初始化是本轮的诊断对象"}]}})  # quoted loosely
    assert artifacts.route_notes(run, "U00")[1]["answer"]["iteration"] == 3
    assert artifacts.route_notes(run, "../x") == []
