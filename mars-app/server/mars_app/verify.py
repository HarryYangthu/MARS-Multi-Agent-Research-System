"""Simulation verification (scenario "成熟方案，只做实现和适配"): the user's design text goes to mars.api.implement,
then the app evaluates the resulting candidate and the baseline on the same budget with api.evaluate(details=True)
and shows them side by side, metric by metric as the project declares them.

A job lives in data/verify/<id>/job.json and moves implementing → evaluating → done | failed (or interrupted when
the app or the core process stopped). Resuming is cheap: the core caches every evaluation by code and config.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

import yaml

from mars_app.gpu import SLOTS

JOB_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")
POLL_SECONDS = 15
LAUNCH_GRACE_SECONDS = 90  # the core process takes its run lock shortly after start_run / implement returns
RESULT_KEYS = ("ok", "metrics", "missing_metrics", "curve", "info", "plots", "checks", "elapsed_seconds", "executed_on",
               "run_dir", "error")
IMPLEMENTATION_KEYS = ("ok", "candidate_dir", "branch", "commit", "changed_files", "violations", "checks",
                       "measure_checks", "gates")


def compact_result(result: dict[str, Any]) -> dict[str, Any]:
    return {k: result.get(k) for k in RESULT_KEYS}


def compact_implementation(impl: dict[str, Any]) -> dict[str, Any]:
    out = {k: impl.get(k) for k in IMPLEMENTATION_KEYS}
    out["turns"] = [{k: t.get(k) for k in ("turn", "status", "seconds", "violations", "reverted", "error")}
                    for t in impl.get("turns") or []]
    out["summary"] = str(impl.get("summary") or "")[-4000:]
    return out


def worse_by(better: str, candidate: Any, baseline: Any) -> float | None:
    """How much the candidate is worse than the baseline in the metric's direction (negative = better)."""
    if not isinstance(candidate, (int, float)) or not isinstance(baseline, (int, float)):
        return None
    return round(candidate - baseline if better == "lower" else baseline - candidate, 6)


def compare(job: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    """Candidate vs baseline per dataset and declared metric, checked against the contract's metric gates and the
    implementation's check gates (all numbers come from the core; this only lines them up)."""
    metrics = job.get("metrics") or {"primary": None, "fields": {}}
    gates = {g["metric"]: float(g.get("max_worse", 0.0)) for g in (contract.get("targets") or {}).get("gates") or []}
    check_gates = (job.get("implementation") or {}).get("gates") or {}
    rows = {}
    for dataset, pair in (job.get("results") or {}).items():
        cand, base = pair.get("candidate") or {}, pair.get("baseline") or {}
        values = {}
        for name, meta in metrics["fields"].items():
            c, b = (cand.get("metrics") or {}).get(name), (base.get("metrics") or {}).get(name)
            delta = worse_by(meta.get("better", "lower"), c, b)
            values[name] = {"candidate": c, "baseline": b, "worse_by": delta, "max_worse": gates.get(name),
                            "ok": None if name not in gates or delta is None else delta <= gates[name]}
        rows[dataset] = {"metrics": values,
                         "plots": {role: [str(Path(r["run_dir"]) / n) for n in r.get("plots") or []] if r.get("run_dir") else []
                                   for role, r in (("candidate", cand), ("baseline", base))}}
    oks = [v["ok"] for row in rows.values() for v in row["metrics"].values() if v["ok"] is not None]
    return {"metrics": metrics, "datasets": rows, "check_gates": check_gates,
            "all_ok": bool(oks) and all(oks) and check_gates.get("all") is not False,
            "full_budget": int(job.get("steps", -1)) == 0}


class VerifyManager:
    def __init__(self, root: Path, runs_dir: Path, api: Callable[[str | None], Any]) -> None:
        self.root, self.runs_dir, self._api = root, runs_dir, api
        self._threads: dict[str, threading.Thread] = {}
        self._stops: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    # ----- persistence -----
    def _path(self, vid: str) -> Path:
        if not JOB_ID.match(vid) or not (self.root / vid / "job.json").exists():
            raise KeyError(vid)
        return self.root / vid / "job.json"

    def load(self, vid: str) -> dict[str, Any]:
        return json.loads(self._path(vid).read_text(encoding="utf-8"))

    def _save(self, job: dict[str, Any]) -> None:
        path = self.root / job["id"] / "job.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(job, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        tmp.replace(path)  # readers never see a half-written job

    # ----- lifecycle -----
    def create(self, title: str, task_path: Path, spec: str, datasets: list[str], steps: int, seed: int,
               parent: str, environment: str | None = None, project_root: str | None = None) -> str:
        if environment is not None and environment not in ("local", "remote"):
            raise ValueError("执行环境必须是 local 或 remote")
        vid = time.strftime("%Y%m%d-%H%M%S") + "-" + f"{time.time_ns() & 0xFFFF:04x}"
        run_id = f"verify-{vid}"
        (self.root / vid).mkdir(parents=True)
        job = {"id": vid, "title": title or spec.strip().splitlines()[0][:80], "run_id": run_id, "task": str(task_path),
               "spec": spec, "parent": parent, "datasets": datasets, "steps": steps, "seed": seed,
               "created": time.strftime("%Y-%m-%d %H:%M:%S"), "started_at": time.time(),
               "stage": "implementing", "error": None, "implementation": None, "results": {}, "environment": environment,
               "project_root": project_root}
        self._save(job)
        try:
            self._api(project_root).implement(task_path, spec, run_id=run_id, parent=parent,
                                  **({"environment": environment} if environment else {}))  # str = the text itself
        except Exception as error:  # noqa: BLE001 - recorded on the job; nothing was started
            job.update(stage="failed", error=f"无法启动实现：{type(error).__name__}: {error}"[:800])
            self._save(job)
            return vid
        self._start(vid)
        return vid

    def running(self, vid: str) -> bool:
        thread = self._threads.get(vid)
        return bool(thread and thread.is_alive())

    def _start(self, vid: str) -> None:
        with self._lock:
            if self.running(vid):
                raise RuntimeError("这个验证正在进行")
            stop = threading.Event()
            thread = threading.Thread(target=self._work, args=(vid, stop), name=f"verify-{vid}", daemon=True)
            self._stops[vid], self._threads[vid] = stop, thread
            thread.start()

    def resume(self, vid: str) -> None:
        job = self.load(vid)
        if job["stage"] == "done":
            raise RuntimeError("这个验证已经完成")
        api = self._api(job.get("project_root"))
        if job["implementation"] is None and api.implementation(job["run_id"]) is None \
                and not api.status(job["run_id"]).get("running"):
            api.resume(job["run_id"])  # the core reruns the implementation from its saved spec
        job.update(stage="implementing" if job["implementation"] is None else "evaluating", error=None,
                   started_at=time.time())
        self._save(job)
        self._start(vid)

    def stop(self, vid: str) -> None:
        job = self.load(vid)
        if not self.running(vid):
            raise RuntimeError("这个验证没有在进行")
        self._stops[vid].set()
        if job["stage"] == "implementing":
            self._api(job.get("project_root")).stop(job["run_id"])  # ends CodingAgent and its gateway; the candidate branch is kept

    # ----- the job -----
    def _work(self, vid: str, stop: threading.Event) -> None:
        job = self.load(vid)
        api = self._api(job.get("project_root"))
        try:
            while job["implementation"] is None:
                impl = api.implementation(job["run_id"])
                if impl is not None:
                    job["implementation"] = compact_implementation(impl)
                    break
                launched_recently = time.time() - float(job.get("started_at", 0)) < LAUNCH_GRACE_SECONDS
                if stop.is_set() or not (api.status(job["run_id"]).get("running") or launched_recently):
                    job.update(stage="interrupted", error="实现进程已结束但没有产出结果（被停止或中途退出）")
                    return
                stop.wait(POLL_SECONDS)
            if not job["implementation"].get("ok"):
                job.update(stage="failed", error="实现没有通过检查（改动范围或项目检查），见实现详情")
                return
            job["stage"] = "evaluating"
            job.setdefault("metrics", api.search_space()["metrics"])  # the project's metric declaration, kept with the job
            self._save(job)
            baseline_dir = str(api.baseline_dir())
            for dataset in job["datasets"]:
                pair = job["results"].setdefault(dataset, {})
                for role, directory, is_baseline in (("baseline", baseline_dir, True),
                                                     ("candidate", job["implementation"]["candidate_dir"], False)):
                    if (pair.get(role) or {}).get("ok"):
                        continue
                    if stop.is_set():
                        job.update(stage="interrupted", error="已停止；继续时会接着评测")
                        return
                    with SLOTS:
                        result = api.evaluate(directory, dataset, int(job["steps"]), int(job["seed"]),
                                              baseline=is_baseline, details=True,
                                              **({"environment": job["environment"]} if job.get("environment") else {}))
                    pair[role] = compact_result(result)
                    self._save(job)
            failed = [f"{d} {role}：{r.get('error')}" for d, pair in job["results"].items()
                      for role, r in pair.items() if not r.get("ok")]
            job.update(stage="failed" if failed else "done", error="；".join(failed)[:1200] or None)
        except Exception as error:  # noqa: BLE001 - e.g. the core rejected a request; shown on the job
            job.update(stage="failed", error=f"{type(error).__name__}: {error}"[:1200])
        finally:
            self._save(job)

    # ----- views -----
    def detail(self, vid: str) -> dict[str, Any]:
        job = self.load(vid)
        runs = Path(job["project_root"]) / "runs" if job.get("project_root") else self.runs_dir
        contract_file = runs / job["run_id"] / "contract.yaml"
        try:
            contract = yaml.safe_load(contract_file.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            contract = {}
        running = self.running(vid)
        stage = job["stage"]
        if not running and stage in ("implementing", "evaluating"):
            stage = "interrupted"  # the app restarted while the job was in flight
        return {**job, "stage": stage, "running": running, "targets": contract.get("targets") or {},
                "comparison": compare(job, contract)}

    def list(self) -> list[dict[str, Any]]:
        items = []
        for path in sorted(self.root.glob("*/job.json"), reverse=True):
            try:
                d = self.detail(path.parent.name)
            except (KeyError, ValueError, OSError):
                continue
            items.append({k: d[k] for k in ("id", "title", "run_id", "stage", "running", "datasets", "steps", "created", "error")}
                         | {"all_ok": d["comparison"]["all_ok"] if d["stage"] == "done" else None})
        return items
