"""Display identities from real request fields, without rewriting run evidence."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from app.execution.tensorboard_artifacts import _is_baseline


@pytest.mark.parametrize("identity, expected", [
    ("baseline_shortmix_bs512", True),
    ("candidate_shortmix_bs256", False),
    ("BASELINE_shortmix_bs512", True),
    ("candidate_other_model", False),
])
def test_missing_role_uses_only_the_individual_job_prefix(identity: str, expected: bool) -> None:
    request: dict[str, Any] = {"schema": "local_command_request.v1", "experiment_id": identity,
        "config": {"seed": 2026, "budget_unit": "epochs", "max_iters": 1, "batch_size": 512}}
    configuration: dict[str, Any] = {"model": {"mix_kernel": 3}, "lr_init": 0.0004}
    original_request, original_configuration = deepcopy(request), deepcopy(configuration)
    assert _is_baseline(request, configuration) is expected
    assert request == original_request and configuration == original_configuration


@pytest.mark.parametrize("role, identity, expected", [
    ("baseline", "candidate_model", True),
    ("candidate", "baseline_model", False),
    ("ablation", "baseline_model", False),
    ("custom", "baseline_model", False),
    (None, "baseline_model", False),
    ("", "baseline_model", False),
])
def test_an_existing_role_is_not_replaced_by_a_job_name(role: Any, identity: str, expected: bool) -> None:
    assert _is_baseline({"experiment_id": identity, "config": {"role": role}}, {}) is expected


@pytest.mark.parametrize("identity", [
    "candidate_vs_baseline",
    "baseline_vs_candidate",
    "baseline-versus-candidate",
    "baseline_and_candidate",
    "candidate_baseline_model",
    "baseline_model.candidate",
    "baseline_model-candidate",
    "baseline_model_vs_reference",
    "unknown_model",
    "comparison_baseline_model",
    "baselineish_model",
    "baseline",
    "baseline_../model",
    "baseline_模型",
    "baseline_model\n",
    "",
    None,
    7,
])
def test_joint_unknown_or_unsafe_job_identity_does_not_guess_a_baseline(identity: Any) -> None:
    assert not _is_baseline({"experiment_id": identity, "config": {}}, {})


def test_run_id_and_labels_do_not_supply_an_individual_job_identity() -> None:
    request = {"run_id": "baseline_candidate_comparison", "experiment_id": "unknown_model",
        "config": {"label": "baseline_original", "model_name": "BaselineModel"}}
    assert not _is_baseline(request, {})


def test_legacy_explicit_model_baseline_remains_compatible() -> None:
    assert _is_baseline({"config": {}, "experiment_id": "old_job"}, {"model": "baseline"})
    assert _is_baseline({"config": {"role": "baseline"}}, {"model": {"kernel": 3}})
    assert not _is_baseline({"config": {"role": "candidate"}}, {"model": "baseline"})
    assert not _is_baseline({"config": {"role": "ablation"}}, {"model": "baseline"})
    assert not _is_baseline({"config": {}, "experiment_id": "candidate_model"}, {"model": "baseline"})
