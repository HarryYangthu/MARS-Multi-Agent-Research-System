"""Hyperparameter search (scenario "已有架构，只改配置"): Optuna studies whose trials each call
mars.api.evaluate(..., overrides=...) on a fixed checkout (the baseline or one candidate iteration) and optimize the
project's primary metric in its declared direction over the chosen datasets.

Each study lives in data/hpo/<id>/ (spec.json + Optuna's SQLite storage), so it survives app restarts and can be
resumed. Evaluations take a slot from gpu.SLOTS, shared with simulation verification.
"""
from __future__ import annotations

import json
import re
import statistics
import threading
import time
from pathlib import Path
from typing import Any, Callable

import optuna
from optuna.storages import RDBStorage, fail_stale_trials
from optuna.trial import TrialState

from mars_app.gpu import MAX_PARALLEL, SLOTS

MAX_TRIALS = 200
SEARCHABLE_TYPES = {"float", "int", "str", "bool", "categorical"}
STUDY_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")
RESULT_KEYS = ("ok", "metrics", "elapsed_seconds", "executed_on", "run_dir", "error")

optuna.logging.set_verbosity(optuna.logging.WARNING)


class TrialFailed(Exception):
    """An evaluation that did not produce a result; the reason is in the trial's `error` attribute."""


def _number(value: Any, kind: str) -> float | int:
    number = float(value)
    if kind == "int":
        if number != int(number):
            raise ValueError(f"{value} 不是整数")
        return int(number)
    return number


def _choice(value: Any, kind: str) -> Any:
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if str(value).lower() in ("true", "false"):
            return str(value).lower() == "true"
        raise ValueError(f"{value} 不是 true/false")
    if kind in ("int", "float"):
        return _number(value, kind)
    return str(value)


def normalize_spec(spec: dict[str, Any], space: dict[str, Any], target_exists: Callable[[str], bool]) -> tuple[dict[str, Any], list[str]]:
    """Validate a study request against api.search_space(); returns the cleaned spec and the problems found."""
    problems: list[str] = []
    environment = spec.get("environment")
    if environment is not None and environment not in ("local", "remote"):
        problems.append("执行环境必须是 local 或 remote")
    keys = {k["key"]: k for k in space.get("keys", [])}
    metric = (space.get("metrics") or {}).get("primary")
    better = ((space.get("metrics") or {}).get("fields") or {}).get(metric, {}).get("better", "lower")
    target = spec.get("target") or {}
    kind = target.get("kind")
    if kind not in ("baseline", "candidate"):
        problems.append("评测对象必须是基线或某个候选")
    elif not target.get("dir") or not target_exists(str(target["dir"])):
        problems.append(f"评测目录不存在：{target.get('dir')}")
    datasets = [str(d) for d in spec.get("datasets") or []]
    if not datasets or any(d not in space.get("datasets", []) for d in datasets) or len(set(datasets)) != len(datasets):
        problems.append(f"数据集必须是 {space.get('datasets')} 中的一个或多个")
    try:
        steps, seed = int(spec.get("steps", -1)), int(spec.get("seed", 2026))
        n_trials, parallel = int(spec.get("n_trials", 0)), int(spec.get("parallel", 1))
    except (TypeError, ValueError):
        return spec, problems + ["steps / seed / n_trials / parallel 必须是整数"]
    if steps < 0:
        problems.append("预算不能为负（0 表示完整预算）")
    if not 1 <= n_trials <= MAX_TRIALS:
        problems.append(f"试验次数必须在 1–{MAX_TRIALS} 之间")
    if not 1 <= parallel <= MAX_PARALLEL:
        problems.append(f"并行试验数必须在 1–{MAX_PARALLEL} 之间（GPU 与进化路线共用）")
    params, seen = [], set()
    for raw in spec.get("params") or []:
        key = str(raw.get("key", ""))
        info = keys.get(key)
        if info is None:
            problems.append(f"{key or '（空）'} 不是可调的配置键")
            continue
        if key in seen:
            problems.append(f"{key} 重复")
            continue
        seen.add(key)
        if info.get("type") not in SEARCHABLE_TYPES:
            problems.append(f"{key} 未在项目 search_space 中声明可搜索的类型")
            continue
        mode = raw.get("mode") or ("range" if info["type"] in ("float", "int") else "choices")
        kind_of_value = "str" if info["type"] == "categorical" else info["type"]
        try:
            if mode == "range":
                if info["type"] not in ("float", "int"):
                    raise ValueError("只有数值键能按范围搜索")
                low, high = _number(raw.get("low"), info["type"]), _number(raw.get("high"), info["type"])
                log = bool(raw.get("log"))
                if not low < high:
                    raise ValueError("下限必须小于上限")
                if log and low <= 0:
                    raise ValueError("对数刻度要求下限大于 0")
                params.append({"key": key, "type": info["type"], "mode": "range", "low": low, "high": high, "log": log})
            else:
                choices = [c if info["type"] == "categorical" else _choice(c, kind_of_value) for c in raw.get("choices") or info.get("choices") or []]
                if not choices or len(set(map(repr, choices))) != len(choices):
                    raise ValueError("候选值不能为空或重复")
                params.append({"key": key, "type": info["type"], "mode": "choices", "choices": choices})
        except (TypeError, ValueError) as error:
            problems.append(f"{key}：{error}")
    if not spec.get("params"):
        problems.append("至少设置一个搜索参数")
    cleaned = {"title": str(spec.get("title") or "").strip()[:120], "target": {"kind": kind, "dir": target.get("dir"),
               "label": str(target.get("label") or "")[:200]}, "datasets": datasets, "steps": steps, "seed": seed,
               "n_trials": n_trials, "parallel": parallel, "params": params, "environment": environment,
               "metric": metric, "direction": "minimize" if better == "lower" else "maximize"}
    return cleaned, problems


def suggest(trial: optuna.Trial, param: dict[str, Any]) -> Any:
    if param["mode"] == "range":
        if param["type"] == "int":
            return trial.suggest_int(param["key"], int(param["low"]), int(param["high"]), log=param["log"])
        return trial.suggest_float(param["key"], float(param["low"]), float(param["high"]), log=param["log"])
    return trial.suggest_categorical(param["key"], param["choices"])


def aggregate(values: list[float]) -> float:
    """Objective of a trial: mean of the project's primary metric over the chosen datasets (direction from the spec)."""
    return round(statistics.mean(values), 4)


class HpoManager:
    def __init__(self, root: Path, api: Callable[[str | None], Any]) -> None:
        self.root, self._api = root, api
        self._threads: dict[str, threading.Thread] = {}
        self._stops: dict[str, threading.Event] = {}
        self._storages: dict[str, RDBStorage] = {}
        self._lock = threading.Lock()

    # ----- storage -----
    def _dir(self, sid: str) -> Path:
        if not STUDY_ID.match(sid) or not (self.root / sid / "spec.json").exists():
            raise KeyError(sid)
        return self.root / sid

    def _storage(self, sid: str) -> RDBStorage:
        with self._lock:
            if sid not in self._storages:
                self._storages[sid] = RDBStorage(f"sqlite:///{self.root / sid / 'study.db'}",
                                                 heartbeat_interval=60, grace_period=180)
            return self._storages[sid]

    def _study(self, sid: str) -> optuna.Study:
        return optuna.load_study(study_name=sid, storage=self._storage(sid))

    def spec(self, sid: str) -> dict[str, Any]:
        return json.loads((self._dir(sid) / "spec.json").read_text(encoding="utf-8"))

    # ----- lifecycle -----
    def create(self, spec: dict[str, Any]) -> str:
        sid = time.strftime("%Y%m%d-%H%M%S") + "-" + f"{time.time_ns() & 0xFFFF:04x}"
        (self.root / sid).mkdir(parents=True)
        (self.root / sid / "spec.json").write_text(json.dumps({**spec, "created": time.strftime("%Y-%m-%d %H:%M:%S")},
                                                               ensure_ascii=False, indent=1), encoding="utf-8")
        optuna.create_study(study_name=sid, storage=self._storage(sid), direction=spec.get("direction", "minimize"))
        self.start(sid)
        return sid

    def running(self, sid: str) -> bool:
        thread = self._threads.get(sid)
        return bool(thread and thread.is_alive())

    def start(self, sid: str) -> None:
        spec = self.spec(sid)
        with self._lock:
            if self.running(sid):
                raise RuntimeError("这个寻优正在进行")
            stop = threading.Event()
            thread = threading.Thread(target=self._work, args=(sid, spec, stop), name=f"hpo-{sid}", daemon=True)
            self._stops[sid], self._threads[sid] = stop, thread
            thread.start()

    def stop(self, sid: str) -> None:
        self._dir(sid)
        if not self.running(sid):
            raise RuntimeError("这个寻优没有在进行")
        self._stops[sid].set()

    # ----- the search -----
    def _evaluate(self, spec: dict[str, Any], dataset: str, overrides: dict[str, Any] | None) -> dict[str, Any]:
        with SLOTS:
            result = self._api(spec.get("project_root")).evaluate(spec["target"]["dir"], dataset, spec["steps"], spec["seed"],
                                          baseline=spec["target"]["kind"] == "baseline", overrides=overrides,
                                          **({"environment": spec["environment"]} if spec.get("environment") else {}))
        return {k: result.get(k) for k in RESULT_KEYS}

    def _work(self, sid: str, spec: dict[str, Any], stop: threading.Event) -> None:
        study = self._study(sid)
        fail_stale_trials(study)  # trials cut off by an app restart; they do not count against the budget
        if "reference" not in study.user_attrs:
            try:
                reference = {d: self._evaluate(spec, d, None) for d in spec["datasets"]}
                values = [r["metrics"][spec["metric"]] for r in reference.values() if r.get("ok")]
                study.set_user_attr("reference", {"datasets": reference,
                                                  "value": aggregate(values) if len(values) == len(reference) else None})
            except Exception as error:  # noqa: BLE001 - recorded; the search still runs without a reference
                study.set_user_attr("reference", {"error": f"{type(error).__name__}: {error}"[:800]})
        counted = [t for t in study.get_trials(deepcopy=False)
                   if t.state == TrialState.COMPLETE or (t.state == TrialState.FAIL and "error" in t.user_attrs)]
        remaining = int(spec["n_trials"]) - len(counted)
        if remaining <= 0 or stop.is_set():
            return

        def objective(trial: optuna.Trial) -> float:
            overrides = {p["key"]: suggest(trial, p) for p in spec["params"]}
            values = []
            for dataset in spec["datasets"]:
                try:
                    result = self._evaluate(spec, dataset, overrides)
                except Exception as error:  # noqa: BLE001 - e.g. ValueError for a key the core rejects
                    trial.set_user_attr("error", f"{dataset}：{type(error).__name__}: {error}"[:800])
                    raise TrialFailed() from error
                trial.set_user_attr(f"d{dataset}", result)
                if not result.get("ok"):
                    trial.set_user_attr("error", f"{dataset}：{result.get('error') or '评测失败'}"[:800])
                    raise TrialFailed()
                values.append(float(result["metrics"][spec["metric"]]))
            return aggregate(values)

        def halt(study: optuna.Study, _: Any) -> None:
            if stop.is_set():
                study.stop()  # trials already training on the GPU finish first

        study.optimize(objective, n_trials=remaining, n_jobs=int(spec["parallel"]), callbacks=[halt],
                       catch=(TrialFailed,), gc_after_trial=False)

    # ----- views -----
    def _trial(self, trial: optuna.trial.FrozenTrial) -> dict[str, Any]:
        started, completed = trial.datetime_start, trial.datetime_complete
        state = trial.state.name.lower()
        if trial.state == TrialState.FAIL and "error" not in trial.user_attrs:
            state = "interrupted"
        return {"number": trial.number, "state": state, "value": trial.value, "params": trial.params,
                "datasets": {k[1:]: v for k, v in trial.user_attrs.items() if k.startswith("d")},
                "error": trial.user_attrs.get("error"),
                "started": started.strftime("%Y-%m-%d %H:%M:%S") if started else None,
                "seconds": round((completed - started).total_seconds()) if started and completed else None}

    def detail(self, sid: str) -> dict[str, Any]:
        spec = self.spec(sid)
        study = self._study(sid)
        trials = [self._trial(t) for t in study.get_trials(deepcopy=False)]
        complete = [t for t in trials if t["state"] == "complete"]
        maximize = spec.get("direction") == "maximize"
        best = (max if maximize else min)(complete, key=lambda t: t["value"]) if complete else None
        reference = study.user_attrs.get("reference")
        running = self.running(sid)
        stopping = running and self._stops[sid].is_set()
        counted = sum(1 for t in trials if t["state"] in ("complete", "fail"))
        return {"id": sid, "spec": spec, "running": running, "stopping": stopping,
                "state": "stopping" if stopping else "running" if running else
                         "finished" if counted >= spec["n_trials"] else "paused",
                "counts": {s: sum(1 for t in trials if t["state"] == s) for s in ("complete", "fail", "running", "interrupted")},
                "reference": reference, "best": best,
                "best_gain": round((best["value"] - reference["value"]) * (1 if maximize else -1), 6)
                if best and reference and reference.get("value") is not None else None,
                "trials": trials}

    def list(self) -> list[dict[str, Any]]:
        items = []
        for path in sorted(self.root.glob("*/spec.json"), reverse=True):
            sid = path.parent.name
            try:
                d = self.detail(sid)
            except (KeyError, ValueError, OSError, optuna.exceptions.OptunaError):
                continue
            items.append({k: d[k] for k in ("id", "spec", "state", "counts", "best", "best_gain")})
        return items
