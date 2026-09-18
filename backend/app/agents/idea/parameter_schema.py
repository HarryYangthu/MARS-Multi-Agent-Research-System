"""Shared parameter-ledger structure; arithmetic remains independently validated."""
from __future__ import annotations

from typing import Any


def _formula_schema() -> dict[str, Any]:
    return {"type": "string", "minLength": 1, "maxLength": 512,
            "description": "Executable arithmetic using declared numeric variables, + - * / and bounded integer powers. "
                           "No equals sign, appended result, tensor notation or prose."}


def _variables_schema() -> dict[str, Any]:
    return {"type": "object", "minProperties": 1,
            "additionalProperties": {"type": "number", "minimum": -1e12, "maximum": 1e12},
            "description": "Finite numeric values only. Put variable explanations in a different field."}


def _count_schema() -> dict[str, Any]:
    return {"type": "integer", "minimum": 1, "maximum": 1e12}


def parameter_components_schema() -> dict[str, Any]:
    """Describe real/complex tensor groups, including scalar [] shapes."""
    component = {"type": "object", "required": ["name", "formula", "dtype", "shape"],
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "formula": _formula_schema(),
            "dtype": {"enum": ["real", "complex"]},
            "shape": {"type": "array", "maxItems": 8,
                "description": "A JSON array of dimensions, not a tensor-shape string. [] denotes a scalar. "
                               "Examples: [16,16,17] or [\"C\",\"R\",\"K\"]. Each expression must evaluate "
                               "to a positive integer; complex dtype counts twice the dimension product.",
                "items": {"anyOf": [
                    {"type": "integer", "minimum": 1, "maximum": 1e12},
                    {"type": "string", "minLength": 1, "maxLength": 512},
                ]}},
        }}
    return {"type": "array", "minItems": 1, "items": component}


def parameter_budget_schema(*, require_evaluation_cases: bool = False) -> dict[str, Any]:
    """Expose the ledger contract without making focused proposals enumerate cases."""
    properties: dict[str, Any] = {"unit": {"const": "real_scalar"}, "variables": _variables_schema()}
    required = ["unit", "variables"]
    for prefix in ("baseline", "candidate"):
        properties[prefix + "_formula"] = _formula_schema()
        properties[prefix + "_parameters"] = _count_schema()
        properties[prefix + "_components"] = parameter_components_schema()
        required += [prefix + "_formula", prefix + "_parameters", prefix + "_components"]
    properties["evaluation_cases"] = {
        "type": "array", "minItems": 1, "maxItems": 32,
        "description": "Include the primary configuration unchanged: inherit or exactly repeat its "
                       "candidate_formula and candidate_components. Other sizes inherit its tensor ledger; "
                       "different candidate architectures may explicitly override BOTH candidate_formula "
                       "and candidate_components. All cases retain the baseline ledger and host budget limit.",
        "items": {"type": "object", "additionalProperties": False,
            "required": ["name", "variables", "baseline_parameters", "candidate_parameters"],
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 120},
                "variables": _variables_schema(),
                "baseline_parameters": _count_schema(),
                "candidate_parameters": _count_schema(),
                "candidate_formula": _formula_schema(),
                "candidate_components": parameter_components_schema(),
            },
            "dependentRequired": {"candidate_formula": ["candidate_components"],
                                  "candidate_components": ["candidate_formula"]},
        },
    }
    if require_evaluation_cases:
        required.append("evaluation_cases")
    return {"type": "object", "required": required, "properties": properties}
