"""Actual numerical calculations and rejection of executable candidate syntax."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.diagnostics.contract_cpu_fixture import measure, validate_features


@pytest.mark.parametrize("source", [
    "import os\ndef features(x):\n    return (x,)\n",
    "def features(x=__import__('os')):\n    return (x,)\n",
    "@danger\ndef features(x):\n    return (x,)\n",
    "def features(x):\n    return (x.__class__,)\n",
    "def features(x):\n    return (__import__('os'),)\n",
    "def features(x):\n    return (x ** x,)\n",
    "def features(x):\n    return (x ** 999999,)\n",
    "def features(x):\n    return (True,)\n",
    "def features(x):\n    return (1e999,)\n",
    "def features(x):\n    return (x / 0,)\n",
    "def features(x):\n    return tuple(x for x in range(8))\n",
])
def test_generated_candidate_rejects_non_arithmetic_execution(source: str) -> None:
    with pytest.raises(ValueError):
        validate_features(source)


def test_real_measured_fit_repeats_with_seed_and_varies_across_seeds(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.py"
    candidate = tmp_path / "candidate.py"
    baseline.write_text("def features(x):\n    return (1.0, x)\n")
    candidate.write_text("def features(x):\n    return (1.0, x, x*x)\n")
    left = measure(baseline, 41)
    right = measure(candidate, 41)
    assert left == measure(baseline, 41)
    assert left != measure(baseline, 42)
    assert len(left[1]) == len(right[1]) == 64
    assert [row["x"] for row in left[1]] == [row["x"] for row in right[1]]
    assert right[0]["mse"] < left[0]["mse"]
    assert left[0]["mse"] == pytest.approx(sum(row["squared_error"] for row in left[1]) / 64)
