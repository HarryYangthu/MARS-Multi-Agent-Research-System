"""Shell-free subprocess implementation of adapter.v1."""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass

from app.execution.adapters.base import AdapterRequest, AdapterResponse


@dataclass(frozen=True)
class ProcessAdapter:
    name: str
    argv: tuple[str, ...]
    timeout_seconds: float = 900.0
    env: dict[str, str] | None = None

    def __post_init__(self) -> None:
        if not self.argv or not self.argv[0].strip():
            raise ValueError("adapter argv must not be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("adapter timeout must be positive")

    async def invoke(self, request: AdapterRequest) -> AdapterResponse:
        process = await asyncio.create_subprocess_exec(
            *self.argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, **(self.env or {})},
        )
        payload = request.model_dump_json().encode("utf-8")
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(payload),
                timeout=self.timeout_seconds,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            return AdapterResponse(
                request_id=request.request_id,
                status="failed",
                error_code="adapter_timeout",
                error=f"adapter '{self.name}' exceeded {self.timeout_seconds}s",
            )
        if process.returncode != 0:
            return AdapterResponse(
                request_id=request.request_id,
                status="failed",
                error_code="adapter_process_failed",
                error=stderr.decode("utf-8", errors="replace")[-4000:],
            )
        try:
            raw = json.loads(stdout.decode("utf-8"))
            return AdapterResponse.model_validate(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            return AdapterResponse(
                request_id=request.request_id,
                status="failed",
                error_code="adapter_invalid_response",
                error=str(exc),
            )
