"""The same complete JSON contracts are shown to, and checked after, each role."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from jsonschema import Draft202012Validator


def _text(description: str) -> dict[str, Any]:
    return {"type": "string", "minLength": 1, "pattern": r"\S", "description": description}


def _texts(description: str, *, minimum: int = 0) -> dict[str, Any]:
    return {"type": "array", "minItems": minimum, "items": _text("A nonblank item."),
            "description": description}


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False,
            "required": list(properties), "properties": properties}


HYPOTHESIS = _object({
    "mechanism": _text("Explain the proposed mechanism and how it changes the baseline."),
    "statement": _text("A concrete research hypothesis, identifying the change and its conditional expected effect."),
    "testable_predictions": _texts("Observable predictions that could falsify the hypothesis.", minimum=1),
    "evidence_refs": _texts("Only references supplied in the input; an empty list declares absent support."),
    "constraints": _texts("Applicable task constraints and method assumptions."),
    "uncertainty": _text("Limitations, missing evidence and conditions under which this hypothesis may fail."),
})

REFLECTION = _object({
    "hypothesis_id": _text("Exactly one supplied hypothesis ID, with no duplicate or invented IDs."),
    "correctness": _text("Assess consistency and implementability; distinguish established facts from unresolved checks."),
    "novelty": _text("Assess distinction using only available evidence; say when novelty cannot be established."),
    "falsifiability": _text("Assess whether the stated predictions can be tested and contradicted."),
    "assumptions": _texts("Assumptions needed for the candidate's reasoning."),
    "failure_modes": _texts("Concrete failure conditions identified in the candidate."),
    "evidence_refs": _texts("Only supplied or candidate evidence references, without inventing sources."),
    "blockers": _texts("Unresolved reasons this candidate cannot proceed; empty if none is identified."),
})

PAIRWISE = _object({
    "outcome": {"type": "string", "enum": ["left", "right", "draw"]},
    "reason": _text("Explain this preference under the task constraints and available evidence; ranking is not proof."),
    "evidence_refs": _texts("Only references supplied for the corresponding pair or task."),
})

META_REVIEW = _object({
    "recurring_errors": _texts("Repeated issues actually observed in the supplied hypotheses and reviews."),
    "successful_patterns": _texts("Useful patterns observed in the current records, without claiming measured gains."),
    "evidence_gaps": _texts("Missing evidence that limits current conclusions."),
    "unexplored_regions": _texts("Potential directions not yet represented by the current pool."),
    "next_round_guidance": _texts("Specific suggestions for a later round; empty if no additional guidance is needed."),
})


def role_schema(role: str) -> dict[str, Any]:
    rows = {"generation": ("hypotheses", HYPOTHESIS), "reflection": ("reflections", REFLECTION),
            "pairwise_judge": ("decisions", PAIRWISE), "evolution": ("children", HYPOTHESIS)}
    if role == "meta_review":
        schema = deepcopy(META_REVIEW)
    elif role in rows:
        name, item = rows[role]
        schema = _object({name: {"type": "array", "items": deepcopy(item)}})
    else:
        raise ValueError(f"unknown discovery role {role}")
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", **schema}


def contract_errors(value: object, schema: dict[str, Any]) -> tuple[str, ...]:
    """Return field-specific failures without repairing or supplying model values."""
    errors: list[str] = []
    for error in Draft202012Validator(schema).iter_errors(value):
        path = "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in error.path)
        errors.append(f"{path}: {error.message}")
    return tuple(errors)
