from __future__ import annotations

from mars_app import verify

METRICS = {"primary": "error", "fields": {"error": {"better": "lower"}, "accuracy": {"better": "higher"}, "size": {"better": "lower"}}}
CONTRACT = {"targets": {"gates": [{"metric": "error", "max_worse": 0.0}, {"metric": "accuracy", "max_worse": 0.01}]}}


def _eval(error: float, accuracy: float, run_dir: str) -> dict:
    return {"ok": True, "metrics": {"error": error, "accuracy": accuracy, "size": 10}, "run_dir": run_dir, "plots": ["curve.png"]}


def _job(error: float, accuracy: float, gates_ok: bool = True, steps: int = 1000) -> dict:
    return {"steps": steps, "metrics": METRICS,
            "implementation": {"ok": True, "gates": {"all": gates_ok, "stop": False, "gates": []}},
            "results": {"a": {"baseline": _eval(4.9, 0.80, "/b"), "candidate": _eval(error, accuracy, "/c")}}}


def test_compare_lines_up_candidate_and_baseline() -> None:
    result = verify.compare(_job(4.8, 0.795), CONTRACT)
    row = result["datasets"]["a"]
    assert row["metrics"]["error"] == {"candidate": 4.8, "baseline": 4.9, "worse_by": -0.1, "max_worse": 0.0, "ok": True}
    assert row["metrics"]["accuracy"]["worse_by"] == 0.005 and row["metrics"]["accuracy"]["ok"] is True  # higher is better
    assert row["metrics"]["size"]["ok"] is None  # not gated: shown for reference
    assert row["plots"] == {"candidate": ["/c/curve.png"], "baseline": ["/b/curve.png"]}
    assert result["all_ok"] is True and result["full_budget"] is False


def test_compare_flags_each_failed_target() -> None:
    result = verify.compare(_job(5.3, 0.70, steps=0), CONTRACT)
    row = result["datasets"]["a"]
    assert row["metrics"]["error"]["ok"] is False and row["metrics"]["accuracy"]["ok"] is False
    assert result["all_ok"] is False and result["full_budget"] is True
    assert verify.compare(_job(4.8, 0.80, gates_ok=False), CONTRACT)["all_ok"] is False  # a failed check gate
    pending = verify.compare({"steps": 1000, "implementation": None, "results": {}}, CONTRACT)
    assert pending["all_ok"] is False and pending["datasets"] == {}


def test_compact_implementation_keeps_summary_short() -> None:
    impl = {"ok": False, "candidate_dir": "/w/c", "checks": {"build": {"ok": False, "error": "shape"}}, "summary": "x" * 9000,
            "turns": [{"turn": 0, "status": "completed", "final_text": "long", "violations": ["src/train.py"]}]}
    out = verify.compact_implementation(impl)
    assert out["checks"]["build"]["ok"] is False and len(out["summary"]) == 4000
    assert out["turns"] == [{"turn": 0, "status": "completed", "seconds": None, "violations": ["src/train.py"],
                             "reverted": None, "error": None}]
