"""Managed, run-scoped TensorBoard servers and execution-display activation."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import sys
import time
from typing import Any
import uuid

import httpx
import yaml

from app.harness.persistence import atomic_write_json
from app.harness.project_workspace import project_root
from app.harness.tools.process_runtime import start_process, terminate_process_tree
from app.settings import repo_root
from app.storage.run_store import RunHandle


def tensorboard_config() -> dict[str, Any]:
    path = repo_root() / "configs/tensorboard.yaml"
    raw = yaml.safe_load(path.read_text()) if path.is_file() else {}
    return dict((raw or {}).get("tensorboard", {}))


def project_logdirs(project: str) -> list[Path]:
    root = project_root(project)
    link_path = root / "repo_link.yaml"
    if not link_path.is_file():
        raise ValueError("项目尚未配置研究代码目录")
    link = yaml.safe_load(link_path.read_text()) or {}
    raw_repo = str(link.get("repo_path", ""))
    if not raw_repo:
        raise ValueError("项目尚未配置研究代码目录")
    repo = (root / raw_repo).resolve()
    configured = tensorboard_config().get("project_logdirs", {}).get(project, ["runs"])
    paths: list[Path] = []
    for item in configured:
        candidate = (repo / str(item)).resolve()
        if not candidate.is_relative_to(repo):
            raise ValueError("TensorBoard 日志目录必须位于项目代码目录内")
        if candidate.is_dir() and candidate not in paths:
            paths.append(candidate)
    if not paths:
        raise ValueError("项目尚无 TensorBoard 日志目录；执行训练后可查看对应运行")
    return paths


def scope_key(project: str, run_id: str | None) -> str:
    return hashlib.sha256(json.dumps([project, run_id]).encode()).hexdigest()[:24]


@dataclass
class TensorBoardSession:
    key: str
    project: str
    run_id: str | None
    logdirs: list[Path]
    port: int
    process: asyncio.subprocess.Process
    last_used: float

    @property
    def prefix(self) -> str:
        return f"/api/tensorboard/view/{self.key}"

    def view(self) -> dict[str, Any]:
        return {"key": self.key, "project": self.project, "run_id": self.run_id,
                "url": self.prefix + "/", "status": "ready",
                "logdirs": [str(p) for p in self.logdirs]}


class TensorBoardManager:
    def __init__(self, state_dir: Path | None = None) -> None:
        self.state_dir = state_dir or repo_root() / "workspace/.tensorboard"
        self.sessions: dict[str, TensorBoardSession] = {}
        self.activations: dict[str, dict[str, Any]] = {}
        self.lock = asyncio.Lock()

    async def ensure(self, project: str, run_id: str | None, logdirs: list[Path]) -> TensorBoardSession:
        if not logdirs or any(not p.is_dir() or "," in str(p) for p in logdirs):
            raise ValueError("TensorBoard 日志目录不可用")
        key = scope_key(project, run_id)
        async with self.lock:
            previous = self.sessions.get(key)
            if previous and previous.process.returncode is None:
                previous.last_used = time.monotonic()
                return previous
            if importlib.util.find_spec("tensorboard") is None:
                raise RuntimeError("当前 Python 环境缺少 TensorBoard，请安装 MARS 的 pimc 依赖")
            cfg = tensorboard_config()
            live = [s for s in self.sessions.values() if s.process.returncode is None]
            if len(live) >= int(cfg.get("max_sessions", 8)):
                active_keys = {str(a["key"]) for a in self.activations.values() if a["phase"] == "running"}
                idle = [s for s in live if s.key not in active_keys]
                if not idle:
                    raise RuntimeError("正在展示的实验过多，请稍后重试")
                oldest = min(idle, key=lambda s: s.last_used)
                await terminate_process_tree(oldest.process)
                del self.sessions[oldest.key]
            self.state_dir.mkdir(parents=True, exist_ok=True)
            # Reserve a loopback port, then verify the child that owns it started.
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = int(reservation.getsockname()[1])
            prefix = f"/api/tensorboard/view/{key}"
            argv = [sys.executable, "-m", "tensorboard.main", "--host", "127.0.0.1", "--port", str(port),
                    "--path_prefix", prefix, "--load_fast=false", "--reload_interval", str(cfg.get("reload_interval", 3)),
                    "--window_title", "MARS · TensorBoard"]
            if len(logdirs) == 1:
                argv += ["--logdir", str(logdirs[0])]
            else:
                argv += ["--logdir_spec", ",".join(f"source_{i + 1}:{p}" for i, p in enumerate(logdirs))]
            log_path = self.state_dir / f"{key}.log"
            with log_path.open("ab") as log:
                process = await start_process(argv, stdin=asyncio.subprocess.DEVNULL,
                                              stdout=log, stderr=asyncio.subprocess.STDOUT)
            session = TensorBoardSession(key, project, run_id, logdirs, port, process, time.monotonic())
            try:
                deadline = time.monotonic() + float(cfg.get("startup_timeout_seconds", 30))
                async with httpx.AsyncClient(trust_env=False, timeout=2) as client:
                    while time.monotonic() < deadline:
                        if process.returncode is not None:
                            raise RuntimeError("TensorBoard 启动失败，请检查服务日志：" + str(log_path))
                        try:
                            response = await client.get(f"http://127.0.0.1:{port}{prefix}/data/environment")
                            if response.status_code == 200:
                                self.sessions[key] = session
                                return session
                        except httpx.HTTPError:
                            pass
                        await asyncio.sleep(0.2)
                raise RuntimeError("TensorBoard 启动超时，请检查服务日志：" + str(log_path))
            except BaseException:
                await terminate_process_tree(process)
                raise

    async def activate(self, run: RunHandle, attempt: int) -> dict[str, Any]:
        # Only the approved execution path calls this; opening a viewer never trains.
        session = await self.ensure(run.project, run.run_id, [run.root.resolve()])
        activation = {**session.view(), "activation_id": uuid.uuid4().hex, "phase": "running",
                      "attempt": attempt, "started_at": time.time(), "task": run.task}
        self.activations[run.project] = activation
        atomic_write_json(run.subdir("execution") / "tensorboard.json", activation)
        return activation

    def finish(self, run: RunHandle, phase: str) -> None:
        activation = self.activations.get(run.project)
        if activation and activation["run_id"] == run.run_id:
            activation = {**activation, "phase": phase}
            self.activations[run.project] = activation
            atomic_write_json(run.subdir("execution") / "tensorboard.json", activation)

    async def close(self) -> None:
        for session in self.sessions.values():
            if session.process.returncode is None:
                await terminate_process_tree(session.process)
        self.sessions.clear()
        self.activations.clear()


_managers: dict[asyncio.AbstractEventLoop, TensorBoardManager] = {}


def get_tensorboard_manager() -> TensorBoardManager:
    # Subprocess transports and locks belong to the loop that created them.
    # Separate application lifespans must not await or terminate each other's
    # TensorBoard sessions during shutdown.
    loop = asyncio.get_running_loop()
    if loop not in _managers:
        _managers[loop] = TensorBoardManager()
    return _managers[loop]


async def shutdown_tensorboard() -> None:
    manager = _managers.pop(asyncio.get_running_loop(), None)
    if manager is not None:
        await manager.close()
