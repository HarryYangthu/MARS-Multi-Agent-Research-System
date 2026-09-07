"""Pure schema/declaration tests; no model, tool or experimental substitutes."""
from copy import deepcopy
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from app.agents.idea.protocol import protocol_errors, protocol_schema


def proposal() -> dict[str, Any]:
    arm = {"training_data_refs": ["fit"], "assessment_data_refs": ["held"],
           "objective_ref": "/evaluation_protocol/objectives/loss",
           "optimizer_ref": "/method_spec/optimizer", "initialization_ref": "/method_spec/init"}
    return {"method_spec": {"train": "Training split definition", "test": "Held-out split definition",
                            "loss": "NMSE_train + lambda R", "optimizer": "Adam and schedule",
                            "init": "Seeded Gaussian initial weights"},
            "evaluation_protocol": {
                "version": "idea.evaluation.v1",
                "datasets": [{"id": "fit", "role": "train", "spec_ref": "/method_spec/train"},
                             {"id": "held", "role": "test", "spec_ref": "/method_spec/test"}],
                "objectives": {"loss": {"spec_ref": "/method_spec/loss", "data_refs": ["fit"]}},
                "arms": {"baseline": deepcopy(arm), "candidate": deepcopy(arm)},
                "randomness": {"seeds": [0, 1, 2], "sources": [{"name": "initialization", "spec_ref": "/method_spec/init"}]},
                "comparison": {"isolates_architecture": True, "differences_justification": ""}}}


def test_valid_protocol_and_schema() -> None:
    Draft202012Validator.check_schema(protocol_schema())
    assert protocol_errors(proposal(), required=True) == []


def test_legacy_opt_in() -> None:
    assert protocol_errors({}) == []
    assert protocol_errors({}, required=True)


@pytest.mark.parametrize("value", [None, [], "invalid", {}, {"version": "v0"}])
def test_malformed_protocol_is_reported(value: Any) -> None:
    assert protocol_errors({"evaluation_protocol": value})


@pytest.mark.parametrize("field", ["training_data_refs", "assessment_data_refs"])
@pytest.mark.parametrize("reference", ["missing", "held", "fit"])
def test_arm_data_roles(field: str, reference: str) -> None:
    value = proposal()
    value["evaluation_protocol"]["arms"]["baseline"][field] = [reference]
    valid = (field == "training_data_refs" and reference == "fit") or (field == "assessment_data_refs" and reference == "held")
    assert bool(protocol_errors(value)) is not valid


def test_heldout_objective_rejected() -> None:
    value = proposal()
    value["evaluation_protocol"]["objectives"]["loss"]["data_refs"] = ["held"]
    assert any("training objectives" in error for error in protocol_errors(value))


def test_heldout_alias_of_training_rejected() -> None:
    value = proposal()
    value["evaluation_protocol"]["datasets"][1]["spec_ref"] = "/method_spec/train"
    assert any("cannot share" in error for error in protocol_errors(value))


def test_distinct_assessment_datasets_rejected() -> None:
    value = proposal()
    value["method_spec"]["validation"] = "Another held-out split"
    value["evaluation_protocol"]["datasets"].append({"id": "val", "role": "validation", "spec_ref": "/method_spec/validation"})
    value["evaluation_protocol"]["arms"]["candidate"]["assessment_data_refs"] = ["val"]
    assert any("same held-out" in error for error in protocol_errors(value))


def test_objective_change_requires_explicit_scope_and_justification() -> None:
    value = proposal()
    value["method_spec"]["other_loss"] = "Different regularization"
    protocol = value["evaluation_protocol"]
    protocol["objectives"]["other"] = {"spec_ref": "/method_spec/other_loss", "data_refs": ["fit"]}
    protocol["arms"]["candidate"]["objective_ref"] = "/evaluation_protocol/objectives/other"
    assert any("same canonical objective" in error for error in protocol_errors(value))
    protocol["comparison"]["isolates_architecture"] = False
    assert any("differences_justification" in error for error in protocol_errors(value))
    protocol["comparison"]["differences_justification"] = "Evaluate architecture plus a changed regularizer together."
    assert protocol_errors(value) == []


def test_objective_training_data_must_belong_to_arm() -> None:
    value = proposal()
    value["method_spec"]["other_train"] = "Additional training set"
    protocol = value["evaluation_protocol"]
    protocol["datasets"].append({"id": "other", "role": "train", "spec_ref": "/method_spec/other_train"})
    protocol["objectives"]["loss"]["data_refs"] = ["other"]
    assert any("arm's training" in error for error in protocol_errors(value))


def test_repeated_deterministic_runs_are_not_independent_trials() -> None:
    value = proposal()
    value["evaluation_protocol"]["randomness"]["sources"] = []
    assert any("explicit random source" in error for error in protocol_errors(value))
    value["evaluation_protocol"]["randomness"]["seeds"] = [0]
    assert protocol_errors(value) == []


@pytest.mark.parametrize("seeds", [[0, 0], [], [True], [-1], [4294967296], ["1"]])
def test_invalid_seeds(seeds: list[Any]) -> None:
    value = proposal()
    value["evaluation_protocol"]["randomness"]["seeds"] = seeds
    assert protocol_errors(value)


@pytest.mark.parametrize("reference", ["/method_spec/missing", "/method_spec/init/x", "/method_spec/steps/-1", "/method_spec/steps/01", "/method_spec/steps/٠", "/method_spec/~2"])
def test_nonresolving_and_noncanonical_refs(reference: str) -> None:
    value = proposal()
    value["method_spec"]["steps"] = ["Initialize"]
    value["evaluation_protocol"]["arms"]["baseline"]["initialization_ref"] = reference
    assert any("reference" in error for error in protocol_errors(value))


def test_canonical_array_and_escaped_reference() -> None:
    value = proposal()
    value["method_spec"]["steps"] = ["Initialize"]
    value["method_spec"]["loss/description"] = "Loss definition"
    value["evaluation_protocol"]["arms"]["baseline"]["initialization_ref"] = "/method_spec/steps/0"
    value["evaluation_protocol"]["objectives"]["loss"]["spec_ref"] = "/method_spec/loss~1description"
    assert protocol_errors(value) == []


def test_duplicate_dataset_and_source_names_rejected() -> None:
    value = proposal()
    protocol = value["evaluation_protocol"]
    protocol["datasets"].append(deepcopy(protocol["datasets"][0]))
    protocol["randomness"]["sources"].append(deepcopy(protocol["randomness"]["sources"][0]))
    errors = protocol_errors(value)
    assert any("dataset ids" in error for error in errors)
    assert any("source names" in error for error in errors)


def test_dangling_objective_reference_rejected() -> None:
    value = proposal()
    value["evaluation_protocol"]["arms"]["baseline"]["objective_ref"] = "/evaluation_protocol/objectives/missing"
    assert any("unresolved" in error for error in protocol_errors(value))


def test_empty_method_definition_rejected() -> None:
    value = proposal()
    value["method_spec"]["init"] = {}
    assert any("empty" in error for error in protocol_errors(value))
