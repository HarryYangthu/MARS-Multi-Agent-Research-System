from __future__ import annotations

import pytest

from app.harness.discovery.candidate_builder import (
    ConfigDelta,
    DeltaOperation,
    DeltaValidationError,
    apply_config_delta,
    build_candidate_record,
    derive_candidate_id,
    genome_fingerprint,
)
from app.harness.discovery.models import ModelGenome


def _genome(*, reverse: bool = False) -> ModelGenome:
    hyperparameters = {"width": 8, "rate": 0.1}
    if reverse:
        hyperparameters = {"rate": 0.1, "width": 8}
    return ModelGenome(
        family="example_family",
        structure={"block": {"depth": 2}},
        hyperparameters=hyperparameters,
        recipe={"epochs": 3},
        mutable_zones=("hyperparameters", "structure.block", "recipe.epochs"),
    )


def test_apply_config_delta_returns_new_genome_without_mutating_parent() -> None:
    parent = _genome()
    delta = ConfigDelta(
        operations=(
            DeltaOperation("set", ("hyperparameters", "rate"), 0.2),
            DeltaOperation("merge", ("structure", "block"), {"skip": True}),
            DeltaOperation("remove", ("recipe", "epochs")),
        )
    )

    child = apply_config_delta(
        parent,
        delta,
        allowed_zones=("hyperparameters.rate", "structure.block", "recipe.epochs"),
    )

    assert parent.hyperparameters["rate"] == 0.1
    assert child.hyperparameters["rate"] == 0.2
    assert child.structure["block"] == {"depth": 2, "skip": True}
    assert "epochs" not in child.recipe


def test_delta_outside_mutable_or_contract_zone_is_rejected() -> None:
    genome = ModelGenome(
        family="example_family",
        hyperparameters={"allowed": 1, "private": 2},
        mutable_zones=("hyperparameters.allowed", "hyperparameters.private"),
    )
    delta = ConfigDelta(
        operations=(DeltaOperation("set", ("hyperparameters", "private"), 3),)
    )

    with pytest.raises(DeltaValidationError, match="outside contract evolution zones"):
        apply_config_delta(
            genome,
            delta,
            allowed_zones=("hyperparameters.allowed",),
        )


def test_remove_of_missing_value_is_rejected_before_materialization() -> None:
    with pytest.raises(DeltaValidationError, match="does not exist"):
        apply_config_delta(
            _genome(),
            ConfigDelta(
                (DeltaOperation("remove", ("hyperparameters", "missing")),)
            ),
        )


def test_delta_cannot_replace_required_mapping_with_scalar() -> None:
    with pytest.raises(DeltaValidationError, match="materialized genome is invalid"):
        apply_config_delta(
            _genome(),
            ConfigDelta((DeltaOperation("set", ("hyperparameters",), 1),)),
        )


@pytest.mark.parametrize(
    "path",
    [
        ("family",),
        ("hyperparameters", "..", "rate"),
        ("hyperparameters", "nested/value"),
        ("hyperparameters", "__class__"),
    ],
)
def test_unsafe_or_immutable_delta_paths_are_rejected(path: tuple[str, ...]) -> None:
    with pytest.raises(DeltaValidationError):
        apply_config_delta(
            _genome(),
            ConfigDelta((DeltaOperation("set", path, 1),)),
        )


def test_candidate_identity_and_fingerprint_are_stable() -> None:
    first = build_candidate_record(
        run_id="run-1",
        genome=_genome(),
        creator="generator",
        operator="mutate",
        parent_ids=("parent-b", "parent-a"),
        generation=2,
        iteration=4,
    )
    second = build_candidate_record(
        run_id="run-1",
        genome=_genome(reverse=True),
        creator="generator",
        operator="mutate",
        parent_ids=("parent-a", "parent-b", "parent-a"),
        generation=2,
        iteration=4,
    )

    assert first.candidate_id == second.candidate_id
    assert first.parent_ids == ("parent-a", "parent-b")
    assert first.fingerprints == second.fingerprints
    assert first.fingerprints["exact"] == genome_fingerprint(first.genome)
    assert first.idempotency_key == f"candidate:{first.candidate_id}"
    assert first.candidate_id == derive_candidate_id(
        run_id="run-1",
        genome=first.genome,
        parent_ids=first.parent_ids,
        generation=2,
        iteration=4,
        creator="generator",
        operator="mutate",
    )
