from __future__ import annotations

from mars_app import hpo

SPACE = {"datasets": ["b", "a"], "metrics": {"primary": "error", "fields": {"error": {"better": "lower"}}}, "keys": [
    {"key": "learning_rate", "type": "float", "low": 1e-4, "high": 1e-2, "log": True, "declared": True},
    {"key": "batch_size", "type": "int", "low": 2, "high": 16, "declared": True},
    {"key": "loss_type", "type": "categorical", "choices": ["sqrt", "mse"], "declared": True},
    {"key": "layout", "declared": False}]}


def _spec(**changes: object) -> dict:
    spec = {"title": "lr", "target": {"kind": "baseline", "dir": "/base"}, "datasets": ["a"], "steps": 304, "seed": 2026,
            "n_trials": 4, "parallel": 2,
            "params": [{"key": "learning_rate", "mode": "range", "low": "1e-4", "high": "3e-3", "log": True},
                       {"key": "batch_size", "mode": "choices", "choices": ["4", "8"]},
                       {"key": "loss_type", "choices": ["sqrt", "mse"]}]}
    return {**spec, **changes}


def _check(spec: dict) -> tuple[dict, list[str]]:
    return hpo.normalize_spec(spec, SPACE, lambda d: d == "/base")


def test_valid_spec_is_normalized() -> None:
    cleaned, problems = _check(_spec())
    assert problems == []
    lr, batch, loss = cleaned["params"]
    assert lr == {"key": "learning_rate", "type": "float", "mode": "range", "low": 1e-4, "high": 3e-3, "log": True}
    assert batch["choices"] == [4, 8] and loss == {"key": "loss_type", "type": "categorical", "mode": "choices", "choices": ["sqrt", "mse"]}
    assert cleaned["metric"] == "error" and cleaned["direction"] == "minimize"


def test_spec_problems() -> None:
    _, problems = _check(_spec(target={"kind": "baseline", "dir": "/elsewhere"}, datasets=["c"], n_trials=0, parallel=3,
                               params=[{"key": "learning_rate", "mode": "range", "low": 0, "high": 1, "log": True},
                                       {"key": "batch_size", "mode": "choices", "choices": ["4.5"]},
                                       {"key": "layout", "choices": ["x"]},
                                       {"key": "nope", "low": 1, "high": 2}]))
    joined = "；".join(problems)
    for expected in ("评测目录不存在", "数据集必须是", "试验次数", "并行试验数必须在 1–2", "对数刻度要求下限大于 0",
                     "4.5 不是整数", "layout 未在项目 search_space 中声明", "nope 不是可调的配置键"):
        assert expected in joined, expected
    _, problems = _check(_spec(params=[{"key": "batch_size", "mode": "range", "low": 8, "high": 4}]))
    assert problems == ["batch_size：下限必须小于上限"]
    _, problems = _check(_spec(params=[]))
    assert problems == ["至少设置一个搜索参数"]


def test_aggregate_is_mean_of_the_primary_metric() -> None:
    assert hpo.aggregate([4.0, 5.0]) == 4.5
