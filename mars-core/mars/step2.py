"""Step 2 — analysis-driven evolution of each handed-off proposal (one route per proposal).

Per iteration: CodingAgent implements one change in an isolated candidate branch → scope and project checks with repair
turns → commit → measure-time checks and their gates → optional prequick → screen on every dataset against the seed
noise floor of the primary metric → automatic diagnostics → the strong model judges the mechanism and specifies the next
discriminating change. Passing the screen and the check gates triggers full-budget verification against the baseline,
judged by the contract's metric gates. Metrics, datasets, checks and gates all come from the project declaration.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import os
import shlex
import shutil
import statistics
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from mars.gateway import LocalGateway
from mars.workspace import EvalJob, git, running_job, worse_by
from mars.run import Run, render
from mars.schemas import Decision
from mars.codingagent import PermissionPolicy, CodingAgentSession

ALLOWED_COMMANDS = ["python ", "python3 ", "git status", "git diff", "git log", "ls",
                    "cat ", "head ", "tail ", "grep ", "rg ", "sed -n", "wc ", "find ", "pytest"]


def field(value: Any, path: str) -> list[Any]:
    """Values at a dotted path; "*" matches every key of a mapping (e.g. layers.*.size)."""
    items = [value]
    for part in path.split("."):
        items = [v for item in items if isinstance(item, dict)
                 for v in (item.values() if part == "*" else [item[part]] if part in item else [])]
    return items


def check_gates(gates: list[dict[str, Any]], results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Contract gates on check outputs: {check, field, max|min, stop}. A missing value fails its gate."""
    rows = []
    for gate in gates:
        values = field(results.get(gate["check"], {}), gate["field"])
        ok = bool(values) and all(isinstance(v, (int, float)) and ("max" not in gate or v <= gate["max"])
                                  and ("min" not in gate or v >= gate["min"]) for v in values)
        rows.append({**gate, "values": values, "ok": ok})
    return {"gates": rows, "all": all(r["ok"] for r in rows), "stop": any(not r["ok"] and r.get("stop") for r in rows)}


class Evolver:
    def __init__(self, run: Run) -> None:
        self.run, self.kernel, self.budget = run, run.kernel, run.contract["budget"]
        self.targets = run.contract.get("targets") or {}
        self.datasets = list(self.kernel.cfg["datasets"])
        self.metrics = self.kernel.metrics
        self.primary = self.metrics["primary"]
        self._baseline_lock = threading.Lock()

    def _worse(self, name: str, candidate: float, baseline: float) -> float:
        return round(worse_by(self.metrics["fields"][name], candidate, baseline), 4)

    # ---------- baselines (cached by the kernel) ----------
    def baseline(self, stage: str) -> dict[str, Any]:
        with self._baseline_lock:
            return self._baseline(stage)

    def _baseline(self, stage: str) -> dict[str, Any]:
        cache = f"step2/baseline_{stage}.json"
        if self.run.has(cache):
            return self.run.load(cache)
        base = self.kernel.base
        summary = {}
        if stage == "screen":
            s = self.budget["screen"]
            results = self.kernel.evaluate_many([EvalJob(base, d, s["steps"], seed, baseline=True, label=self._label("baseline", None, stage))
                                                 for d in self.datasets for seed in s["seeds"]])
            for d in self.datasets:
                runs = [r for r in results if r["dataset"] == d and r["ok"]]
                if not runs:
                    raise RuntimeError(f"基线在数据集 {d} 上评测失败：{next(r.get('error') for r in results if r['dataset'] == d)}")
                values = [r["metrics"][self.primary] for r in runs]
                # Robust statistics: one unconverged seed must not inflate the noise margin (median and 1.4826*MAD).
                median = statistics.median(values)
                spread = 1.4826 * statistics.median(abs(v - median) for v in values)
                summary[d] = {"metrics": {name: statistics.median(r["metrics"][name] for r in runs if isinstance(r["metrics"].get(name), (int, float)))
                                          for name in self.metrics["fields"] if any(isinstance(r["metrics"].get(name), (int, float)) for r in runs)},
                              "primary_seeds": values, "spread": round(spread, 4), "statistic": "median/MAD",
                              "outlier_seeds": [v for v in values if abs(v - median) > max(3 * spread, 1e-12) and spread > 0],
                              "margin": round(max(float(s.get("margin_floor", 0.0)), 2 * spread), 4), "run_dir": runs[0]["run_dir"],
                              "plots": runs[0].get("plots", [])}
        else:
            v = self.budget["verify"]
            for r in self.kernel.evaluate_many([EvalJob(base, d, v["steps"], v["seed"], baseline=True, label=self._label("baseline", None, stage))
                                                for d in self.datasets]):
                if not r["ok"]:
                    raise RuntimeError(f"完整基线在数据集 {r['dataset']} 上评测失败：{r.get('error')}")
                summary[r["dataset"]] = {"metrics": r["metrics"], "run_dir": r["run_dir"], "plots": r.get("plots", [])}
        self.run.save(cache, summary)
        self.run.event("step2", f"基线 {stage} 就绪", summary={k: v["metrics"] for k, v in summary.items()})
        return summary

    def _label(self, route_id: str, tag: str | None, stage: str) -> dict[str, Any]:
        return {"run_id": self.run.run_dir.name, "route": route_id, "iteration": int(tag[1:]) if tag else None, "stage": stage}

    # ---------- implementation through CodingAgent ----------
    def _implement(self, route: dict[str, Any], workdir: Path, task: dict[str, Any], tag: str,
                   runtime: Path | None = None) -> dict[str, Any]:
        runtime = runtime or self.run.path(f"step2/{route['id']}/{tag}/codingagent")
        cfg = self.kernel.cfg
        python, quote = cfg["python"], subprocess.list2cmdline if os.name == "nt" else shlex.join
        from mars.project import fill
        commands = {name: quote(fill(spec["command"], self.kernel.values(workdir))) for name, spec in cfg["checks"].items()}
        prompt = render("implement", proposal=route["proposal"], task=task, constraints=self.run.contract.get("constraints", []),
                        targets=self.targets, history=self._history_brief(route), checks=commands or "（项目未声明检查）",
                        editable=cfg["editable_files"], config_keys=cfg["editable_config_keys"],
                        domain_rules=cfg["prompts"].get("implement") or "（项目未提供编码规则）")
        data_roots = [Path(spec["data"]) if Path(spec["data"]).is_dir() else Path(spec["data"]).parent for spec in cfg["datasets"].values()]
        policy = PermissionPolicy([workdir, runtime, self.kernel.pack, Path(python).parents[1], *data_roots],
                                  ALLOWED_COMMANDS + [python + " ", quote([python]) + " "])  # the project's python
        turns: list[dict[str, Any]] = []

        async def session_loop() -> None:
            with LocalGateway(self.run.llm, "coder", runtime / "gateway") as gateway:
                session = CodingAgentSession(workdir, runtime, gateway.endpoint, gateway.token, gateway.model, policy,
                                       runtime / "codingagent.jsonl")
                try:
                    await session.start()
                    message = prompt
                    for turn in range(int(self.budget["repair_turns"]) + 1):
                        result = await session.send(message, timeout=60 * float(self.budget["codingagent_minutes"]))
                        reverted = self._revert_out_of_scope(workdir)
                        violations = self.kernel.check_scope(workdir)
                        checks = self.kernel.checks(workdir, "implement")
                        turns.append({"turn": turn, "status": result.status, "seconds": round(result.seconds, 1),
                                      "final_text": result.final_text[-1500:], "reverted": reverted,
                                      "violations": violations, "checks": checks, "denials": list(session.denials)})
                        if result.status in ("completed", "timeout") and not violations and all(c.get("ok") for c in checks.values()):
                            return
                        message = render("repair", violations=violations, checks=checks, reverted=reverted,
                                         status=result.status, error=result.error)
                finally:
                    await session.close()

        try:
            with running_job({"kind": "codingagent", **self._label(route["id"], tag, "implement"), "target": "local"}, self.kernel.jobs):
                asyncio.run(session_loop())
        except Exception as error:  # noqa: BLE001 - recorded as an implementation failure, the route continues
            turns.append({"error": f"{type(error).__name__}: {error}"[:800]})
        final = turns[-1] if turns else {}
        ok = "checks" in final and all(c.get("ok") for c in final["checks"].values()) and not final.get("violations")
        return {"ok": ok, "turns": turns}

    def implement_once(self, spec: str, parent: str = "mars/base") -> dict[str, Any]:
        """Simulation-verification entry: implement a given design once (CodingAgent + checks + repair turns), commit it and
        run the measure-time checks. No training and no decision; evaluate the returned checkout with api.evaluate."""
        if self.run.has("implement/result.json"):
            return self.run.load("implement/result.json")
        name = f"{self.run.run_dir.name}-impl"
        self.kernel.remove_candidate(name)  # a previous attempt that was interrupted mid-edit starts over cleanly
        workdir = self.kernel.create_candidate(name, parent)
        title = spec.strip().splitlines()[0][:120] if spec.strip() else "user design"
        route = {"id": "IMPL", "proposal": {"title": title, "implementation_spec": spec}, "history": []}
        task = {"summary": title, "implementation_spec": spec, "kind": "initial"}
        self.run.event("implement", "开始实现", parent=parent)
        implementation = self._implement(route, workdir, task, "i1", runtime=self.run.path("implement/codingagent"))
        commit = self.kernel.commit(workdir, f"MARS implement: {title[:60]}")
        final = implementation["turns"][-1] if implementation["turns"] else {}
        result = {"ok": implementation["ok"], "candidate_dir": str(workdir), "branch": f"cand/{name}", "commit": commit,
                  "changed_files": self.kernel.changed_files(workdir), "violations": final.get("violations"),
                  "checks": final.get("checks"), "turns": [{k: v for k, v in t.items() if k != "final_text"}
                                                           for t in implementation["turns"]],
                  "summary": final.get("final_text", "")}
        if implementation["ok"]:
            measured = self.kernel.checks(workdir, "measure")
            result["measure_checks"], result["gates"] = measured, check_gates(self.targets.get("checks") or [], measured)
        self.run.save("implement/result.json", result)
        self.run.event("implement", f"实现{'完成' if result['ok'] else '失败'}", candidate_dir=str(workdir), commit=commit[:10])
        return result

    def _revert_out_of_scope(self, workdir: Path) -> list[str]:
        """Restore files the agent is not allowed to change (scope is also re-checked by kernel.check_scope)."""
        reverted = []
        for path in self.kernel.changed_files(workdir):
            if not self.kernel.editable(path):
                if git(workdir, "ls-files", path):
                    git(workdir, "checkout", "HEAD", "--", path)
                else:
                    (workdir / path).unlink(missing_ok=True)
                reverted.append(path)
        return reverted

    # ---------- measurement ----------
    def _diagnose(self, workdir: Path, spec: dict[str, Any], output: str | None = None) -> dict[str, Any]:
        """A diagnostic declared by the project, run by the system (on the baseline's or the candidate's screen output)."""
        declared = self.kernel.cfg["diagnostics"].get(spec.get("name", ""))
        if declared is None:
            return {**spec, "error": f"项目没有声明诊断 {spec.get('name')}；可用 {sorted(self.kernel.cfg['diagnostics'])}"}
        if declared.get("source") == "baseline":
            workdir, output = self.kernel.base, next(iter(self.baseline("screen").values()))["run_dir"]
        extra = {"output": output} if output else {}
        if spec.get("args"):  # passed like evaluation overrides: a JSON file
            from mars.workspace import digest
            args = self.run.path(f"step2/diagnostic-args/{digest(spec['args'])}.json")
            args.write_text(json.dumps(spec["args"], ensure_ascii=False), encoding="utf-8")
            extra["overrides"] = str(args)
        return {**spec, "result": self.kernel.command("diagnostics", spec["name"], workdir, **extra)}

    def _metric_rows(self, result: dict[str, Any], reference: dict[str, float]) -> dict[str, Any]:
        return {name: self._worse(name, result["metrics"][name], reference[name])
                for name in self.metrics["fields"] if isinstance(result["metrics"].get(name), (int, float)) and name in reference}

    def _measure(self, route: dict[str, Any], workdir: Path, tag: str, task: dict[str, Any] | None = None) -> dict[str, Any]:
        task = task or {}
        evidence: dict[str, Any] = {}
        checks = self.kernel.checks(workdir, "measure")
        gates = check_gates(self.targets.get("checks") or [], checks)
        evidence["checks"], evidence["check_gates"] = checks, gates
        if gates["stop"]:
            evidence["stopped_at"] = "checks"
            return evidence
        if task.get("diagnostics"):
            evidence["diagnostics"] = [self._diagnose(workdir, d) for d in task["diagnostics"]]
        pq = self.budget.get("prequick")
        if pq:
            pre = self.kernel.evaluate(EvalJob(workdir, pq["dataset"], pq["steps"], 2026, label=self._label(route["id"], tag, "prequick")))
            rows = [{**g, "value": (pre.get("metrics") or {}).get(g["metric"])} for g in pq.get("gates") or []]
            for row in rows:
                row["ok"] = isinstance(row["value"], (int, float)) and ("max" not in row or row["value"] <= row["max"]) and ("min" not in row or row["value"] >= row["min"])
            evidence["prequick"] = {"ok": pre["ok"], "error": pre.get("error"), "metrics": pre.get("metrics"),
                                    "curve": pre.get("curve", []), "gates": rows, "passed": bool(pre["ok"] and all(r["ok"] for r in rows))}
            if not evidence["prequick"]["passed"]:
                evidence["stopped_at"] = "prequick"
                return evidence
        s = self.budget["screen"]
        screen_base = self.baseline("screen")
        results = self.kernel.evaluate_many([EvalJob(workdir, d, s["steps"], s["seeds"][0], label=self._label(route["id"], tag, "screen"))
                                             for d in self.datasets])
        screen, passed = {}, True
        for r in results:
            b = screen_base[r["dataset"]]
            if not r["ok"]:
                screen[r["dataset"]] = {"ok": False, "error": r.get("error")}
                passed = False
                continue
            worse = self._metric_rows(r, b["metrics"])
            ok = worse[self.primary] <= b["margin"]
            screen[r["dataset"]] = {"metrics": r["metrics"], "baseline_metrics": b["metrics"], "worse_by": worse,
                                    "primary": self.primary, "margin": b["margin"], "passed": ok,
                                    "curve_tail": r.get("curve", [])[-5:], "info": r.get("info"), "run_dir": r["run_dir"], "plots": r.get("plots", [])}
            passed &= ok
        evidence["screen"], evidence["screen_passed"] = screen, passed
        first = next((x for x in screen.values() if x.get("run_dir")), None)
        auto = [{"name": name, "question": spec.get("description", "")} for name, spec in self.kernel.cfg["diagnostics"].items() if spec.get("auto")]
        if first and auto:
            evidence["auto_diagnostics"] = [self._diagnose(workdir, spec, first["run_dir"]) for spec in auto]
        # The screen is a short-budget proxy whose ranking can flip at full budget, so near-misses also get the full verify.
        window = float(self.budget["verify"].get("window", 0.0))
        near = bool(screen) and all("worse_by" in x and x["worse_by"][self.primary] <= max(x["margin"], window) for x in screen.values())
        if (passed or near) and gates["all"]:
            evidence["verify"] = self._verify(workdir, self._label(route["id"], tag, "verify"))
        evidence["plots"] = self._keep_plots(route["id"], tag, evidence)
        return evidence

    def _verify(self, workdir: Path, label: dict[str, Any] | None = None) -> dict[str, Any]:
        v = self.budget["verify"]
        base = self.baseline("verify")
        gates = self.targets.get("gates") or [{"metric": self.primary, "max_worse": 0.0}]
        results = self.kernel.evaluate_many([EvalJob(workdir, d, v["steps"], v["seed"], label=label) for d in self.datasets])
        report, passed = {}, True
        for r in results:
            b = base[r["dataset"]]
            if not r["ok"]:
                report[r["dataset"]] = {"ok": False, "error": r.get("error")}
                passed = False
                continue
            worse = self._metric_rows(r, b["metrics"])
            checks = {g["metric"]: g["metric"] in worse and worse[g["metric"]] <= float(g.get("max_worse", 0.0)) for g in gates}
            report[r["dataset"]] = {"metrics": r["metrics"], "baseline_metrics": b["metrics"], "worse_by": worse, "gates": checks,
                                    "passed": all(checks.values()), "run_dir": r["run_dir"], "plots": r.get("plots", [])}
            passed &= all(checks.values())
        report["passed"] = passed
        return report

    def _keep_plots(self, route_id: str, tag: str, evidence: dict[str, Any]) -> dict[str, str]:
        """Copy evaluation plots into the run (workspace/evals may be cleaned or absent elsewhere):
        step2/<route>/<tag>/plots/{stage}_{dataset}_{name} and baselines under step2/plots/."""
        kept = {}
        for stage in ("screen", "verify"):
            for dataset, item in (evidence.get(stage) or {}).items():
                for name in item.get("plots", []) if isinstance(item, dict) else []:
                    source = Path(item["run_dir"]) / name
                    if source.exists():
                        key = f"{stage}_{dataset}_{name}"
                        target = self.run.path(f"step2/{route_id}/{tag}/plots/{key}")
                        shutil.copyfile(source, target)
                        kept[key] = f"plots/{key}"
            summary = self.run.load(f"step2/baseline_{stage}.json") if self.run.has(f"step2/baseline_{stage}.json") else {}
            for dataset, item in summary.items():
                for name in item.get("plots", []):
                    source, target = Path(item["run_dir"]) / name, self.run.path(f"step2/plots/baseline_{stage}_{dataset}_{name}")
                    if source.exists() and not target.exists():
                        shutil.copyfile(source, target)
        return kept

    # ---------- the route loop ----------
    def _history_brief(self, route: dict[str, Any]) -> list[dict[str, Any]]:
        brief = []
        for item in route["history"]:
            e = item.get("evidence", {})
            brief.append({"iteration": item["iteration"], "change": item["task"].get("summary", "")[:300],
                          "implemented": item["implementation"]["ok"], "stopped_at": e.get("stopped_at"),
                          "failed_check_gates": [{k: g[k] for k in ("check", "field", "values")} for g in (e.get("check_gates") or {}).get("gates", []) if not g["ok"]],
                          "screen_worse_by": {d: x.get("worse_by") for d, x in e.get("screen", {}).items()},
                          "verify_worse_by": {d: x.get("worse_by") for d, x in e.get("verify", {}).items() if isinstance(x, dict)},
                          "diagnostics": [{k: v for k, v in x.items() if k in ("name", "args", "result", "error")}
                                          for x in e.get("diagnostics", [])],
                          "decision": item.get("decision", {}).get("verdict"),
                          "assessment": item.get("decision", {}).get("mechanism_assessment", "")[:300]})
        return brief

    def new_route(self, package: dict[str, Any]) -> dict[str, Any]:
        return {"id": package["id"], "proposal": package["proposal"], "iteration": 0, "parent": "mars/base",
                "best": None, "history": [], "status": "active", "refuted_streak": 0}

    def step(self, route: dict[str, Any]) -> dict[str, Any]:
        """Advance one route by exactly one iteration (idempotent per iteration via saved artifacts)."""
        if route["status"] != "active":
            return route
        k = route["iteration"] + 1
        tag = f"i{k}"
        record_path = f"step2/{route['id']}/{tag}/record.json"
        if self.run.has(record_path):
            record = self.run.load(record_path)
        elif self.run.has(f"step2/{route['id']}/STOP"):  # operator request (api.stop_route): end before a new iteration
            route["status"] = "stopped"
            self.run.event("step2", f"{route['id']} 按操作者要求停止（已完成 {route['iteration']} 轮）",
                           reason=(self.run.run_dir / f"step2/{route['id']}/STOP").read_text(encoding="utf-8").strip())
            return route
        else:
            if k > 1 and route["history"][-1].get("decision", {}).get("failed"):
                previous = route["history"][-1]   # a transient model failure: redo that decision before moving on
                previous["decision"] = self._decide(route, previous)
                self.run.save(f"step2/{route['id']}/i{k - 1}/record.json", previous)
                if previous["decision"].get("failed"):
                    raise RuntimeError(f"{route['id']}: 决策仍然失败，暂停（可用 resume 继续）")
                self._apply_decision(route, previous["decision"])
                if route["status"] != "active":  # the redone verdict ends the route: no empty next iteration
                    self.run.event("step2", f"{route['id']} 第 {k - 1} 轮决策重做后：{route['status']}", route=route["id"],
                                   iteration=k - 1, status=route["status"], decision=previous["decision"].get("verdict"))
                    return route
            if k == 1:
                p = route["proposal"]
                task = {"summary": p["one_sentence"], "implementation_spec": p["implementation_spec"],
                        "training_overrides": p.get("training_overrides", {}), "diagnostics": p.get("diagnostics", []),
                        "kind": "initial"}
            else:
                last = route["history"][-1]["decision"].get("next_change") or {}
                task = {**last, "kind": "evolve"}
            name = f"{self.run.run_dir.name}-{route['id']}-{tag}"
            base_commit = git(self.kernel.base, "rev-parse", route["parent"])
            done = f"step2/{route['id']}/{tag}/implementation.json"   # written once the code of this iteration is committed
            saved = self.run.load(done) if self.run.has(done) else None
            workdir = self.kernel.candidates / name
            if saved and workdir.exists() and git(workdir, "rev-parse", "HEAD") == saved["commit"]:
                implementation, commit = saved["implementation"], saved["commit"]  # crashed while measuring: keep the code
                self.run.event("step2", f"{route['id']} 第 {k} 轮：沿用已提交的实现继续评测", route=route["id"], iteration=k,
                               status="measuring", commit=commit[:10])
            else:
                self.kernel.remove_candidate(name)  # an attempt interrupted mid-edit restarts from the parent version
                workdir = self.kernel.create_candidate(name, route["parent"])
                self.run.event("step2", f"{route['id']} 第 {k} 轮：开始实现", task=task.get("summary", "")[:200], parent=route["parent"],
                               route=route["id"], iteration=k, status="implementing")
                implementation = self._implement(route, workdir, task, tag)
                commit = self.kernel.commit(workdir, f"MARS {route['id']} {tag}: {task.get('summary', '')[:60]}")
                self.run.save(done, {"commit": commit, "implementation": implementation})
            evidence = self._measure(route, workdir, tag, task) if implementation["ok"] else {"stopped_at": "implementation"}
            record = {"iteration": k, "task": task, "branch": f"cand/{name}", "commit": commit,
                      "parent_branch": route["parent"], "base_commit": base_commit,
                      "diff_files": self.kernel.changed_files(workdir), "implementation": implementation, "evidence": evidence}
            record["decision"] = self._decide(route, record)
            self.run.save(record_path, record)
            self.run.remember({"title": f"{route['id']} {tag} {task.get('summary', '')[:80]}", "route": route["id"],
                               "iteration": k, "change": task.get("summary"), "stopped_at": evidence.get("stopped_at"),
                               "screen": {d: (x.get("worse_by") or {}).get(self.primary) for d, x in evidence.get("screen", {}).items()},
                               "verify": evidence.get("verify", {}).get("passed"),
                               "decision": record["decision"].get("verdict"),
                               "assessment": record["decision"].get("mechanism_assessment", "")[:400]})
        route["history"].append(record)
        route["iteration"] = k
        from mars import api  # the run's own copies of diff and coding summary (viewable without repository or raw logs)
        for snapshot in (api.iteration_diff, api.codingagent_summary):
            with suppress(Exception):
                snapshot(self.run.run_dir.name, route["id"], k, project_root=self.kernel.scope.root)
        self._update_route(route, record)
        self.run.event("step2", f"{route['id']} 第 {k} 轮完成：{route['status']}", route=route["id"], iteration=k,
                       status=route["status"], decision=record["decision"].get("verdict"), stopped_at=record["evidence"].get("stopped_at"),
                       screen={d: (x.get("worse_by") or {}).get(self.primary) for d, x in record["evidence"].get("screen", {}).items()},
                       verify=record["evidence"].get("verify", {}).get("passed"))
        return route

    def _decide(self, route: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
        notes_file, seen_file = (self.run.path(f"step2/{route['id']}/{n}") for n in ("notes.jsonl", "notes.seen"))  # api.add_route_note
        notes = [json.loads(line) for line in notes_file.read_text(encoding="utf-8").splitlines()] if notes_file.exists() else []
        seen = int(seen_file.read_text(encoding="utf-8")) if seen_file.exists() else 0
        cfg = self.kernel.cfg
        prompt = render("decide", notes=[n["text"] for n in notes[seen:]] or "（无）", proposal=route["proposal"], targets=self.targets,
                        constraints=self.run.contract.get("constraints", []), metrics=self.metrics,
                        diagnostics={name: spec.get("description", "") for name, spec in cfg["diagnostics"].items()} or "（项目未声明诊断）",
                        domain=cfg["prompts"].get("domain") or "（项目未提供领域说明）",
                        domain_decide=cfg["prompts"].get("decide") or "（项目未提供证据说明）",
                        history=self._history_brief(route), record={k: v for k, v in record.items() if k != "implementation"},
                        implementation=[{k: v for k, v in t.items() if k != "final_text"} for t in record["implementation"]["turns"]],
                        lessons=self.run.reference(),
                        kb=[{"id": c["id"], "title": c["title"], "text": c["text"][:600]} for c in
                            self.run.kb.search_many([route["proposal"]["mechanism_hypothesis"], "失败 原因 归因"], k=3, limit=6)],
                        remaining=int(self.budget["max_iterations_per_route"]) - record["iteration"])
        last_error, messages = "", [{"role": "user", "content": prompt}]
        for attempt in range(3):
            try:
                decision = self.run.llm.structured("strong", messages, Decision, tag="step2.decide").model_dump()
            except RuntimeError as error:
                last_error = str(error)
                time.sleep(60 * 2 ** attempt)
                continue
            # A route ends only after two consecutive refutations; the first one must still name a discriminating change.
            first_refutation = (decision.get("verdict") == "refuted" and not decision.get("next_change")
                                and route["refuted_streak"] == 0 and record["iteration"] < int(self.budget["max_iterations_per_route"]))
            if not first_refutation or attempt == 2:
                for answer, note in zip(decision.get("operator_notes") or [], notes[seen:]):  # replies follow note order
                    answer["note"] = note["text"]  # verbatim, so callers can match replies to notes
                seen_file.write_text(str(len(notes)), encoding="utf-8")  # answered in decision["operator_notes"]
                return decision
            correction = ("本路线首次判 refuted：规则是连续两次否定才终止，请保留 refuted 判断，并给出一个最能确认或推翻"
                          "核心机制的判别性改动（next_change），写清哪种实测结果会最终否定它。")
            messages = messages[:1] + [{"role": "assistant", "content": json.dumps(decision, ensure_ascii=False)},
                                       {"role": "user", "content": correction + "请重新给出完整决策。"}]
        self.run.event("step2", f"{route['id']} 第 {record['iteration']} 轮决策调用失败，路线保持活跃，下一步将重做决策",
                       error=last_error[:300])
        return {"verdict": "continue", "failed": True, "mechanism_assessment": f"决策调用失败：{last_error}"[:500],
                "evidence_for": [], "evidence_against": [], "alternative_explanations": [], "next_change": None,
                "rationale": "模型调用失败（暂时性故障，不作为科学结论）"}

    def _update_route(self, route: dict[str, Any], record: dict[str, Any]) -> None:
        evidence, decision = record["evidence"], record["decision"]
        if evidence.get("verify", {}).get("passed"):
            route["status"], route["success"] = "success", record
            return
        screen = evidence.get("screen", {})
        if screen and all("worse_by" in x for x in screen.values()):
            score = statistics.mean(x["worse_by"][self.primary] for x in screen.values())
            if route["best"] is None or score < route["best"]["score"]:
                route["best"] = {"iteration": record["iteration"], "branch": record["branch"], "score": round(score, 4)}
        if route["best"]:
            route["parent"] = route["best"]["branch"]
        elif record["implementation"]["ok"]:  # no screened winner yet: keep building on the latest working code
            route["parent"] = record["branch"]
        self._apply_decision(route, decision)

    def _apply_decision(self, route: dict[str, Any], decision: dict[str, Any]) -> None:
        """Route status from a verdict. A failed decision (transient model error) carries no verdict: it leaves the
        refutation streak untouched and is applied again, once, after it has been redone."""
        failed = decision.get("failed")
        if not failed:
            route["refuted_streak"] = route["refuted_streak"] + 1 if decision.get("verdict") == "refuted" else 0
        if not failed and (decision.get("verdict") == "stop" or route["refuted_streak"] >= 2):
            route["status"] = "refuted" if route["refuted_streak"] >= 2 else "stopped"
        elif route["iteration"] >= int(self.budget["max_iterations_per_route"]):
            route["status"] = "budget_exhausted"
        elif not failed and not decision.get("next_change"):
            route["status"] = "stopped"

    def round(self, routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Each route evolves independently until it ends (no lock-step waiting between routes).
        Saved per-iteration records act as checkpoints: a resumed round replays them instead of recomputing."""
        def run(route: dict[str, Any]) -> None:
            while route["status"] == "active":
                self.step(route)

        active = [r for r in routes if r["status"] == "active"]
        with ThreadPoolExecutor(max_workers=max(1, len(active))) as pool:
            list(pool.map(run, active))
        return routes
