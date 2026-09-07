"""Declared comparison checks; these do not prove sample disjointness or science."""
from __future__ import annotations

import re
from typing import Any

from jsonschema import Draft202012Validator


def protocol_schema() -> dict[str, Any]:
    """Return a portable schema for evaluation_protocol, without local refs."""
    text = {"type": "string", "minLength": 1, "pattern": r"\S"}
    method_ref = {"type": "string", "pattern": "^/method_spec/.+"}
    refs = {"type": "array", "items": text, "minItems": 1, "uniqueItems": True}
    arm = {"type": "object", "additionalProperties": False,
           "required": ["training_data_refs", "assessment_data_refs", "objective_ref", "optimizer_ref", "initialization_ref"],
           "properties": {"training_data_refs": refs, "assessment_data_refs": refs,
                          "objective_ref": {"type": "string", "pattern": "^/evaluation_protocol/objectives/[A-Za-z][A-Za-z0-9_]*$"},
                          "optimizer_ref": method_ref, "initialization_ref": method_ref}}
    return {
        "type": "object", "additionalProperties": False,
        "required": ["version", "datasets", "objectives", "arms", "randomness", "comparison"],
        "properties": {
            "version": {"const": "idea.evaluation.v1"},
            "datasets": {"type": "array", "minItems": 2, "items": {
                "type": "object", "additionalProperties": False,
                "required": ["id", "role", "spec_ref"],
                "properties": {"id": text, "role": {"enum": ["train", "validation", "test"]}, "spec_ref": method_ref}}},
            "objectives": {"type": "object", "minProperties": 1,
                "propertyNames": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_]*$"},
                "additionalProperties": {"type": "object", "additionalProperties": False,
                    "required": ["spec_ref", "data_refs"],
                    "properties": {"spec_ref": method_ref, "data_refs": refs}}},
            "arms": {"type": "object", "additionalProperties": False,
                     "required": ["baseline", "candidate"], "properties": {"baseline": arm, "candidate": arm}},
            "randomness": {"type": "object", "additionalProperties": False,
                "required": ["seeds", "sources"], "properties": {
                    "seeds": {"type": "array", "minItems": 1, "uniqueItems": True,
                              "items": {"type": "integer", "minimum": 0, "maximum": 4294967295}},
                    "sources": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                        "required": ["name", "spec_ref"], "properties": {"name": text, "spec_ref": method_ref}}}}},
            "comparison": {"type": "object", "additionalProperties": False,
                "required": ["isolates_architecture", "differences_justification"],
                "properties": {"isolates_architecture": {"type": "boolean"}, "differences_justification": {"type": "string"}}},
        },
    }


def _reference(metadata: dict[str, Any], ref: str) -> Any:
    value: Any = metadata
    for raw in ref[1:].split("/"):
        if re.search(r"~(?![01])", raw):
            raise ValueError("invalid JSON pointer escape")
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", key):
                raise ValueError("noncanonical array index")
            value = value[int(key)]
        elif isinstance(value, dict):
            value = value[key]
        else:
            raise ValueError("reference traverses a scalar")
    if value is None or value == "" or value == {} or value == []:
        raise ValueError("reference is empty")
    return value


def protocol_errors(metadata: dict[str, Any], *, required: bool = False) -> list[str]:
    """Validate declarations. Legacy proposals may omit the field by default.

    Data roles cannot prove physical sample disjointness. References cannot prove
    a loss implements its description or that randomness is actually consumed.
    """
    prefix = "/evaluation_protocol"
    if "evaluation_protocol" not in metadata:
        return [prefix + ": required controlled-comparison protocol"] if required else []
    protocol = metadata["evaluation_protocol"]
    schema_errors = sorted(Draft202012Validator(protocol_schema()).iter_errors(protocol),
                           key=lambda error: str(list(error.absolute_path)))
    if schema_errors:
        return [prefix + "/" + "/".join(str(part) for part in error.absolute_path) + ": " + error.message
                for error in schema_errors]
    errors: list[str] = []

    def check_reference(ref: str, location: str) -> None:
        try:
            _reference(metadata, ref)
        except (ValueError, KeyError, IndexError) as exc:
            errors.append(f"{prefix}/{location}: unresolved or empty reference {ref}: {exc}")

    datasets = {item["id"]: item for item in protocol["datasets"]}
    if len(datasets) != len(protocol["datasets"]):
        errors.append(prefix + "/datasets: dataset ids must be unique")
    for index, item in enumerate(protocol["datasets"]):
        check_reference(item["spec_ref"], f"datasets/{index}/spec_ref")
    training = {name for name, item in datasets.items() if item["role"] == "train"}
    held_out = set(datasets) - training
    if not training or not held_out:
        errors.append(prefix + "/datasets: declare train and held-out validation or test datasets")
    train_specs = {datasets[name]["spec_ref"] for name in training}
    if train_specs & {datasets[name]["spec_ref"] for name in held_out}:
        errors.append(prefix + "/datasets: train and held-out datasets cannot share a canonical spec_ref")
    for name, objective in protocol["objectives"].items():
        check_reference(objective["spec_ref"], f"objectives/{name}/spec_ref")
        if not set(objective["data_refs"]) <= training:
            errors.append(prefix + f"/objectives/{name}/data_refs: training objectives may reference only declared train datasets")
    for name, arm in protocol["arms"].items():
        if not set(arm["training_data_refs"]) <= training:
            errors.append(prefix + f"/arms/{name}/training_data_refs: must reference declared train datasets")
        if not set(arm["assessment_data_refs"]) <= held_out:
            errors.append(prefix + f"/arms/{name}/assessment_data_refs: must reference declared held-out datasets")
        for field in ("optimizer_ref", "initialization_ref", "objective_ref"):
            check_reference(arm[field], f"arms/{name}/{field}")
        objective = protocol["objectives"].get(arm["objective_ref"].rsplit("/", 1)[-1])
        if objective and not set(objective["data_refs"]) <= set(arm["training_data_refs"]):
            errors.append(prefix + f"/arms/{name}/objective_ref: objective data must belong to the arm's training data")
    baseline, candidate = protocol["arms"]["baseline"], protocol["arms"]["candidate"]
    if set(baseline["assessment_data_refs"]) != set(candidate["assessment_data_refs"]):
        errors.append(prefix + "/arms: baseline and candidate must use the same held-out comparison datasets")
    if protocol["comparison"]["isolates_architecture"]:
        if baseline["objective_ref"] != candidate["objective_ref"]:
            errors.append(prefix + "/arms: architecture isolation requires the same canonical objective_ref for both arms")
        if set(baseline["training_data_refs"]) != set(candidate["training_data_refs"]):
            errors.append(prefix + "/arms: architecture isolation requires the same training datasets")
    elif not protocol["comparison"]["differences_justification"].strip():
        errors.append(prefix + "/comparison/differences_justification: explain intentional non-architecture differences")
    randomness = protocol["randomness"]
    if len(randomness["seeds"]) > 1 and not randomness["sources"]:
        errors.append(prefix + "/randomness/sources: multiple seeds require an explicit random source; deterministic repetitions are not independent trials")
    names = [source["name"] for source in randomness["sources"]]
    if len(names) != len(set(names)):
        errors.append(prefix + "/randomness/sources: random source names must be unique")
    for index, source in enumerate(randomness["sources"]):
        check_reference(source["spec_ref"], f"randomness/sources/{index}/spec_ref")
    return errors
