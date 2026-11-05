"""Folder management and artifact browsing. All project metadata belongs to mars-app, not mars-core."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Literal

import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from mars_app import library, projects
from mars_app.artifacts import RUN_ID
from mars_app.launcher import validate_contract


class ProjectBody(BaseModel):
    path: str = Field(min_length=1)
    config: dict[str, Any] = {}


class ContractBody(BaseModel):
    yaml: str | None = None
    contract: dict[str, Any] | None = None
    environment: Literal["local", "remote"] | None = None


class DraftBody(BaseModel):
    run_id: str
    contract_yaml: str
    human_approval: bool = True


def routes(store: projects.ProjectStore, api: Callable[[], Any]) -> APIRouter:
    router = APIRouter(prefix="/api")
    cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
    cache_lock = threading.RLock()

    def current() -> dict[str, Any]:
        return projects.selected.get() or store.get("current")

    def entries(refresh: bool = False) -> list[dict[str, Any]]:
        project = current()
        key = project["id"]
        with cache_lock:
            old = cache.get(key)
            if old and not refresh and time.monotonic() - old[0] < 30:
                return old[1]
            found = library.index(Path(project["runs_dir"]), Path(project["reports_dir"]))
            if len(cache) > 8:
                cache.clear()
            cache[key] = (time.monotonic(), found)
            return found

    @router.get("/projects")
    def project_list():
        return {"projects": store.list(), "default_parent": str(store.data_dir / "projects")}

    @router.post("/projects/preflight")
    def preflight(body: ProjectBody):
        return {"checks": store.preflight(body.path, body.config)}

    @router.post("/projects")
    def create(body: ProjectBody):
        return store.create(body.path, body.config)

    @router.post("/projects/open")
    def open_project(body: ProjectBody):
        return store.create(body.path, body.config, opening=True)

    @router.put("/projects/{pid}")
    def update(pid: str, body: dict[str, Any]):
        return store.update(pid, body)

    @router.put("/projects/{pid}/location")
    def move(pid: str, body: ProjectBody):
        """Moving is refused while any of the project's runs has a live process (its paths would vanish under it)."""
        project = store.get(pid)
        if not project["builtin"] and project["connected"]:
            scoped = api()
            running = [r["run_id"] for r in scoped.list_runs() if scoped.status(r["run_id"]).get("running")]
            if running:
                raise ValueError(f"这些运行还在进行，先停止再移动项目：{'、'.join(running)}")
        return store.move(pid, body.path)

    @router.post("/projects/{pid}/contracts")
    def save_contract(pid: str, body: DraftBody):
        project = store.get(pid)
        if not RUN_ID.fullmatch(body.run_id):
            raise ValueError("研究约定名称格式不正确")
        try:
            spec = yaml.safe_load(body.contract_yaml)
        except yaml.YAMLError as error:
            raise ValueError("研究约定不是合法的 YAML") from error
        errors = validate_contract(spec)
        if errors:
            raise ValueError("；".join(errors))
        root = store.data_dir if project["builtin"] else Path(project["path"])
        directory = root / "tasks"
        path = directory / f"{body.run_id}.yaml"
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("研究约定必须保存在项目目录内")
        directory.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.with_suffix(".yaml.bak").write_bytes(path.read_bytes())
        tmp = path.with_suffix(".yaml.tmp")
        tmp.write_text(yaml.safe_dump({**spec, "human_approval": body.human_approval}, allow_unicode=True, sort_keys=False), encoding="utf-8")
        tmp.replace(path)
        return {"path": str(path), "saved": True}

    @router.get("/filesystem")
    def filesystem(path: str = "", files: bool = False):
        return projects.browse(path, files)

    @router.get("/project/overview")
    def overview():
        return library.overview(Path(current()["runs_dir"]))

    @router.post("/contracts/check")
    def contract(body: ContractBody):
        try:
            spec = yaml.safe_load(body.yaml) if body.yaml is not None else body.contract
        except yaml.YAMLError as error:
            raise HTTPException(400, f"研究约定不是合法的 YAML：{error}") from error
        if body.environment is not None and isinstance(spec, dict):
            execution = spec.get("execution")
            spec = {**spec, "execution": {**(execution if isinstance(execution, dict) else {}), "environment": body.environment}}
        errors = validate_contract(spec)
        return {"contract": spec, "yaml": yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), "errors": errors}

    @router.get("/artifacts")
    def artifacts():
        return entries(refresh=True)

    @router.get("/artifacts/{aid}/preview")
    def artifact_preview(aid: str):
        item = library.find(entries(), aid)
        if item["virtual"]:
            diff = api().iteration_diff(item["run_id"], item["route"], item["iteration"])
            return {"format": "diff", "diff": diff}
        return library.preview(item)

    @router.get("/artifacts/{aid}/file")
    def artifact_file(aid: str, download: bool = False):
        item = library.find(entries(), aid)
        if item["virtual"]:
            diff = api().iteration_diff(item["run_id"], item["route"], item["iteration"])
            text = "\n".join(f["patch"] for f in diff.get("files", []))
            return PlainTextResponse(text, headers={"Content-Disposition": "attachment; filename=changes.patch"})
        path = Path(item["path"])
        return FileResponse(path, filename=path.name if download else None)

    @router.post("/artifacts/{aid}/reveal")
    def reveal(aid: str):
        item = library.find(entries(), aid)
        if item["virtual"]:
            raise ValueError("代码差异请在工作台查看")
        path = Path(item["path"])
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif os.name == "nt":
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"opened": True}

    return router
