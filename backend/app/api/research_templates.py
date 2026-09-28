"""Read saved settings for a new declaration; never reuse old admission."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, Query

from app.api.dependencies import get_run_store
from app.bridge.research_templates import (
    SavedResearchPage, SavedResearchSettings, TemplateMissing, TemplateUnavailable,
    list_research_settings, load_research_settings,
)

router = APIRouter(prefix="/api/research-templates", tags=["research-templates"])


@router.get("", response_model=SavedResearchPage)
def saved_settings(cursor: str | None = Query(default=None), limit: int | None = Query(default=None)) -> SavedResearchPage:
    try:
        return list_research_settings(get_run_store(), cursor=cursor, limit=limit)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_settings_query",
            "message": "Saved settings query or configured limits are unavailable"}) from exc


@router.get("/{run_id}", response_model=SavedResearchSettings)
def saved_setting(run_id: str = Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")) -> SavedResearchSettings:
    try:
        return load_research_settings(get_run_store(), run_id)
    except TemplateMissing as exc:
        raise HTTPException(status_code=404, detail={"code": "research_settings_not_found",
            "message": "No reusable saved research settings were found"}) from exc
    except (TemplateUnavailable, OSError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(status_code=409, detail={"code": "research_settings_unavailable",
            "message": "Saved authority is incomplete or invalid; settings cannot be reused"}) from exc
