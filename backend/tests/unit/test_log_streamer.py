from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.execution.log_streamer import CommandSpec, stream_subprocess


@pytest.mark.asyncio
async def test_stream_subprocess_publishes_stdout_and_stderr(tmp_path: Path) -> None:
    events: list[tuple[str, dict[str, object]]] = []

    async def publish(channel: str, payload: dict[str, object]) -> None:
        events.append((channel, payload))

    result = await stream_subprocess(
        CommandSpec(
            argv=(
                sys.executable,
                "-c",
                "import sys; print('out'); print('err', file=sys.stderr)",
            )
        ),
        bus_publish=publish,
        channel="run.r.execution",
        log_path=tmp_path / "execution.log",
    )

    assert result.returncode == 0
    assert result.stdout_lines == 1
    assert result.stderr_lines == 1
    assert any(
        e[1].get("stream") == "stdout" and e[1].get("line") == "out"
        for e in events
    )
    assert any(
        e[1].get("stream") == "stderr" and e[1].get("line") == "err"
        for e in events
    )
    assert "[stdout] out" in (tmp_path / "execution.log").read_text(encoding="utf-8")
