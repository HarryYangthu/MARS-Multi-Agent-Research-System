"""Explicit model compatibility shared by request construction and agent loops."""
from __future__ import annotations

from app.harness.llm.provider_base import ReasoningEffort


class ModelCompatibilityError(ValueError):
    """Credential-free compatibility failure safe for product error displays."""


def requires_glm_thinking(provider: str, model: str) -> bool:
    name = model.strip().lower()
    return provider == "zhipu" and (name == "glm-5.3" or name.startswith("glm-5.3-"))


def setup_reasoning(provider: str, model: str) -> tuple[bool, ReasoningEffort | None]:
    """Bound setup/probe thinking explicitly instead of the model's max default."""
    return (True, "low") if requires_glm_thinking(provider, model) else (False, None)
