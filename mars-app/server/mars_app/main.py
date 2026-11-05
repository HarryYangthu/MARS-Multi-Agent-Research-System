"""mars-app HTTP API: run dashboard data, live event stream, human approval, run control — all via mars.api."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Literal

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from mars_app import artifacts, core, evolution, export, gpu, hpo, models_config, verify, projects, workspace_routes, library
from mars_app.launcher import Launcher
from mars_app.settings import APP_ROOT, Settings

settings = Settings.from_env()
launcher = Launcher(settings)
app = FastAPI(title="MARS App", version="0.3.0")
project_store = projects.ProjectStore(settings.data_dir, settings.core_root)


LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
DEV_PORTS = {"5180"}  # the Vite dev server proxies /api here


def same_origin(origin: str | None, host: str | None) -> bool:
    """A state change may come from this app's own page (same host:port, or the local Vite dev server) or from a
    non-browser client (no Origin). Any other web page open on this machine is refused: the app listens on
    127.0.0.1 without auth, so without this a form on some site could stop or resume runs."""
    if not origin:
        return True
    if origin == "null":
        return False
    authority = origin.split("://", 1)[-1].rstrip("/")
    if authority == host:
        return True
    name, _, port = authority.rpartition(":")
    return name in LOCAL_HOSTS and port in DEV_PORTS


def local_host(host: str | None) -> bool:
    """Requests must address this machine by a loopback name. A DNS-rebinding page reaches 127.0.0.1 under its own
    domain name, with matching Origin and Host, so only the Host name tells it apart. MARS_APP_ALLOWED_HOSTS adds
    names for a future LAN deployment (which needs authentication first)."""
    if not host:
        return False
    name = host.rsplit(":", 1)[0] if not host.endswith("]") else host
    extra = {h.strip() for h in os.environ.get("MARS_APP_ALLOWED_HOSTS", "").split(",") if h.strip()}
    return name in LOCAL_HOSTS or name in extra


@app.middleware("http")
async def reject_cross_origin_writes(request: Request, call_next):
    if not local_host(request.headers.get("host")):
        return JSONResponse({"detail": "只接受通过 127.0.0.1 或 localhost 访问"}, status_code=403)
    if request.method not in ("GET", "HEAD", "OPTIONS") and not same_origin(request.headers.get("origin"), request.headers.get("host")):
        return JSONResponse({"detail": "拒绝来自其他网页的修改请求"}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def project_scope(request: Request, call_next):
    pid = request.headers.get("x-mars-project") or request.query_params.get("project") or "current"
    try:
        project = project_store.get(pid)
    except (OSError, ValueError, yaml.YAMLError) as error:
        return JSONResponse({"detail": str(error)}, status_code=400)
    token = projects.selected.set(project)
    try:
        if project.get("demo") and request.method not in ("GET", "HEAD") and request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "演示项目只读：可以查看结果，不能启动或修改。"}, status_code=403)
        if not project["connected"] and request.url.path.startswith(("/api/runs", "/api/refine", "/api/hpo", "/api/verify")) \
                and (request.method not in ("GET", "HEAD") or request.url.path.startswith(("/api/hpo", "/api/verify"))):
            return JSONResponse({"detail": "此项目尚未连接可执行的内核工作区，可以保存设置和浏览产物；当前内核尚未提供独立项目运行接口。"}, status_code=409)
        return await call_next(request)
    finally:
        projects.selected.reset(token)


@app.exception_handler(ValueError)
async def invalid_input(_request: Request, error: ValueError):
    return JSONResponse({"detail": str(error)}, status_code=400)


@app.exception_handler(OSError)
async def file_error(_request: Request, error: OSError):
    return JSONResponse({"detail": f"无法访问文件或文件夹：{error}"}, status_code=400)


def _connected() -> bool:
    project = projects.selected.get()
    return project is None or bool(project["connected"])


def _runs_root() -> Path:
    project = projects.selected.get()
    return Path(project["runs_dir"]) if project else settings.runs_dir


def _tasks_dir() -> Path:
    """Contracts the app writes: the project's tasks/ folder, or the app's own data/tasks for the default workspace."""
    root = _project_root()
    return Path(root) / "tasks" if root else launcher.tasks_dir


def _events(run_id: str, since: int = 0) -> list[dict[str, Any]]:
    return core.events(_api(), run_id, since) if _connected() else projects.saved_events(_runs_root() / run_id, since)


def _project_root() -> str | None:
    """The selected project's folder, or None for the core's own default workspace."""
    project = projects.selected.get()
    return None if project is None or project["builtin"] else project["path"]


def _api_for(project_root: str | None) -> core.Scoped:
    """mars.api scoped to a project; background jobs keep the project they were started in."""
    try:
        module = core.load(settings.core_root)
    except core.CoreUnavailable as error:
        raise HTTPException(503, str(error)) from error
    if project_root is not None and not core.supports_projects(module):
        raise HTTPException(409, "当前内核不支持在独立项目中执行，只能浏览已保存的产物。")
    return core.Scoped(module, project_root)


def _api() -> core.Scoped:
    if not _connected():
        raise HTTPException(409, "此项目尚未连接可执行的内核工作区，当前只能浏览已保存的产物。")
    return _api_for(_project_root())


def _core_supports_projects() -> bool:
    try:
        return core.supports_projects(core.load(settings.core_root))
    except (core.CoreUnavailable, ImportError):
        return False


project_store.supports_projects = _core_supports_projects
if os.environ.get("MARS_DEMO_PROJECT"):  # the installer's read-only demo project (real results, no repo or data needed)
    project_store.register(os.environ["MARS_DEMO_PROJECT"])
if os.environ.get("MARS_DEFAULT_PROJECT"):
    project_store.register(os.environ["MARS_DEFAULT_PROJECT"])

# Searches and simulation checks live with their project: <project>/hpo/<id>, <project>/verify/<id>;
# the core's default workspace keeps them in the app's data folder.
_managers: dict[tuple[str, str], Any] = {}


def _manager(kind: str) -> Any:
    root = _project_root()
    key = (kind, root or "")
    if key not in _managers:
        base = Path(root) if root else settings.data_dir
        _managers[key] = (hpo.HpoManager(base / "hpo", _api_for) if kind == "hpo"
                          else verify.VerifyManager(base / "verify", Path(root) / "runs" if root else settings.runs_dir, _api_for))
    return _managers[key]


def _candidates(api: Any) -> list[dict[str, Any]]:
    """Every Step2 iteration that still has a checkout (newest runs first), from api.route_summaries."""
    found = []
    runs = _runs_root()
    if not runs.exists():
        return found
    project = _project_root()
    if project and (Path(project) / ".mars/candidate-imports.json").is_file():
        # The installer materializes exported Git commits through the public project API; it never trains.
        with project_store.lock:
            result = subprocess.run([sys.executable, str(settings.deploy_root / "install/prepare_project.py"), project],
                                    capture_output=True, text=True, timeout=180)
        if result.returncode:
            raise ValueError("历史候选准备失败：" + (result.stderr or result.stdout)[-1200:])
    for path in sorted(runs.iterdir(), reverse=True):
        if not artifacts.is_run_dir(path):
            continue
        try:
            summaries = api.route_summaries(path.name)
        except (OSError, ValueError, KeyError):
            continue
        for route in summaries:
            for it in route.get("iterations", []):
                # only candidates the coding agent produced (an iteration that stopped at implementation has no model)
                if it.get("candidate_dir") and it.get("stopped_at") != "implementation":
                    found.append({"run_id": path.name, "route": route.get("route"), "iteration": it.get("iteration"),
                                  "dir": it["candidate_dir"], "branch": it.get("branch"), "change": it.get("change", ""),
                                  "stopped_at": it.get("stopped_at"),
                                  "screen": {d: (x or {}).get("worse_by") for d, x in (it.get("screen") or {}).items()}})
    return found


def _run_dir(run_id: str) -> Path:
    root = _runs_root()
    path = root / run_id
    if not artifacts.RUN_ID.match(run_id) or not path.resolve().is_relative_to(root.resolve()) or not artifacts.is_run_dir(path):
        raise HTTPException(404, f"运行 {run_id} 不存在")
    return path


def _status(api: Any, run_id: str) -> dict[str, Any]:
    """api.status(); a record caught mid-write reads as invalid JSON, which the next poll sees whole."""
    try:
        if not _connected():
            return projects.saved_status(_runs_root() / run_id)
        return dict(api.status(run_id))
    except (OSError, ValueError, KeyError) as error:
        return {"phase": f"unknown: {type(error).__name__}", "running": False, "pid": None}


class StartRequest(BaseModel):
    run_id: str
    contract_yaml: str
    human_approval: bool = True


class ApprovalRequest(BaseModel):
    approve: list[str]


@app.get("/api/health")
def health() -> dict[str, Any]:
    try:
        api = core.load(settings.core_root)
        return {"core_root": str(settings.core_root), "core_found": True, "api_version": api.API_VERSION, "error": None}
    except (core.CoreUnavailable, ImportError) as error:
        return {"core_root": str(settings.core_root), "core_found": False, "api_version": None, "error": str(error)}


@app.get("/api/runs")
def list_runs() -> list[dict[str, Any]]:
    api = _api() if _connected() else None
    root = _runs_root()
    if not root.exists():
        return []
    runs = [artifacts.summary(path, _status(api, path.name), _events(path.name))
            for path in root.iterdir() if artifacts.is_run_dir(path) and path.resolve().is_relative_to(root.resolve())]
    return sorted(runs, key=lambda r: (r["last_event"] or {}).get("ts") or "", reverse=True)


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str) -> dict[str, Any]:
    run_dir, api = _run_dir(run_id), _api() if _connected() else None
    status = _status(api, run_id)
    found = artifacts.detail(run_dir, status, _events(run_id))
    project = projects.selected.get()
    reports = Path(project["reports_dir"]) if project else settings.data_dir / "exports"
    found["followups"] = artifacts.followups(reports, run_id)
    try:
        if api:
            # status already returns the public route summaries, including lineage. Avoid doing every Git
            # lookup twice on each workbench refresh; retain the explicit API fallback for older cores.
            summaries = status.get("routes")
            artifacts.add_lineage(found["routes"], summaries if isinstance(summaries, list) else api.route_summaries(run_id))
    except (OSError, ValueError, KeyError):
        pass  # a record caught mid-write; the next reload has it
    return found


@app.get("/api/runs/{run_id}/events")
def run_events(run_id: str, after: int = 0) -> list[dict[str, Any]]:
    _run_dir(run_id)
    return _events(run_id, max(0, after))


@app.get("/api/runs/{run_id}/stream")
async def run_stream(run_id: str, request: Request) -> StreamingResponse:
    """Server-sent events: one message per new event (id = line index), so clients resume with Last-Event-ID."""
    _run_dir(run_id)
    header = request.headers.get("last-event-id", "")
    start = int(header) + 1 if header.isdigit() else int(request.query_params.get("after", "0") or 0)

    async def generate() -> AsyncIterator[str]:
        cursor = start
        while not await request.is_disconnected():
            for event in _events(run_id, cursor):
                cursor = event["index"] + 1
                yield f"id: {event['index']}\ndata: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
            yield ": keep-alive\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/api/runs/{run_id}/routes/{route_id}/iterations/{iteration}")
def iteration_record(run_id: str, route_id: str, iteration: int) -> dict[str, Any]:
    found = artifacts.record(_run_dir(run_id), route_id, iteration)
    if found is None:
        raise HTTPException(404, f"{route_id} 第 {iteration} 轮尚无记录")
    return found


def _core_call(call: Callable[[], Any], missing: str) -> Any:
    """A read-only mars.api call whose None / FileNotFoundError means "not produced yet"."""
    try:
        found = call()
    except FileNotFoundError as error:
        # the core says why (e.g. the iteration's commit is not in the candidate repository)
        raise HTTPException(404, f"{missing}：{error}" if str(error) else missing) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except (subprocess.CalledProcessError, TypeError, KeyError, OSError) as error:
        # e.g. a recorded commit no longer in the candidate repository: say so instead of a bare 500
        detail = (getattr(error, "stderr", "") or str(error)).strip().splitlines()
        raise HTTPException(502, f"内核读取失败（{type(error).__name__}）：{detail[-1] if detail else ''}"[:300]) from error
    if found is None:
        raise HTTPException(404, missing)
    return found


class NoteRequest(BaseModel):
    text: str


@app.get("/api/runs/{run_id}/routes/{route_id}/notes")
def notes_list(run_id: str, route_id: str) -> list[dict[str, Any]]:
    return artifacts.route_notes(_run_dir(run_id), route_id)


@app.post("/api/runs/{run_id}/routes/{route_id}/notes")
def notes_add(run_id: str, route_id: str, body: NoteRequest) -> list[dict[str, Any]]:
    """A note for one route's next decision (api.add_route_note); the decision says whether it was adopted."""
    run_dir, api = _run_dir(run_id), _api()
    if not hasattr(api, "add_route_note"):
        raise HTTPException(501, "当前内核不支持路线留言，请升级内核")
    if not body.text.strip() or len(body.text) > 2000:
        raise HTTPException(400, "留言不能为空，且不超过 2000 字")
    _control(lambda: api.add_route_note(run_id, route_id, body.text.strip()) or {})
    return artifacts.route_notes(run_dir, route_id)


@app.get("/api/runs/{run_id}/evolution")
def run_evolution(run_id: str, route: str | None = None, iteration: int | None = None) -> dict[str, Any]:
    """Animated evolution story: the Step1 round behind a route, then the route's rounds and researcher interventions."""
    return evolution.build_evolution(_api(), _run_dir(run_id), run_id, route=route, iteration=iteration, project_root=_project_root())


@app.get("/api/runs/{run_id}/step1/{round_name}/story")
def run_step1_story(run_id: str, round_name: str) -> dict[str, Any] | None:
    """One Step1 round as the animated research story (search, screening, cards, critiques, scores)."""
    if not (round_name[:1] == "r" and round_name[1:].isdigit()):
        raise HTTPException(400, "轮次格式应为 r0、r1…")
    return evolution.step1_story(_run_dir(run_id), round_name)


@app.get("/api/runs/{run_id}/routes/{route_id}/iterations/{iteration}/diff")
def iteration_diff(run_id: str, route_id: str, iteration: int) -> dict[str, Any]:
    """api.iteration_diff: the candidate's changes against the iteration it was built on (patches over 200 KB truncated)."""
    _run_dir(run_id)
    api = _api()
    return _core_call(lambda: api.iteration_diff(run_id, route_id, iteration), f"{route_id} 第 {iteration} 轮代码不可用")


@app.get("/api/runs/{run_id}/routes/{route_id}/iterations/{iteration}/coding")
def iteration_coding(run_id: str, route_id: str, iteration: int) -> dict[str, Any]:
    """api.codingagent_summary: the coding agent's repair turns and tool/permission counts, parsed by the core."""
    _run_dir(run_id)
    api = _api()
    return _core_call(lambda: api.codingagent_summary(run_id, route_id, iteration), f"{route_id} 第 {iteration} 轮没有编码记录")


@app.get("/api/runs/{run_id}/plots/{route_id}/{name}")
def run_plot(run_id: str, route_id: str, name: str, iteration: int | None = None) -> FileResponse:
    """Spectra the core keeps in runs/ (route "baseline" with name baseline_<stage>_<dataset> for the reference)."""
    path = artifacts.plot_file(_run_dir(run_id), route_id, iteration, name)
    if path is None:
        raise HTTPException(404, "图像不存在")
    return FileResponse(path)


class EpochRequest(BaseModel):
    candidate_dir: str | None = None
    overrides: dict[str, Any] | None = None


_epoch_cache: dict[str, Any] = {}


@app.post("/api/steps-per-epoch")
def steps_per_epoch(body: EpochRequest) -> dict[str, Any]:
    """Optimizer steps per epoch for each bound dataset (api.steps_per_epoch builds the model and reads the data,
    ~15 s), cached per project, candidate and overrides. steps = epochs x steps_per_epoch; steps 0 = all epochs."""
    api = _api()
    if not hasattr(api, "steps_per_epoch"):
        raise HTTPException(501, "当前内核不提供每 epoch 步数，请升级内核")
    key = json.dumps([_project_root(), body.candidate_dir, body.overrides or {}], sort_keys=True, ensure_ascii=False)
    if key not in _epoch_cache:
        _epoch_cache[key] = _core_call(lambda: api.steps_per_epoch(body.candidate_dir, body.overrides or None), "无法计算每 epoch 步数")
    return _epoch_cache[key]


@app.get("/api/jobs")
def jobs(remote_progress: bool = False) -> dict[str, Any]:
    """What is computing now: the core's registered jobs (training / coding) plus this app's own searches and
    verifications. remote_progress reads remote training progress over ssh (~1 s), so callers ask for it on demand."""
    if not _connected():
        return {"core": [], "app": [], "core_error": None, "max_parallel": gpu.MAX_PARALLEL, **library.compute_summary(_runs_root())}
    api = _api()
    try:
        core_jobs = list(api.active_jobs(remote_progress=remote_progress))
    except (OSError, ValueError, KeyError) as error:
        core_jobs, core_error = [], str(error)
    else:
        core_error = None
    app_jobs = [{"kind": "hpo", "id": s["id"], "title": s["spec"].get("title", ""), "state": s["state"],
                 "counts": s["counts"]} for s in _manager('hpo').list() if s["state"] in ("running", "stopping")]
    app_jobs += [{"kind": "verify", "id": v["id"], "title": v["title"], "state": v["stage"]}
                 for v in _manager('verify').list() if v["running"]]
    return {"core": core_jobs, "app": app_jobs, "core_error": core_error, "max_parallel": gpu.MAX_PARALLEL,
            **library.compute_summary(_runs_root())}


@app.get("/api/execution-environments")
def execution_environments() -> dict[str, Any]:
    api = _api()
    if not hasattr(api, "execution_environments"):
        raise HTTPException(503, "当前内核尚未提供执行环境选择接口，请更新 mars-core。")
    return api.execution_environments()


@app.get("/api/runs/{run_id}/report", response_class=PlainTextResponse)
def report(run_id: str) -> str:
    path = _run_dir(run_id) / "report.md"
    if not path.exists():
        raise HTTPException(404, "报告尚未生成（运行结束时由内核写出）")
    return path.read_text(encoding="utf-8")


@app.get("/api/runs/{run_id}/log", response_class=PlainTextResponse)
def run_log(run_id: str) -> str:
    _run_dir(run_id)
    path = _runs_root() / f"{run_id}.log"
    return path.read_bytes()[-20000:].decode(errors="replace") if path.is_file() else ""


@app.get("/api/runs/{run_id}/export/{fmt}")
def export_run(run_id: str, fmt: str) -> FileResponse:
    """Word full report / PowerPoint summary / Excel per-iteration data, built from the run's artifacts."""
    run_dir = _run_dir(run_id)
    try:
        project = projects.selected.get()
        working = project is not None and not project.get("demo")
        out = Path(project["reports_dir"]) if working else settings.data_dir / "exports"
        owner = Path(project["path"]) if working and not project.get("builtin") else settings.data_dir
        if not out.resolve().is_relative_to(owner.resolve()):
            raise ValueError("报告目录链接到项目以外")
        path = export.export(fmt, run_dir, _api(), out)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return FileResponse(path, media_type=export.MEDIA_TYPES[fmt], filename=path.name)


@app.get("/api/plot")
def plot(path: str) -> FileResponse:
    """Evaluation images referenced by records and evaluations: .png files under the selected project folder
    (relative paths resolve there, e.g. a copied project's verify/<id>/plots/)."""
    root = Path(_project_root()).resolve() if _project_root() else None
    target = (root / path if root and not Path(path).is_absolute() else Path(path)).resolve()
    allowed = [root] if root else []
    if not any(base in target.parents for base in allowed) or target.suffix != ".png" or not target.is_file():
        raise HTTPException(404, "图像不存在")
    return FileResponse(target)


# ----- human approval (Step1 → Step2) -----
def _pending(run_id: str) -> dict[str, Any] | None:
    """The approval the run is parked at, per api.status() (phase awaiting_approval + pending_approval)."""
    run_dir = _run_dir(run_id)
    status = _status(_api() if _connected() else None, run_id)
    if status.get("phase") != "awaiting_approval" or not status.get("step1_round"):
        return None
    return artifacts.approval(run_dir, status["step1_round"], status.get("pending_approval") or [],
                              artifacts.contract(run_dir))


@app.get("/api/runs/{run_id}/proposals/{round_name}")
def proposal_round(run_id: str, round_name: str) -> dict[str, Any]:
    run = _run_dir(run_id)
    if not round_name.startswith("r") or not round_name[1:].isdigit():
        raise HTTPException(400, "提案轮次格式不正确")
    ranking = artifacts._json(run / "step1" / round_name / "ranking.json", []) or []
    if not ranking:
        raise HTTPException(404, "这一轮还没有评审排序")
    return {"enabled": True, "pending": False, **artifacts.approval(run, round_name, ranking, artifacts.contract(run))}


def _control(action: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Run a mars.api control call; its refusals (unknown run, already running, id taken) become 4xx."""
    try:
        return action()
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error
    except OSError as error:
        raise HTTPException(500, f"无法启动内核命令行：{error}") from error


@app.get("/api/runs/{run_id}/approval")
def approval_status(run_id: str) -> dict[str, Any]:
    pending = _pending(run_id)
    return {"enabled": True, "pending": pending is not None, **(pending or {})}


@app.post("/api/runs/{run_id}/approval", status_code=202)
def approve(run_id: str, body: ApprovalRequest) -> dict[str, Any]:
    pending = _pending(run_id)
    if pending is None:
        raise HTTPException(409, "这个运行当前没有等待确认的方案")
    selection = [s.strip() for s in body.approve if s.strip()]
    problems = artifacts.validate_selection(selection, pending)  # the core's handoff fails on unknown ids
    if problems:
        raise HTTPException(400, "；".join(problems))
    api = _api()

    def answer() -> dict[str, Any]:
        api.resume(run_id, approve=selection)
        return {"run_id": run_id, "approve": selection, "round": pending["round"]}

    return _control(answer)


# ----- hyperparameter search (Optuna over api.evaluate overrides) -----
class StudyRequest(BaseModel):
    spec: dict[str, Any]


@app.get("/api/hpo/meta")
def hpo_meta() -> dict[str, Any]:
    """Search space, the baseline checkout and every candidate iteration that still has a checkout."""
    api = _api()
    return {"search_space": api.search_space(), "baseline_dir": str(api.baseline_dir()),
            "max_parallel": gpu.MAX_PARALLEL, "max_trials": hpo.MAX_TRIALS, "candidates": _candidates(api)}


@app.get("/api/hpo")
def hpo_list() -> list[dict[str, Any]]:
    return _manager('hpo').list()


@app.post("/api/hpo")
def hpo_create(body: StudyRequest) -> dict[str, Any]:
    api = _api()
    spec, problems = hpo.normalize_spec(body.spec, api.search_space(), lambda d: Path(d).is_dir())
    if problems:
        raise HTTPException(400, "；".join(problems))
    return {"id": _manager('hpo').create({**spec, "project_root": _project_root()})}


@app.get("/api/hpo/{sid}")
def hpo_detail(sid: str) -> dict[str, Any]:
    try:
        return _manager('hpo').detail(sid)
    except KeyError as error:
        raise HTTPException(404, f"寻优 {sid} 不存在") from error


@app.post("/api/hpo/{sid}/{action}")
def hpo_control(sid: str, action: str) -> dict[str, Any]:
    try:
        if action == "stop":
            _manager('hpo').stop(sid)
        elif action == "resume":
            _manager('hpo').start(sid)
        else:
            raise HTTPException(404, f"未知操作 {action}")
    except KeyError as error:
        raise HTTPException(404, f"寻优 {sid} 不存在") from error
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error
    return {"id": sid, "action": action}


# ----- simulation verification (implement → evaluate candidate and baseline) -----
class VerifyRequest(BaseModel):
    title: str = ""
    template: str
    spec: str
    datasets: list[str]
    steps: int
    seed: int = 2026
    parent: str = "mars/base"
    environment: Literal["local", "remote"] | None = None


@app.get("/api/verify/meta")
def verify_meta() -> dict[str, Any]:
    api = _api()
    return {"templates": [{"name": t["name"], "title": t["title"]} for t in launcher.templates(_tasks_dir())],
            "datasets": api.search_space().get("datasets", []), "max_parallel": gpu.MAX_PARALLEL,
            "parents": [{"branch": c["branch"], "label": f"{c['run_id']} · {c['route']} 第 {c['iteration']} 轮", "change": c["change"],
                         "run_id": c["run_id"], "route": c["route"], "iteration": c["iteration"]}
                        for c in _candidates(api) if c.get("branch")]}


@app.get("/api/verify")
def verify_list() -> list[dict[str, Any]]:
    return _manager('verify').list()


@app.post("/api/verify")
def verify_create(body: VerifyRequest) -> dict[str, Any]:
    api = _api()
    datasets = api.search_space().get("datasets", [])
    problems = []
    if not body.spec.strip():
        problems.append("方案描述不能为空")
    if not body.datasets or any(d not in datasets for d in body.datasets):
        problems.append(f"数据集必须是 {datasets} 中的一个或多个")
    if body.steps < 0:
        problems.append("训练步数不能为负（0 表示完整预算）")
    if body.parent != "mars/base" and not body.parent.startswith("cand/"):
        problems.append("只能在基线或某个候选分支上实现")
    try:
        task = launcher.template_path(body.template, _tasks_dir())
    except ValueError as error:
        problems.append(str(error))
    if problems:
        raise HTTPException(400, "；".join(problems))
    vid = _manager('verify').create(body.title.strip()[:120], task, body.spec, list(dict.fromkeys(body.datasets)),
                               body.steps, body.seed, body.parent, body.environment, _project_root())
    return {"id": vid}


@app.get("/api/verify/{vid}")
def verify_detail(vid: str) -> dict[str, Any]:
    try:
        return _manager('verify').detail(vid)
    except KeyError as error:
        raise HTTPException(404, f"验证 {vid} 不存在") from error


@app.post("/api/verify/{vid}/{action}")
def verify_control(vid: str, action: str) -> dict[str, Any]:
    try:
        if action == "stop":
            _manager('verify').stop(vid)
        elif action == "resume":
            _manager('verify').resume(vid)
        else:
            raise HTTPException(404, f"未知操作 {action}")
    except KeyError as error:
        raise HTTPException(404, f"验证 {vid} 不存在") from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(409, str(error)) from error
    return {"id": vid, "action": action}


# ----- refine (the user's own design evolves near itself) -----
class RefineRequest(BaseModel):
    run_id: str
    contract_yaml: str
    proposal: str
    human_approval: bool = True


@app.post("/api/refine")
def refine(body: RefineRequest) -> dict[str, Any]:
    try:
        spec = yaml.safe_load(body.contract_yaml)
    except yaml.YAMLError as error:
        raise HTTPException(400, f"研究约定不是合法的 YAML：{error}") from error
    api = _api()
    return _control(lambda: launcher.start_refine(api, body.run_id.strip(), spec, body.proposal, body.human_approval,
                                                  _runs_root(), _tasks_dir()))


# ----- model connection settings (the file MARS_MODELS_CONFIG points at; keys only in its env file) -----
model_checks = models_config.CheckRunner(settings.core_root, settings.deploy_root, settings.data_dir / "model-checks")


class ModelsRequest(BaseModel):
    providers: dict[str, dict[str, Any]]
    roles: dict[str, dict[str, Any]]
    fallbacks: dict[str, str] = {}
    retries: int | None = None


class KeyRequest(BaseModel):
    name: str
    value: str


class CheckRequest(BaseModel):
    role: str
    skip_thinking: bool = False


def _editable_models() -> tuple[Path, dict[str, Any]]:
    path = models_config.config_path()
    if path is None:
        raise HTTPException(409, "没有设置 MARS_MODELS_CONFIG：内核正在用自带的 configs/models.yaml，应用不修改内核目录。"
                                 "请用 MARS_MODELS_CONFIG 指定一个配置文件后再编辑（内网部署包会自动设置）。")
    try:
        return path, models_config.load(path)
    except (OSError, ValueError, yaml.YAMLError) as error:
        raise HTTPException(500, f"读取 {path} 失败：{error}") from error


@app.get("/api/models")
def models_view() -> dict[str, Any]:
    return {**models_config.view(settings.core_root), "roles_required": list(models_config.ROLES),
            "check_available": model_checks.script.is_file(), "check_script": str(model_checks.script),
            "checks": model_checks.latest()}


@app.put("/api/models")
def models_save(body: ModelsRequest) -> dict[str, Any]:
    path, original = _editable_models()
    merged = models_config.merge(original, body.model_dump())
    try:
        models_config.save(path, merged)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return models_view()


@app.put("/api/models/keys")
def models_key(body: KeyRequest) -> dict[str, Any]:
    """Stores a key in the env file; the response says where and whether it is set, never the value."""
    path, cfg = _editable_models()
    try:
        written = models_config.set_key(path, cfg, body.name.strip(), body.value)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {**written, "keys": models_config.key_status(cfg, path)}


@app.post("/api/models/check")
def models_check(body: CheckRequest) -> dict[str, Any]:
    try:
        return {"id": model_checks.start(body.role, body.skip_thinking)}
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error


@app.get("/api/models/check/{cid}")
def models_check_status(cid: str) -> dict[str, Any]:
    try:
        return model_checks.get(cid)
    except KeyError as error:
        raise HTTPException(404, f"检查 {cid} 不存在") from error


# ----- run control -----
@app.get("/api/declaration")
def declaration() -> dict[str, Any]:
    """What the selected project declares (metrics, checks, datasets) and whether it can execute, read-only."""
    root = _project_root()
    if root is None:
        return {"ready": False, "metrics": {"primary": None, "fields": {}}, "checks": [], "datasets": [], "problems": ["请先新建或打开一个项目文件夹"]}
    found = _api_for(root).inspect_project()  # Scoped passes project_root
    binding = found.get("binding") or {}
    return {"ready": bool(found["ready"].get("local")), "metrics": binding.get("metrics") or {"primary": None, "fields": {}},
            "checks": binding.get("checks") or [], "datasets": [d["id"] for d in binding.get("datasets") or []],
            "problems": [c["message"] for c in found["checks"] if c["status"] == "fail" and "local" in c["required_for"]]}


@app.get("/api/templates")
def templates() -> list[dict[str, Any]]:
    return launcher.templates(_tasks_dir())


@app.post("/api/runs")
def start_run(body: StartRequest) -> dict[str, Any]:
    try:
        spec = yaml.safe_load(body.contract_yaml)
    except yaml.YAMLError as error:
        raise HTTPException(400, f"研究约定不是合法的 YAML：{error}") from error
    api = _api()
    return _control(lambda: launcher.start(api, body.run_id.strip(), spec, body.human_approval, _runs_root(), _tasks_dir()))


@app.post("/api/runs/{run_id}/resume")
def resume_run(run_id: str) -> dict[str, Any]:
    if _pending(run_id) is not None:
        raise HTTPException(409, "这个运行停在人工确认点，请先选择进入 Step2 的方案")
    api = _api()

    def resume() -> dict[str, Any]:
        api.resume(run_id)
        return {"run_id": run_id}

    return _control(resume)


@app.post("/api/runs/{run_id}/stop")
def stop_run(run_id: str) -> dict[str, Any]:
    """api.stop freezes the CLI, ends its whole process tree (training, gateway, CodingAgent) and keeps the checkpoint."""
    _run_dir(run_id)
    api = _api()
    if not api.stop(run_id):
        raise HTTPException(409, f"运行 {run_id} 没有在进行")
    return {"run_id": run_id, "stopped": True}


class StopRouteRequest(BaseModel):
    reason: str = "研究员在工作台停止了此路线"


@app.post("/api/runs/{run_id}/routes/{route_id}/stop")
def stop_route(run_id: str, route_id: str, body: StopRouteRequest) -> dict[str, Any]:
    found = run_detail(run_id)
    route = next((r for r in found["routes"] if r["id"] == route_id), None)
    if not route or route["status"] != "active" or found["state"] != "running":
        raise HTTPException(409, "这条路线当前没有在进化")
    _api().stop_route(run_id, route_id, reason=body.reason[:1000])
    return {"requested": True}


app.include_router(workspace_routes.routes(project_store, _api))


# ----- built frontend (web/dist) -----
DIST = APP_ROOT / "web/dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        return FileResponse(DIST / "index.html", headers={"Cache-Control": "no-cache"})  # an upgrade loads its new hashed assets
