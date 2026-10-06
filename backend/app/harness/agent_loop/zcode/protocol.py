"""Owned NDJSON client for the official ZCode app-server protocol."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import json
from pathlib import Path
from typing import Any

from app.harness.tools.process_runtime import start_process, terminate_process_tree


class ZCodeProtocolError(RuntimeError):
    pass


class ZCodeClient:
    def __init__(self, *, command: tuple[str, ...], cwd: Path, env: dict[str, str],
                 timeout: int, max_line_bytes: int) -> None:
        self.command, self.cwd, self.env = command, cwd, env
        self.timeout, self.max_line_bytes = timeout, max_line_bytes
        self.process: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task[None] | None = None
        self.stderr_reader: asyncio.Task[None] | None = None
        self.pending: dict[int, asyncio.Future[Any]] = {}
        self.counter = 0
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=2048)
        self.write_lock = asyncio.Lock()
        self.last_stderr = ""

    async def start(self) -> None:
        self.process = await start_process((*self.command, "app-server"), cwd=self.cwd, env=self.env,
                                           stream_limit=self.max_line_bytes)
        self.reader = asyncio.create_task(self._read())
        self.stderr_reader = asyncio.create_task(self._stderr())
        await self.call("runtime/capabilities", {})

    async def _send(self, message: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise ZCodeProtocolError("ZCode process not started")
        payload = json.dumps(message, ensure_ascii=False, allow_nan=False).encode() + b"\n"
        if len(payload) > self.max_line_bytes:
            raise ZCodeProtocolError("ZCode protocol request too large")
        async with self.write_lock:
            self.process.stdin.write(payload)
            await self.process.stdin.drain()

    async def call(self, method: str, params: dict[str, Any]) -> Any:
        self.counter += 1
        key = self.counter
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self.pending[key] = future
        try:
            await self._send({"id": key, "method": method, "params": params})
            return await asyncio.wait_for(future, self.timeout)
        finally:
            self.pending.pop(key, None)

    async def _read(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        failure = "ZCode process exited"
        try:
            while line := await self.process.stdout.readline():
                if len(line) > self.max_line_bytes:
                    raise ZCodeProtocolError("ZCode protocol response too large")
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ZCodeProtocolError("Invalid ZCode protocol envelope")
                key = value.get("id")
                if "method" in value and key is not None:
                    # File, command and network access belong to ToolRegistry.
                    # Never grant fullAccess or respond with provider credentials.
                    method = value["method"]
                    if method == "session/requestRuntimePreferences":
                        result = {"nativeSearchEnhancementsEnabled": False, "memoryEnabled": False,
                                  "askUserQuestionAutoResolutionEnabled": False}
                        await self._send({"id": key, "result": result})
                    elif method == "interaction/requestPermission":
                        await self._send({"id": key, "result": {"decision": "deny", "reason": "MARS tools only"}})
                    else:
                        await self._send({"id": key, "error": {"code": -32601, "message": "Host capability not granted by MARS"}})
                elif key in self.pending:
                    future = self.pending[key]
                    if not future.done():
                        if "error" in value:
                            future.set_exception(ZCodeProtocolError(str(value["error"].get("message", "RPC failed"))))
                        else:
                            future.set_result(value.get("result"))
                else:
                    # Public lifecycle events only; no private reasoning transcript.
                    if self.events.full():
                        self.events.get_nowait()
                    self.events.put_nowait(value)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failure = "ZCode protocol failed: " + type(exc).__name__
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(ZCodeProtocolError(failure))
            if not self.events.full():
                self.events.put_nowait({"method": "mars/processExited", "params": {"error": failure}})

    async def _stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        # Drain diagnostics to avoid pipe deadlocks. Do not store potentially
        # credential-bearing upstream errors or hidden model output.
        while chunk := await self.process.stderr.read(8192):
            self.last_stderr = "ZCode emitted diagnostics (" + str(len(chunk)) + " bytes)"

    async def close(self, session_id: str = "") -> None:
        if session_id and self.process is not None and self.process.returncode is None:
            with suppress(Exception):
                await asyncio.wait_for(self.call("session/stop", {"sessionId": session_id}), 3)
        if self.process is not None:
            await terminate_process_tree(self.process)
        for task in (self.reader, self.stderr_reader):
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
