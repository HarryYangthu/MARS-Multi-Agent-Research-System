"""Reproduce bounded counterexamples to the archived, rejected model proposal.

This is an external inspection of that exact candidate, not an Agent tool run,
an implementation of the proposed model, or a PIMC performance experiment.
Run with the project's Python environment from any directory.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import yaml


def inspect(root: Path) -> dict[str, object]:
    raw = (root / "proposal.md").read_bytes()
    provenance = json.loads((root / "provenance.json").read_text())
    digest = hashlib.sha256(raw).hexdigest()
    if digest != provenance["proposal_sha256"]:
        raise ValueError("The candidate changed; this review binds to its original bytes")
    metadata = yaml.safe_load(raw.decode().split("---", 2)[1])
    cases = []
    for case in metadata["parameter_budget"]["evaluation_cases"]:
        size = case["variables"]["G"]
        baseline, candidate = size * size, size * size + 2 * (size - 1)
        cases.append({"case": case["name"], "baseline": baseline,
                      "candidate": candidate, "ratio": candidate / baseline,
                      "matches_declared": (baseline == case["baseline_parameters"]
                                           and candidate == case["candidate_parameters"]),
                      "within_test_limit": candidate / baseline <= 1.2})

    # Both closed intervals contain an internal grid point.
    grid = [-1.0, -0.5, 0.5, 1.0]
    boundary = grid[1]
    matching_cells = [i for i in range(3) if grid[i] <= boundary <= grid[i + 1]]

    # Positive raw spacings are normalized by their sum in the proposal.
    spacing = 2.0 / 15
    raw_gaps = [spacing] * 15
    scaled = [2 * gap for gap in raw_gaps]

    def normalized(gaps: list[float]) -> list[float]:
        return [2 * gap / sum(gaps) for gap in gaps]

    physical_difference = max(abs(a - b) for a, b in zip(normalized(raw_gaps), normalized(scaled)))
    raw_deviation = sum(abs(gap - spacing) / spacing for gap in scaled) / len(scaled)

    # Away from selection boundaries, hard nearest-neighbour gathered values
    # do not depend locally on node positions. This does NOT say sorting values
    # has zero derivative, nor that all grid-training methods are impossible.
    values, coordinate, epsilon = [2.0, 3.0, 5.0, 11.0], 0.1, 1e-6

    def nearest(nodes: list[float]) -> float:
        return values[min(range(len(nodes)), key=lambda i: abs(nodes[i] - coordinate))]

    derivatives = []
    for index in (1, 2):
        left, right = grid.copy(), grid.copy()
        left[index] -= epsilon
        right[index] += epsilon
        derivatives.append((nearest(right) - nearest(left)) / (2 * epsilon))

    return {"candidate_sha256": digest, "inspection": "external_bounded_numerical_checks",
            "scientific_validated": False, "simulation_executed": False,
            "parameter_cases": cases,
            "internal_boundary": {"coordinate": boundary, "matching_cells": matching_cells,
                                  "issue": "closed intervals do not specify a unique cell at internal nodes"},
            "movement_metric": {"raw_spacing_scale": 2, "normalized_gap_max_change": physical_difference,
                                "raw_deviation_metric": raw_deviation,
                                "issue": "raw-gap deviation can change while the physical grid is unchanged"},
            "hard_nearest_neighbor": {"coordinate": coordinate, "epsilon": epsilon,
                                      "interior_node_finite_differences": derivatives,
                                      "issue": "A2 needs an actual optimization path for node locations"},
            "floating_point_spacing": {"softplus_negative_1000_float64": math.log1p(math.exp(-1000)),
                                       "issue": "positive real-arithmetic spacing needs a numerical safeguard in an implementation"}}


if __name__ == "__main__":
    folder = Path(__file__).resolve().parent / "previous_candidate"
    (folder / "numerical_inspection.json").write_text(
        json.dumps(inspect(folder), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
