"""Host configuration for bounded metadata search and explicit source fallback."""
from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel, ConfigDict, Field
import yaml

from app.settings import repo_root


class ArxivPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    min_interval_seconds: int = Field(ge=3, le=30)
    request_timeout_seconds: int = Field(ge=1, le=30)
    fallback_to_openalex: bool


class OpenAlexPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_timeout_seconds: int = Field(ge=1, le=30)


class SearchPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    arxiv: ArxivPolicy
    openalex: OpenAlexPolicy


@lru_cache(maxsize=1)
def search_policy() -> SearchPolicy:
    return SearchPolicy.model_validate(yaml.safe_load(
        (repo_root() / "configs/literature_search.yaml").read_text(encoding="utf-8")))
