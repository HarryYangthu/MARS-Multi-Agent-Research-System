"""Pin caller-specified performance gates without inventing measured results."""
from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator


def performance_schema(requirement: dict[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {key: {"const": value} for key, value in requirement.items()}
    properties.update({
        "selection_split": {"const": "validation"},
        "report_split": {"const": "held_out_test"},
        "acceptance_expression": {"type": "string", "minLength": 1},
        "status": {"const": "pending_experiment"},
    })
    required = list(properties)
    # Historical requirements stay valid. New callers can explicitly pin final
    # acceptance to held-out evaluation, independently of model selection.
    properties["acceptance_split"] = {"const": "held_out_test"}
    return {"type": "object", "additionalProperties": False,
            "required": required, "properties": properties}


def performance_errors(metadata: dict[str, Any], requirements: dict[str, Any]) -> list[str]:
    requirement = requirements.get("performance_requirement")
    if not isinstance(requirement, dict):
        return []
    rule = metadata.get("decision_rule", {})
    performance = rule.get("performance") if isinstance(rule, dict) else None
    return ["/decision_rule/performance/" + "/".join(map(str, error.absolute_path)) + ": " + error.message
            for error in Draft202012Validator(performance_schema(requirement)).iter_errors(performance)]
