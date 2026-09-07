"""Compose the public CPU regression pack; every metric comes from an actual fit."""
from pathlib import Path

from app.bridge.discovery_composition import ProjectPackCandidateAgent, ProjectPackRoutingAdapter
from app.bridge.discovery_service import DiscoveryService
from app.bridge.discovery_types import DiscoveryRunSpec
from app.bridge.extension_runtime import build_extension_runtime
from app.harness.runtime.event_bus import InProcessEventBus
from app.storage.run_store import RunStore


def build_service(root: Path) -> DiscoveryService:
    runtime = build_extension_runtime(
        distribution="v30-core",
        pack_roots=(Path(__file__).resolve().parents[2] / "projects/synthetic_regression",),
        execution_device="cpu",
        workspace_runs_root=root / "runs",
    )
    return DiscoveryService(
        run_store=RunStore(root / "runs"), event_bus=InProcessEventBus(),
        candidate_agent=ProjectPackCandidateAgent(runtime),
        adapter=ProjectPackRoutingAdapter(runtime),
    )


def discovery_spec(*, candidates: int = 20, iterations: int = 1, idea_mode: str = "auto") -> DiscoveryRunSpec:
    return DiscoveryRunSpec.model_validate({
        "task": "actual_regression_discovery", "project": "synthetic_regression",
        "objective": "minimize validation_mse",
        "objectives": [{"name": "validation_mse", "direction": "minimize", "unit": "squared_unit"}],
        "budget": {"proposals": candidates * iterations},
        "candidates_per_iteration": candidates, "max_iterations": iterations,
        "auto_approve": True, "idea_mode": idea_mode,
        "project_inputs": {"mode": "synthetic", "candidate_count": candidates},
    })
