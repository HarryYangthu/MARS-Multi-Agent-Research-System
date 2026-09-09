"""Pure schema/declaration tests; no model, tool or experimental substitutes."""
from copy import deepcopy
import json
import os
from pathlib import Path
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


@pytest.mark.parametrize("reference", ["/decision_rule", "/decision_rule/primary"])
def test_primary_comparison_can_explicitly_bind_its_decision(reference: str) -> None:
    value = proposal()
    value["decision_rule"] = {"primary": "Human-authored statistical decision contract."}
    value["evaluation_protocol"]["comparison"]["decision_rule_ref"] = reference
    assert protocol_errors(value) == []


@pytest.mark.parametrize("reference", ["/decision_rule/missing", "/method_spec/loss", "/decision_rule/",
                                      "/decision_rule/~2", "/decision_rule/steps/01"])
def test_explicit_primary_decision_must_resolve_canonically(reference: str) -> None:
    value = proposal()
    value["decision_rule"] = {"steps": ["Human-authored decision step."]}
    value["evaluation_protocol"]["comparison"]["decision_rule_ref"] = reference
    assert protocol_errors(value)


def test_primary_omission_preserves_legacy_validation_but_dangling_explicit_ref_fails() -> None:
    value = proposal()
    assert protocol_errors(value) == []
    value["evaluation_protocol"]["comparison"]["decision_rule_ref"] = "/decision_rule"
    assert any("unresolved" in error for error in protocol_errors(value))


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


def multiple_comparisons() -> dict[str, Any]:
    """Human-authored protocol declarations only; no execution is represented."""
    value = proposal()
    value["method_spec"].update({
        "train_A": "Independent case A training data and target definition.",
        "test_A": "Independent case A held-out data and target definition.",
        "loss_A": "Case A objective evaluated only on case A training data.",
        "baseline_A": "A separately initialized baseline model for case A.",
        "candidate_A": "A separately initialized candidate model for case A.",
        "ablation": "A separately initialized ablation of the primary candidate.",
    })
    value["decision_rule"] = {"primary": "Primary decision procedure.",
                              "case_A": "Case A decision procedure.", "ablation": "Ablation decision procedure."}
    protocol = value["evaluation_protocol"]
    protocol["datasets"].extend([
        {"id": "fit_A", "role": "train", "spec_ref": "/method_spec/train_A"},
        {"id": "held_A", "role": "test", "spec_ref": "/method_spec/test_A"},
    ])
    protocol["objectives"]["loss_A"] = {"spec_ref": "/method_spec/loss_A", "data_refs": ["fit_A"]}
    for name in ("baseline_A", "candidate_A"):
        protocol["arms"][name] = {**deepcopy(protocol["arms"]["baseline"]),
            "training_data_refs": ["fit_A"], "assessment_data_refs": ["held_A"],
            "objective_ref": "/evaluation_protocol/objectives/loss_A", "method_spec_ref": f"/method_spec/{name}"}
    protocol["arms"]["ablation"] = {**deepcopy(protocol["arms"]["candidate"]),
                                     "method_spec_ref": "/method_spec/ablation"}
    protocol["comparisons"] = {
        "case_A": {"baseline_arm": "baseline_A", "candidate_arm": "candidate_A",
                   "decision_rule_ref": "/decision_rule/case_A", "isolates_architecture": True,
                   "differences_justification": ""},
        "ablation": {"baseline_arm": "candidate", "candidate_arm": "ablation",
                     "decision_rule_ref": "/decision_rule/ablation", "isolates_architecture": True,
                     "differences_justification": ""},
    }
    return value


def test_independent_case_and_ablation_pairs_preserve_separate_training_bindings() -> None:
    value = multiple_comparisons()
    before = deepcopy(value)
    assert protocol_errors(value, required=True) == []
    assert value == before
    arms = value["evaluation_protocol"]["arms"]
    assert arms["baseline_A"]["training_data_refs"] == ["fit_A"]
    assert arms["candidate"]["training_data_refs"] == ["fit"]
    assert arms["ablation"]["training_data_refs"] == ["fit"]


def test_additional_arm_requires_a_resolving_method_definition() -> None:
    value = multiple_comparisons()
    arm = value["evaluation_protocol"]["arms"]["ablation"]
    del arm["method_spec_ref"]
    assert any("method_spec_ref" in error for error in protocol_errors(value))
    arm["method_spec_ref"] = "/method_spec/missing"
    assert any("unresolved" in error for error in protocol_errors(value))
    arm["method_spec_ref"] = "/method_spec/ablation"
    value["method_spec"]["ablation"] = {}
    assert any("empty" in error for error in protocol_errors(value))


@pytest.mark.parametrize("primary", ["baseline", "candidate"])
def test_additional_pairs_do_not_remove_the_primary_comparison(primary: str) -> None:
    value = multiple_comparisons()
    del value["evaluation_protocol"]["arms"][primary]
    assert any(f"'{primary}' is a required property" in error for error in protocol_errors(value))


def test_additional_arms_must_participate_in_named_comparisons() -> None:
    value = multiple_comparisons()
    del value["evaluation_protocol"]["comparisons"]["ablation"]
    assert any("/arms/ablation: additional arm must be referenced" in error for error in protocol_errors(value))
    del value["evaluation_protocol"]["comparisons"]
    assert any("/arms/baseline_A: additional arm must be referenced" in error for error in protocol_errors(value))


@pytest.mark.parametrize("field", ["baseline_arm", "candidate_arm", "decision_rule_ref", "isolates_architecture",
                                  "differences_justification"])
def test_named_comparison_requires_all_declared_fields(field: str) -> None:
    value = multiple_comparisons()
    del value["evaluation_protocol"]["comparisons"]["ablation"][field]
    assert any(field in error for error in protocol_errors(value))


@pytest.mark.parametrize("field", ["baseline_arm", "candidate_arm"])
def test_named_comparisons_reject_unknown_arms(field: str) -> None:
    value = multiple_comparisons()
    value["evaluation_protocol"]["comparisons"]["ablation"][field] = "missing"
    assert any("unknown arm reference" in error for error in protocol_errors(value))


def test_named_comparison_cannot_compare_an_arm_with_itself() -> None:
    value = multiple_comparisons()
    value["evaluation_protocol"]["comparisons"]["ablation"]["baseline_arm"] = "ablation"
    assert any("must be different arms" in error for error in protocol_errors(value))


@pytest.mark.parametrize("reference", ["/decision_rule/missing", "/method_spec/ablation", "/decision_rule/",
                                      "/decision_rule/~2", "/decision_rule/steps/01"])
def test_named_decision_reference_must_resolve_inside_decision_rule(reference: str) -> None:
    value = multiple_comparisons()
    value["decision_rule"]["steps"] = ["A decision step."]
    value["evaluation_protocol"]["comparisons"]["ablation"]["decision_rule_ref"] = reference
    assert protocol_errors(value)


def test_named_comparison_may_reference_the_complete_canonical_decision_rule() -> None:
    value = multiple_comparisons()
    value["evaluation_protocol"]["comparisons"]["ablation"]["decision_rule_ref"] = "/decision_rule"
    assert protocol_errors(value) == []


def test_each_named_pair_requires_same_held_out_data_even_for_nonarchitecture_comparisons() -> None:
    value = multiple_comparisons()
    value["evaluation_protocol"]["arms"]["ablation"]["assessment_data_refs"] = ["held_A"]
    pair = value["evaluation_protocol"]["comparisons"]["ablation"]
    pair.update(isolates_architecture=False, differences_justification="Intentional objective change.")
    assert any("/comparisons/ablation: both arms must use the same held-out" in error for error in protocol_errors(value))


def test_named_architecture_pair_requires_same_objective_and_training_data() -> None:
    value = multiple_comparisons()
    arm = value["evaluation_protocol"]["arms"]["ablation"]
    arm["training_data_refs"] = ["fit_A"]
    arm["objective_ref"] = "/evaluation_protocol/objectives/loss_A"
    errors = protocol_errors(value)
    assert any("/comparisons/ablation: architecture isolation requires the same canonical objective" in error for error in errors)
    assert any("/comparisons/ablation: architecture isolation requires the same training" in error for error in errors)
    pair = value["evaluation_protocol"]["comparisons"]["ablation"]
    pair["isolates_architecture"] = False
    assert any("differences_justification" in error for error in protocol_errors(value))
    pair["differences_justification"] = "This declared comparison intentionally changes the training conditions."
    assert protocol_errors(value) == []


@pytest.mark.parametrize("value", [None, [], {}, {"bad/name": {}}])
def test_present_malformed_comparisons_are_not_treated_as_legacy(value: Any) -> None:
    data = proposal()
    data["evaluation_protocol"]["comparisons"] = value
    assert protocol_errors(data)


def test_real_rejected_named_arms_are_not_retroactively_accepted() -> None:
    """An actual unmodified model response still lacks required primary/method/pair declarations."""
    configured = os.environ.get("MARS_TEST_PROTOCOL_ARMS_EVENTS")
    if not configured:
        pytest.skip("requires actual seventh-run native response events")
    path = Path(configured)
    before = path.read_bytes()
    events = [json.loads(line) for line in before.decode().splitlines()]
    response = next(event for event in events if event["kind"] == "model_response" and event["request"] == 7)
    call = next(item for item in response["visible"]["tool_calls"] if item["function"]["name"] == "mars_submit_document")
    metadata = json.loads(call["function"]["arguments"])["metadata"]
    original = deepcopy(metadata)
    errors = protocol_errors(metadata, required=True)
    assert any("'baseline' is a required property" in error for error in errors)
    assert any("'candidate' is a required property" in error for error in errors)
    assert any("method_spec_ref" in error for error in errors)
    assert metadata == original and path.read_bytes() == before
