"""Results of actual execution; no metrics are synthesized by this container."""
from dataclasses import dataclass, field


@dataclass
class SimulationResult:
    run_id: str
    experiment_id: str
    duration_seconds: float
    status: str
    metrics: dict[str, float]
    fingerprint_hash: str
    is_mock: bool = False  # Historical read compatibility; real execution must be false.
    loss_curve: list[float] = field(default_factory=list)
