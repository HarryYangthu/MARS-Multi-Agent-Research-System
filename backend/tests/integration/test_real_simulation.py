"""Phase C: real CPU execution backend (subprocess) + mock fallback."""
from __future__ import annotations

import pytest

from app.execution.config import get_execution_config
from app.execution.simulation_runner import (
    JobSpec,
    _resolve_repo_main,
    _run_real_experiment,
    run_one,
)


def test_execution_config_has_backend() -> None:
    cfg = get_execution_config()
    assert cfg.backend in ("mock", "real")
    assert cfg.backend == "mock"  # default in configs/execution.yaml (CLAUDE.md #9)
    assert cfg.job_timeout_seconds > 0


def test_resolve_repo_main_finds_stub() -> None:
    # moe-pimc's repo_link points at a symlink that may be absent; the
    # in-repo pimc-stub/main.py is the guaranteed fallback.
    main_py = _resolve_repo_main("moe-pimc")
    assert main_py is not None
    assert main_py.name == "main.py"


@pytest.mark.asyncio
async def test_real_backend_runs_subprocess() -> None:
    points: list[float] = []

    async def pub(_ch: str, p: dict) -> None:  # type: ignore[type-arg]
        if p.get("event") == "execution.curve_point":
            points.append(float(p["value"]))

    result = await run_one(
        JobSpec(run_id="r", experiment_id="ablation_a", project="moe-pimc"),
        bus_publish=pub,
        steps=12,
        backend="real",
    )
    assert result.is_mock is False
    assert result.status == "completed"
    assert {"loss", "RES", "PIM", "APE"} <= set(result.metrics)
    # Real gradient descent → loss actually goes down, and we streamed it.
    assert len(points) == 12
    assert points[-1] < points[0]


@pytest.mark.asyncio
async def test_mock_backend_is_default() -> None:
    result = await run_one(
        JobSpec(run_id="r", experiment_id="e", project="moe-pimc"),
        steps=3,
        backend="mock",
    )
    assert result.is_mock is True


@pytest.mark.asyncio
async def test_real_falls_back_to_mock_when_no_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.execution.simulation_runner._resolve_repo_main", lambda _project: None
    )
    result = await run_one(
        JobSpec(run_id="r", experiment_id="e", project="ghost"),
        steps=3,
        backend="real",
    )
    assert result.is_mock is True  # safety net keeps the e2e demo alive


@pytest.mark.asyncio
async def test_real_timeout_kills_subprocess() -> None:
    with pytest.raises(TimeoutError):
        await _run_real_experiment(
            JobSpec(run_id="r", experiment_id="slow", project="moe-pimc"),
            bus_publish=None,
            steps=10_000_000,
            timeout=0.001,
        )
