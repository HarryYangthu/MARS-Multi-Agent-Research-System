"""Top-bar aggregate stats: agent count / running / failed / artifacts."""
from __future__ import annotations

from collections import Counter
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.dependencies import get_orchestrator, get_run_store
from app.bridge.agent_registry import get_registry as get_agent_registry
from app.harness.kb.stores import ZONES, get_stores


class Stats(BaseModel):
    agents_registered: int
    runs_total: int
    runs_running: int
    runs_failed: int
    runs_waiting_review: int
    artifacts_total: int
    kb_total: int
    kb_per_zone: dict[str, int]
    states: dict[str, int]
    waiting_review_runs: list[dict[str, str]]


router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("", response_model=Stats)
async def get_stats() -> Stats:
    orch = get_orchestrator()
    store = get_run_store()
    agent_reg = get_agent_registry()

    state_counter: Counter[str] = Counter()
    runs_running = 0
    runs_failed = 0
    runs_waiting_review = 0
    artifacts_total = 0
    waiting_review_runs: list[dict[str, str]] = []
    runs = store.list()

    for r in runs:
        # If we still hold an in-memory session, use its live graph; otherwise
        # peek at the artifact filesystem to estimate completeness.
        try:
            sess = orch.session(r.run_id)
            states = sess.graph.all_states()
            for s in states.values():
                state_counter[s.value] += 1
            if any(v.value in {"running", "waiting_review", "approved"} for v in states.values()):
                runs_running += 1
            waiting_agents = [
                k for k, v in states.items() if v.value == "waiting_review"
            ]
            if waiting_agents:
                runs_waiting_review += 1
                waiting_review_runs.append(
                    {
                        "run_id": r.run_id,
                        "task": r.task,
                        "agent": waiting_agents[0],
                    }
                )
            if any(v.value == "failed" for v in states.values()):
                runs_failed += 1
        except KeyError:
            # Approximate from disk
            for sub in ("idea", "experiment", "coding", "execution", "writing"):
                d = r.root / sub
                if d.exists() and any(d.glob("*.approved.md")):
                    state_counter["done"] += 1
                else:
                    state_counter["unknown"] += 1
        # Count any *.md (v1/v2/approved) as a produced artifact
        for sub in ("idea", "experiment", "coding", "execution", "writing"):
            d = r.root / sub
            if d.exists():
                artifacts_total += sum(1 for p in d.glob("*.md"))

    kb = get_stores()
    kb_per_zone = {z: len(kb.zone(z).all()) for z in ZONES}
    return Stats(
        agents_registered=len(agent_reg.names()),
        runs_total=len(runs),
        runs_running=runs_running,
        runs_failed=runs_failed,
        runs_waiting_review=runs_waiting_review,
        artifacts_total=artifacts_total,
        kb_total=sum(kb_per_zone.values()),
        kb_per_zone=kb_per_zone,
        states=dict(state_counter),
        waiting_review_runs=waiting_review_runs,
    )
