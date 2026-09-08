"""Validate caller-provided Idea context without rewriting its source text."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

IDEA_CONTEXT_KEYS = frozenset({"background", "baseline_code", "data_description", "analysis_results", "metric_definition", "literature_notes"})


def validate_idea_context(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("idea_context must be an object of labeled text")
    result: dict[str, str] = {}
    for key, text in value.items():
        if not isinstance(key, str) or key not in IDEA_CONTEXT_KEYS:
            raise ValueError("idea_context contains an unsupported context label")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"idea_context.{key} must contain nonempty text")
        result[key] = text  # Preserve spaces, Unicode and line endings for evidence hashes.
    return result


class IdeaRequirements(BaseModel):
    """Optional overrides; omitted fields retain the Idea Agent's defaults."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    min_sources: int | None = Field(default=None, ge=0, le=100)
    min_pdfs: int | None = Field(default=None, ge=0, le=100)
    require_parameter_budget: bool | None = None
    max_parameter_ratio: float | None = Field(default=None, gt=0, le=100)
    require_evaluation_protocol: bool | None = None
    require_research_dossier: bool | None = None


def validate_idea_extra(extra: dict[str, Any]) -> dict[str, str]:
    """Validate internal persisted extras as well as the public API payload."""
    if "scope" in extra and extra["scope"] not in ("method_proposal", "project_proposal"):
        raise ValueError("Idea scope must be method_proposal or project_proposal")
    if "idea_requirements" in extra:
        requirements = extra["idea_requirements"]
        if isinstance(requirements, dict) and any(value is None for value in requirements.values()):
            raise ValueError("Persisted idea_requirements must omit unset fields instead of storing null")
        IdeaRequirements.model_validate(extra["idea_requirements"])
    if "idea_context" in extra:
        return validate_idea_context(extra["idea_context"])
    return {}
