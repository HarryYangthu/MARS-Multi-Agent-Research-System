"""Stable facade for the MARS application layer (Web, CLI wrappers, VSCode). Import only this module.

Everything here reads saved run artifacts or launches the CLI as a subprocess, so callers never depend on
internal modules. Artifact fields are append-only; breaking changes bump API_VERSION.
"""
from __future__ import annotations

import json
import re
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from mars.osutil import alive, lock, unlock
from mars.project import ExecutionContext, Scope, resolve, resolve_path

ROOT = Path(__file__).resolve().parents[1]
API_VERSION = 3  # 3: projects only; metrics, checks and gates come from the project declaration


def inspect_project(project_root: str | Path) -> dict[str, Any]:
    """Read-only inspection; never creates a checkout or starts a model/training process."""
    from mars.project import inspect
    return inspect(project_root)[0]


def _scope(project_root: str | Path | None) -> Scope:
    """The project folder; None means MARS_DEFAULT_PROJECT (there is no workspace outside projects)."""
    return Scope(Path(project_root) if project_root is not None else None)


def _run_path(run_id: str, project_root: str | Path | None = None) -> Path:
    return _scope(project_root).run(run_id)


def _route_path(run_id: str, route: str, project_root: str | Path | None) -> Path:
    if not re.fullmatch(r"[A-Z][A-Za-z0-9_-]*", route):
        raise ValueError("无效的路线 ID")
    path = _run_path(run_id, project_root) / "step2" / route
    if not path.resolve().is_relative_to(_run_path(run_id, project_root).resolve()):
        raise ValueError("路线目录越过运行目录")
    return path


def _artifact(run_id: str, name: str, project_root: str | Path | None) -> Path:
    run = _run_path(run_id, project_root)
    path = run / name
    if not path.resolve().is_relative_to(run.resolve()):
        raise ValueError("运行产物链接到运行目录以外")
    return path


def _saved_context(run_id: str, project_root: str | Path | None, validate: bool = False) -> ExecutionContext:
    scope = _scope(project_root)
    snapshot = scope.run(run_id) / "execution.json"
    if not snapshot.is_file():
        raise ValueError(f"项目运行 {run_id} 缺少执行快照 execution.json")
    return ExecutionContext.restore(scope, snapshot, validate=validate)


def _kernel(project_root: str | Path | None = None, environment: str | None = None, workdir: Path | None = None):
    from mars.workspace import Workspace
    scope = _scope(project_root)
    if workdir:
        if not workdir.is_relative_to(scope.workspace.resolve()):
            raise ValueError("评测目录不属于所选项目的工作区")
        snapshot = next((p / "binding.json" for p in [workdir, *workdir.parents] if p.is_relative_to(scope.workspace.resolve()) and (p / "binding.json").is_file()), None)
        if snapshot is None:
            raise ValueError("评测目录没有项目绑定快照，请先使用本项目的 baseline_dir 或 implement")
        context = ExecutionContext.restore(scope, snapshot, validate=True, environment=environment)
    else:
        context = resolve(scope.root, environment)
    return Workspace(context)


def _prepare_task(task_path: str | Path, run_id: str, project_root: str | Path | None, environment: str | None) -> Path:
    from mars.project import prepare_run
    scope = _scope(project_root)
    prepare_run(scope.run(run_id), resolve_path(task_path, scope.root), scope.root, environment)
    return scope.run(run_id) / "contract.yaml"


def _validate_approval(run_id: str, approve: list[str] | None, project_root: str | Path | None = None) -> None:
    if approve is None:
        return
    run = _run_path(run_id, project_root)
    rounds = sorted((p for p in (run / "step1").glob("r*") if p.name[1:].isdigit()), key=lambda p: int(p.name[1:]))
    latest = rounds[-1] if rounds else None
    if not latest or not (latest / "ranking.json").is_file() or (latest / "handoff.json").exists():
        raise ValueError("当前运行没有待确认轮次")
    allowed = {r["id"] for r in json.loads((latest / "ranking.json").read_text(encoding="utf-8"))}
    if not approve or len(set(approve)) != len(approve) or any(a not in allowed for a in approve):
        raise ValueError(f"只能批准当前轮次中的方案 ID：{sorted(allowed)}")


def _launcher() -> str:
    return str(ROOT / "bin/mars")


def start_run(task_path: str | Path, run_id: str = "", auto_approve: bool = False, environment: str | None = None,
              *, project_root: str | Path | None = None) -> str:
    """Start a research run (Step1 + Step2) in the background; returns its run id. Progress: events()/status()."""
    run_id = _new_run_id(run_id, Path(task_path).stem, project_root)
    task = _prepare_task(task_path, run_id, project_root, environment)
    _launch(run_id, ["run", str(task), "--run-id", run_id] + (["--auto-approve"] if auto_approve else []), environment, project_root, True)
    return run_id


def _launch(run_id: str, args: list[str], environment: str | None = None,
            project_root: str | Path | None = None, prepared: bool = False) -> None:
    """Detached CLI process for a run; Windows has no sh launcher, so the core venv's python runs mars.cli directly."""
    if environment not in (None, "local", "remote"):
        raise ValueError("执行环境必须是 local 或 remote")
    scope = _scope(project_root)
    scope.run(run_id)
    args = ["--project", str(scope.root)] + (["--environment", environment] if environment else []) + (["--prepared"] if prepared else []) + args
    runs = scope.runs
    runs.mkdir(parents=True, exist_ok=True)
    log = (runs / f"{run_id}.log").open("a", encoding="utf-8")
    if os.name == "nt":
        env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONUTF8": "1"}
        subprocess.Popen([str(ROOT / ".venv/Scripts/python.exe"), "-m", "mars.cli", *args], cwd=ROOT, env=env, stdout=log,
                         stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)
    else:
        subprocess.Popen([_launcher(), *args], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    log.close()


def _new_run_id(run_id: str, suffix: str, project_root: str | Path | None = None) -> str:
    run_id = run_id or time.strftime("%Y%m%d-%H%M%S") + "-" + suffix
    path = _run_path(run_id, project_root)
    if (path / "contract.yaml").exists() or path.exists():
        raise ValueError(f"run {run_id} already exists; use resume() or another run_id")
    return run_id


def _text_file(run_id: str, name: str, text_or_path: str | Path, project_root: str | Path | None = None) -> Path:
    """Stage the content next to the run log for the CLI to copy. Only a Path object is read as a file; a str is
    always the text itself, so user-typed text that happens to look like a path never reads a server file."""
    scope = _scope(project_root)
    text = (resolve_path(text_or_path, scope.root).read_text(encoding="utf-8")
            if isinstance(text_or_path, Path) else str(text_or_path))
    if not text.strip():
        raise ValueError(f"{name} is empty")
    staged = scope.run(run_id) / f"input.{name}.md"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_text(text, encoding="utf-8")
    return staged


def start_refine(task_path: str | Path, proposal: str | Path, run_id: str = "", auto_approve: bool = False,
                 environment: str | None = None, *, project_root: str | Path | None = None) -> str:
    """Refine scenario: structure the user's own design (str = the text, Path = a file to read), then evolve it in Step2 with a short
    budget (the task's max_iterations_per_route) without returning to Step1. Unless auto_approve, the run stops
    at awaiting_approval with the structured proposal (pending_approval, step1/r0/debate.json) for confirmation."""
    run_id = _new_run_id(run_id, Path(task_path).stem + "-refine", project_root)
    task = _prepare_task(task_path, run_id, project_root, environment)
    staged = _text_file(run_id, "proposal", proposal, project_root)
    _launch(run_id, ["refine", str(task), "--proposal", str(staged), "--run-id", run_id]
            + (["--auto-approve"] if auto_approve else []), environment, project_root, True)
    return run_id


def implement(task_path: str | Path, spec: str | Path, run_id: str = "", parent: str = "mars/base",
              environment: str | None = None, *, project_root: str | Path | None = None) -> str:
    """Simulation-verification scenario: implement a given design once in its own candidate branch (CodingAgent with
    checks and repair turns), commit it and run its measure checks. spec: str = the text, Path = a file to read. Runs in the background; poll
    implementation(run_id), then train/evaluate `candidate_dir` with evaluate()."""
    run_id = _new_run_id(run_id, "implement", project_root)
    task = _prepare_task(task_path, run_id, project_root, environment)
    staged = _text_file(run_id, "spec", spec, project_root)
    _launch(run_id, ["implement", str(task), "--spec", str(staged), "--run-id", run_id,
                     "--parent", parent], environment, project_root, True)
    return run_id


def implementation(run_id: str, *, project_root: str | Path | None = None) -> dict[str, Any] | None:
    """Result of implement(): ok, candidate_dir, branch, commit, changed_files, violations, checks, measure_checks, gates,
    turns, summary. None while it is still running (see status(run_id)["running"])."""
    path = _artifact(run_id, "implement/result.json", project_root)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def resume(run_id: str, approve: list[str] | None = None, *, project_root: str | Path | None = None) -> None:
    """Continue a run from its checkpoint; `approve` answers a pending human approval with proposal ids."""
    if not (_run_path(run_id, project_root) / "contract.yaml").exists():
        raise ValueError(f"run {run_id} not found")
    if _process(run_id, project_root)[0]:
        raise RuntimeError(f"run {run_id} is already running")
    _validate_approval(run_id, approve, project_root)
    _saved_context(run_id, project_root, validate=True)
    _launch(run_id, ["resume", run_id] + (["--approve", ",".join(approve)] if approve else []), project_root=project_root)


def list_runs(*, project_root: str | Path | None = None) -> list[dict[str, Any]]:
    runs = []
    root = _scope(project_root).runs
    for path in sorted(root.iterdir()) if root.exists() else []:
        if path.is_dir() and (path / "contract.yaml").exists():
            runs.append({"run_id": path.name, "title": _contract_title(path), "phase": status(path.name, project_root=project_root)["phase"]})
    return runs


def events(run_id: str, since: int = 0, *, project_root: str | Path | None = None) -> list[dict[str, Any]]:
    """Events with all fields; `since` is the number of events already consumed."""
    path = _artifact(run_id, "events.jsonl", project_root)
    if not path.exists():
        return []
    items = []
    for line in path.read_text(encoding="utf-8").splitlines()[since:]:
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:  # a line still being written; it is returned on the next call
            break
    return items


def status(run_id: str, *, project_root: str | Path | None = None) -> dict[str, Any]:
    """phase: starting / step1 / awaiting_approval / step2 / finished; `running` says whether a process is executing it."""
    run = _run_path(run_id, project_root)
    if not (run / "contract.yaml").is_file():
        raise ValueError(f"此项目中不存在运行 {run_id}")
    rounds = sorted((p for p in (run / "step1").glob("r*") if p.is_dir()), key=lambda p: int(p.name[1:]))
    latest = rounds[-1] if rounds else None
    running, pid = _process(run_id, project_root)
    if (run / "implement").exists():
        phase = "finished" if (run / "implement/result.json").exists() else "implementing"
    elif (run / "report.md").exists():
        phase = "finished"
    elif latest and (latest / "ranking.json").exists() and not (latest / "handoff.json").exists():
        phase = "step1" if running else "awaiting_approval"  # judging finished; the graph stops at the approval
    elif latest and (latest / "handoff.json").exists():
        phase = "step2"
    else:
        phase = "step1" if rounds else "starting"
    all_events = events(run_id, project_root=project_root)
    result: dict[str, Any] = {"api_version": API_VERSION, "run_id": run_id, "phase": phase, "running": running,
                              "pid": pid, "step1_round": latest.name if latest else None,
                              "routes": route_summaries(run_id, project_root=project_root), "last_events": all_events[-10:],
                              "health": _health(all_events)}
    if (run / "implement").exists():
        result["kind"], result["implementation"] = "implement", implementation(run_id, project_root=project_root)
    else:
        result["kind"] = "refine" if (run / "step1/r0/user_proposal.md").exists() else "rsi"
    if phase == "awaiting_approval" and latest:
        result["pending_approval"] = json.loads((latest / "ranking.json").read_text(encoding="utf-8"))
    result["project_root"] = str(_scope(project_root).root)
    snapshot = run / "execution.json"
    result["binding_id"] = json.loads(snapshot.read_text(encoding="utf-8"))["binding"]["id"] if snapshot.exists() else None
    return result


def stop(run_id: str, wait_seconds: float = 30, *, project_root: str | Path | None = None) -> bool:
    """Stop the process executing a run and everything it started (training, gateways, CodingAgent).
    The checkpoint is kept, so resume() continues from the last completed step. Returns False if nothing was running."""
    running, pid = _process(run_id, project_root)
    if not running or not pid:
        return False
    if os.name == "nt":  # no SIGSTOP/ps on Windows: taskkill ends the whole tree
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        deadline = time.time() + wait_seconds
        while time.time() < deadline and _process(run_id, project_root)[0]:
            time.sleep(0.5)
        return True
    os.kill(pid, signal.SIGSTOP)  # freeze it so no new children appear while the tree is collected
    tree = _descendants(pid)
    for child in reversed(tree):
        try:
            os.kill(child, signal.SIGTERM)
        except ProcessLookupError:
            pass
    os.kill(pid, signal.SIGTERM)
    os.kill(pid, signal.SIGCONT)
    deadline = time.time() + wait_seconds
    while time.time() < deadline and _process(run_id, project_root)[0]:
        time.sleep(0.5)
    for leftover in [pid] + tree:  # e.g. a gateway still draining connections after SIGTERM
        try:
            os.kill(leftover, signal.SIGKILL)
        except ProcessLookupError:
            pass
    return True


def stop_route(run_id: str, route_id: str, reason: str = "", *, project_root: str | Path | None = None) -> None:
    """Ask one Step2 route to end before its next iteration; the other routes continue. When every route has
    ended the run returns to Step1 with feedback as usual. A running process checks this between iterations."""
    route_dir = _route_path(run_id, route_id, project_root)
    if not route_dir.is_dir():
        raise ValueError(f"route {route_id} not found in run {run_id}")
    (route_dir / "STOP").write_text(reason or "operator request", encoding="utf-8")


def add_route_note(run_id: str, route: str, text: str, *, project_root: str | Path | None = None) -> None:
    """Leave a note for one Step2 route ("try X next", "stop doing Y"). Its next decision reads every unanswered note
    and records in decision["operator_notes"] whether each was adopted and why."""
    route_dir, stamp = _route_path(run_id, route, project_root), time.strftime("%Y-%m-%d %H:%M:%S")
    if not route_dir.is_dir() or not text.strip():
        raise ValueError(f"route {route} not found in run {run_id}, or the note is empty")
    for path, item in ((route_dir / "notes.jsonl", {"ts": stamp, "text": text.strip()}),
                       (_artifact(run_id, "events.jsonl", project_root), {"ts": stamp, "stage": "operator", "route": route,
                                                                         "message": f"{route} 收到留言：{text.strip()[:80]}"})):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")


def route_summaries(run_id: str, *, project_root: str | Path | None = None) -> list[dict[str, Any]]:
    summaries, run = [], _run_path(run_id, project_root)
    for route_dir in sorted((run / "step2").glob("[A-Z]*")):
        if not route_dir.is_dir():
            continue
        _route_path(run_id, route_dir.name, project_root)
        iterations = []
        for path in sorted(route_dir.glob("i*/record.json"), key=lambda p: int(p.parent.name[1:])):
            _artifact(run_id, str(path.relative_to(run)), project_root)
            record = json.loads(path.read_text(encoding="utf-8"))
            evidence = record.get("evidence", {})
            iterations.append({
                "iteration": record["iteration"], "change": record["task"].get("summary", ""),
                "branch": record.get("branch"), "stopped_at": evidence.get("stopped_at"),
                "check_gates": evidence.get("check_gates"), "prequick": evidence.get("prequick"),
                "screen": {d: {k: x.get(k) for k in ("metrics", "baseline_metrics", "worse_by", "primary", "margin", "passed", "ok", "error")}
                           for d, x in evidence.get("screen", {}).items()},
                "verify": evidence.get("verify"), "decision": record.get("decision", {}).get("verdict"),
                "commit": record.get("commit"), "candidate_dir": _candidate_dir(record.get("branch"), project_root, run_id),
                **_lineage(run_id, route_dir.name, record, project_root),
                "assessment": record.get("decision", {}).get("mechanism_assessment", "")})
        summaries.append({"route": route_dir.name, "iterations": iterations, "stop_requested": (route_dir / "STOP").exists()})
    return summaries


def evaluate(candidate_dir: str | Path, dataset: str, steps: int, seed: int = 2026, baseline: bool = False,
             overrides: dict[str, Any] | None = None, details: bool = False, environment: str | None = None,
             *, project_root: str | Path | None = None) -> dict[str, Any]:
    """Run the project's evaluation command on one committed checkout (a candidate, or with baseline=True the base
    repository); blocks until it ends. steps: budget passed to the command, 0 = full. overrides: editable_config_keys
    for this evaluation only (ValueError otherwise); results cached per (code, overrides, data, command, pack, target).
    details: also the project's measure-time checks for a candidate."""
    from mars.workspace import EvalJob
    workdir = resolve_path(candidate_dir, _scope(project_root).root)
    kernel = _kernel(project_root, environment, workdir)
    if baseline:
        kernel.ensure_base()
    result = kernel.evaluate(EvalJob(workdir, dataset, steps, seed, baseline=baseline, overrides=overrides))
    if details and result.get("ok") and not baseline:
        result["checks"] = kernel.checks(workdir, "measure")
    return result


def execution_environments(*, project_root: str | Path | None = None) -> dict[str, Any]:
    """Safe execution choices for CLI/app clients; contains no host addresses or credentials."""
    found = inspect_project(_scope(project_root).root)
    return {**found["execution_environments"], "project_root": found["project_root"], "binding_id": found["binding_id"]}


def baseline_dir(*, project_root: str | Path | None = None) -> str:
    """Checkout of the bound baseline repository, for evaluate(baseline=True)."""
    kernel = _kernel(project_root)
    kernel.ensure_base()
    return str(kernel.base)


def search_space(*, project_root: str | Path | None = None) -> dict[str, Any]:
    """Config keys a hyperparameter search may override (editable_config_keys) with the ranges the project declares
    (search_space: {key: {type: float|int|categorical, low, high, log, choices}})."""
    kernel = _kernel(project_root)
    declared = kernel.cfg["search_space"]
    keys = [{"key": key, **(declared.get(key) or {}), "declared": key in declared} for key in kernel.cfg["editable_config_keys"]]
    return {"datasets": list(kernel.cfg["datasets"]), "keys": keys, "metrics": kernel.metrics,
            "project_root": str(kernel.scope.root), "binding_id": kernel.context.binding["id"]}


def steps_per_epoch(candidate_dir: str | Path | None = None, overrides: dict[str, Any] | None = None,
                    *, project_root: str | Path | None = None) -> dict[str, Any]:
    """Budget units per epoch for each dataset, when the project declares a diagnostic named steps_per_epoch (its JSON
    carries {"datasets": {name: steps}}); overrides are passed as a JSON file like evaluate()."""
    import tempfile
    workdir = resolve_path(candidate_dir, _scope(project_root).root) if candidate_dir else None
    kernel = _kernel(project_root, workdir=workdir)
    kernel.ensure_base()
    if "steps_per_epoch" not in kernel.cfg["diagnostics"]:
        raise ValueError("项目没有声明 diagnostics.steps_per_epoch")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump(kernel.validate_overrides(overrides), handle)
    try:
        result = kernel.command("diagnostics", "steps_per_epoch", workdir or kernel.base, overrides=handle.name,
                                baseline=int(candidate_dir is None))
    finally:
        os.unlink(handle.name)
    if not result.get("ok", True) or "datasets" not in result:
        raise RuntimeError(f"无法计算每 epoch 步数：{result.get('error')}")
    return result["datasets"]


def iteration_diff(run_id: str, route: str, iteration: int, max_patch_bytes: int = 200_000,
                   *, project_root: str | Path | None = None) -> dict[str, Any]:
    """Code change of one Step2 iteration against the version it was built from, limited to the candidate's
    editable files (infrastructure synced from the baseline is not part of the change). Read from the run's own
    copy i<k>/diff.json when present, so copied runs/ work without the candidate repository."""
    cache = _route_path(run_id, route, project_root) / f"i{iteration}" / "diff.json"
    if cache.is_file() and max_patch_bytes == 200_000:
        return json.loads(cache.read_text(encoding="utf-8"))
    record = _record(run_id, route, iteration, project_root)
    lineage = _lineage(run_id, route, record, project_root)
    base, commit = lineage["base_commit"], record.get("commit")
    repo = _repo(project_root, run_id)
    if not (repo / ".git").exists():
        raise FileNotFoundError(f"候选仓库 {repo} 不存在（新安装或从别处拷来的运行记录），无法给出代码差异")
    for name, sha in (("本轮提交", commit), ("父版本提交", base)):
        if not sha or subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=repo, capture_output=True).returncode:
            raise FileNotFoundError(
                f"{run_id} {route} i{iteration} 的{name} {(sha or '未知')[:10]} 不在候选仓库中：该轮编码 Agent 改写了工作树的 "
                ".git 指针，提交落进了它自建的私有仓库（内核已于 2026-10-25 加上防护），无法给出差异")
    specs = ["--", *(f":(glob){p}" for p in _editable_files(project_root, run_id))]
    files = []
    for line in _git(repo, "diff", "--name-status", "--no-renames", base, commit, *specs).splitlines():
        code, path = line.split("\t", 1)
        patch = _git(repo, "diff", base, commit, "--", path)
        files.append({"path": path, "status": {"A": "added", "D": "deleted"}.get(code[0], "modified"),
                      "patch": patch[:max_patch_bytes], "truncated": len(patch) > max_patch_bytes})
    result = {"base_commit": base, "commit": commit, **{k: lineage[k] for k in ("parent_iteration", "parent_branch")}, "files": files}
    if max_patch_bytes == 200_000:
        cache.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def active_jobs(run_id: str | None = None, remote_progress: bool = False,
                *, project_root: str | Path | None = None) -> list[dict[str, Any]]:
    """Trainings and coding sessions running right now (registered by processes started after 2026-10-25).
    kind train|codingagent; route "baseline" for baseline evaluations; target "local-cpu" or "<server>/<device>".
    progress: {step, total, epoch} from {output}/progress.json if the evaluation writes one — read locally for CPU runs;
    for remote runs only with remote_progress=True (one ssh round trip, about a second)."""
    from mars.workspace import Workspace
    scope = _scope(project_root)
    jobs_dir = scope.workspace / "jobs"
    jobs = []
    for path in sorted(jobs_dir.glob("*.json")) if jobs_dir.exists() else []:
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):  # finished between listing and reading
            continue
        if not alive(job.get("pid", -1)):  # its process is gone: the registration is stale
            path.unlink(missing_ok=True)
            continue
        if run_id and job.get("run_id") != run_id:
            continue
        progress = None
        if job["kind"] in ("evaluate", "train"):
            raw = None
            local = Path(job["output"]) / "progress.json"
            if local.exists():
                raw = local.read_text(encoding="utf-8")
            elif remote_progress and job.get("remote_run"):
                binding_file = Path(job.get("binding_file", ""))
                if not binding_file.resolve().is_relative_to(scope.workspace.resolve()):
                    raise ValueError("活动任务的绑定不属于当前项目")
                kernel = Workspace(ExecutionContext.restore(scope, binding_file, validate=True, environment="remote"))
                done = subprocess.run([*kernel._ssh(kernel.cfg["remote"]), "cat", f"{job['remote_run']}/progress.json"],
                                      capture_output=True, text=True, encoding="utf-8", timeout=30)
                raw = done.stdout if done.returncode == 0 else None
            if raw:
                data = json.loads(raw)
                progress = {"step": data.get("step"), "epoch": data.get("epoch"),
                            "total": data.get("total") or job.get("steps") or None}
        jobs.append({k: job.get(k) for k in ("kind", "run_id", "route", "iteration", "stage", "dataset", "seed",
                                              "baseline", "started", "target")} | {"progress": progress})
        jobs[-1]["project_root"] = str(scope.root)
    return jobs


def codingagent_summary(run_id: str, route: str, iteration: int, *, project_root: str | Path | None = None) -> dict[str, Any]:
    """What the coding agent did in one Step2 iteration: one entry per turn (the first implementation turn, then
    repair turns after failed checks) plus session-wide tool usage parsed from its log; cached as
    i<k>/codingagent_summary.json, which is read first (the raw log is not needed to view a copied run)."""
    from mars.codingagent import summarize_log
    cache = _route_path(run_id, route, project_root) / f"i{iteration}" / "codingagent_summary.json"
    if cache.is_file():
        return json.loads(cache.read_text(encoding="utf-8"))
    record = _record(run_id, route, iteration, project_root)
    turns = []
    for t in record.get("implementation", {}).get("turns", []):
        smoke_ok = (t.get("smoke") or {}).get("ok")
        outcome = ("error" if "error" in t else "check_failed" if t.get("violations") else
                   "smoke_failed" if smoke_ok is False or t.get("dead_gradients") else "ok")
        turns.append({"turn": t.get("turn"), "status": t.get("status"), "seconds": t.get("seconds"), "outcome": outcome,
                      "summary": t.get("final_text") or t.get("error", ""), "violations": t.get("violations") or [],
                      "reverted": t.get("reverted") or [], "denials": t.get("denials") or []})
    log = _route_path(run_id, route, project_root) / f"i{iteration}" / "codingagent" / "codingagent.jsonl"
    session = summarize_log(log) if log.exists() else {}
    workdir = _candidate_dir(record.get("branch"), project_root, run_id)
    if workdir and session.get("files_touched"):
        session["files_touched"] = [f[len(workdir) + 1:] if f.startswith(workdir + "/") else f for f in session["files_touched"]]
    result = {"turns": turns, **session}
    if log.exists():
        cache.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def _record(run_id: str, route: str, iteration: int, project_root: str | Path | None = None) -> dict[str, Any]:
    if type(iteration) is not int or iteration < 1:
        raise ValueError("iteration 必须是正整数")
    _route_path(run_id, route, project_root)
    path = _artifact(run_id, f"step2/{route}/i{iteration}/record.json", project_root)
    if not path.exists():
        raise ValueError(f"no record for {run_id} {route} i{iteration}")
    return json.loads(path.read_text(encoding="utf-8"))


def _snapshot(project_root: str | Path | None, run_id: str | None) -> dict[str, Any] | None:
    """The run's recorded binding, read without validation (views of finished or older runs need no live binding)."""
    path = _scope(project_root).run(run_id) / "execution.json" if run_id else None
    return json.loads(path.read_text(encoding="utf-8")) if path and path.is_file() else None


def _workspace(project_root: str | Path | None, run_id: str | None) -> Path:
    """Where a run's candidates live. A run copied into a project without its execution snapshot has none here:
    views fall back to the run's own cached copies."""
    scope, snapshot = _scope(project_root), _snapshot(project_root, run_id)
    return scope.path(f"workspace/bindings/{snapshot['binding']['id']}") if snapshot else scope.workspace / "none"


def _repo(project_root: str | Path | None = None, run_id: str | None = None) -> Path:
    return _workspace(project_root, run_id) / "base"


def _editable_files(project_root: str | Path | None = None, run_id: str | None = None) -> list[str]:
    snapshot = _snapshot(project_root, run_id)
    return snapshot["binding"]["config"]["editable_files"] if snapshot else []


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", check=True).stdout


def _lineage(run_id: str, route: str, record: dict[str, Any], project_root: str | Path | None = None) -> dict[str, Any]:
    """Which iteration this one was built from: recorded since 2026-10-25; older records use the parent in the
    "开始实现" event and git."""
    parent = record.get("parent_branch")
    if not parent:
        marker = f"{route} 第 {record['iteration']} 轮：开始实现"
        parent = next((e.get("parent") for e in reversed(events(run_id, project_root=project_root)) if e.get("message") == marker), None)
    match = re.search(rf"-{re.escape(route)}-i(\d+)$", parent or "")
    parent_iteration = int(match.group(1)) if match else 0
    base = record.get("base_commit")
    if not base and match:
        try:
            base = _record(run_id, route, parent_iteration, project_root)["commit"]
        except ValueError:
            base = None
    if not base and record.get("commit"):
        try:
            base = _git(_repo(project_root, run_id), "merge-base", record["commit"], "mars/base").strip()
        except (subprocess.CalledProcessError, OSError):  # no candidate repository here (fresh install, copied runs/)
            base = None
    return {"parent_iteration": parent_iteration, "parent_branch": parent, "base_commit": base}


def _candidate_dir(branch: str | None, project_root: str | Path | None = None, run_id: str | None = None) -> str | None:
    if not branch or not branch.startswith("cand/"):
        return None
    root = _workspace(project_root, run_id) / "candidates"
    path = root / branch[len("cand/"):]
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("候选分支路径越过项目工作区")
    return str(path) if path.exists() else None


def _health(items: list[dict[str, Any]]) -> dict[str, Any]:
    """For alerts: time since the last event and the most recent failure (model API, remote training, crash)."""
    last = items[-1]["ts"] if items else None
    idle = None
    try:
        idle = round(time.time() - time.mktime(time.strptime(last, "%Y-%m-%d %H:%M:%S"))) if last else None
    except ValueError:
        pass
    failure = next((e for e in reversed(items) if "error" in e or re.search(r"(调用|执行|实现|训练)失败|失败：", e.get("message", ""))), None)
    if failure and "余额" in json.dumps(failure, ensure_ascii=False):
        kind = "model_quota"
    elif failure and "模型调用失败" in json.dumps(failure, ensure_ascii=False):
        kind = "model_api"
    else:
        kind = "other" if failure else None
    return {"last_event_ts": last, "seconds_since_last_event": idle,
            "last_failure": {**failure, "kind": kind} if failure else None}


def _process(run_id: str, project_root: str | Path | None = None) -> tuple[bool, int | None]:
    """(running, pid): the CLI holds an exclusive flock on runs/<id>/run.lock for as long as it executes the run."""
    path = _run_path(run_id, project_root) / "run.lock"
    if not path.exists():
        return False, None
    with path.open(encoding="utf-8") as handle:
        text = handle.read().strip()
        pid = int(text) if text.isdigit() else None
        try:
            lock(handle, shared=True)
        except BlockingIOError:
            return True, pid
        unlock(handle)
        return False, None


def _descendants(pid: int) -> list[int]:
    """All descendants of pid, parents before children."""
    table = subprocess.run(["ps", "-ax", "-o", "pid=,ppid="], capture_output=True, text=True, encoding="utf-8").stdout.split()
    children: dict[int, list[int]] = {}
    for child, parent in zip(map(int, table[::2]), map(int, table[1::2])):
        children.setdefault(parent, []).append(child)
    order, queue = [], list(children.get(pid, []))
    while queue:
        current = queue.pop(0)
        order.append(current)
        queue += children.get(current, [])
    return order


def _contract_title(run: Path) -> str:
    import yaml
    return str((yaml.safe_load((run / "contract.yaml").read_text(encoding="utf-8")) or {}).get("title", ""))
