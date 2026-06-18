"""Knowledge-base read API for the front-end.

Browse + count + search the 4 KB zones.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.harness.kb.retriever import query as kb_query
from app.harness.kb.stores import MAIN_ZONES, QUARANTINE_ZONE, get_stores

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class ZoneSummary(BaseModel):
    name: str
    label_zh: str
    count: int


class KBItem(BaseModel):
    id: str
    zone: str
    text: str
    text_excerpt: str
    metadata: dict[str, Any]


class SearchHit(BaseModel):
    score: float
    item: KBItem


_LABELS_ZH: dict[str, str] = {
    "literature": "文献库",
    "methodology": "方法库",
    "code_assets": "代码资产库",
    "run_archive": "实验运行档案",
}


def _to_item(rec: Any) -> KBItem:
    return KBItem(
        id=rec.id,
        zone=rec.zone,
        text=rec.text,
        text_excerpt=rec.text[:280],
        metadata=rec.metadata,
    )


@router.get("/zones", response_model=list[ZoneSummary])
async def list_zones() -> list[ZoneSummary]:
    s = get_stores()
    return [
        ZoneSummary(
            name=z,
            label_zh=_LABELS_ZH.get(z, z),
            count=len(s.zone(z).all(exclude_mock=True, exclude_superseded=True)),
        )
        for z in MAIN_ZONES
    ]


@router.get("/search", response_model=list[SearchHit])
async def search_all(
    q: str,
    top_k: int = 5,
    zone: str | None = None,
    zones: str | None = None,
    project: str | None = None,
    memory_type: str | None = None,
    include_mock: bool = False,
    include_superseded: bool = False,
    profile: str | None = None,
) -> list[SearchHit]:
    del profile  # Reserved for backend profile adapters; defaults stay safe.
    if not q.strip():
        return []
    selected_zones = _parse_zones(zones or zone)
    hits = kb_query(
        query=q,
        zones=selected_zones,
        top_k=top_k,
        project=project,
        memory_type=memory_type,
        include_mock=include_mock,
        include_superseded=include_superseded,
    )
    return [SearchHit(score=h.score, item=_to_item(h.record)) for h in hits]


@router.get("/quarantine/items", response_model=list[KBItem])
async def list_quarantine_items(
    limit: int = 20,
    project: str | None = None,
    memory_type: str | None = None,
    include_mock: bool = True,
    include_superseded: bool = True,
) -> list[KBItem]:
    s = get_stores()
    filters = _filters(project=project, memory_type=memory_type)
    records = s.zone(QUARANTINE_ZONE).all(
        filters=filters,
        exclude_mock=not include_mock,
        exclude_superseded=not include_superseded,
    )[:limit]
    return [_to_item(rec) for rec in records]


@router.get("/quarantine/search", response_model=list[SearchHit])
async def search_quarantine(
    q: str,
    top_k: int = 5,
    project: str | None = None,
    memory_type: str | None = None,
    include_mock: bool = True,
    include_superseded: bool = True,
) -> list[SearchHit]:
    if not q.strip():
        return []
    hits = kb_query(
        query=q,
        zones=[QUARANTINE_ZONE],
        top_k=top_k,
        project=project,
        memory_type=memory_type,
        include_mock=include_mock,
        include_superseded=include_superseded,
    )
    return [SearchHit(score=h.score, item=_to_item(h.record)) for h in hits]


@router.get("/{zone}/items", response_model=list[KBItem])
async def list_items(zone: str, limit: int = 20) -> list[KBItem]:
    s = get_stores()
    if zone not in MAIN_ZONES:
        raise HTTPException(status_code=404, detail=f"unknown zone '{zone}'")
    records = s.zone(zone).all(exclude_mock=True, exclude_superseded=True)[:limit]
    return [_to_item(rec) for rec in records]


@router.get("/{zone}/search", response_model=list[SearchHit])
async def search(
    zone: str,
    q: str,
    top_k: int = 5,
    project: str | None = None,
    memory_type: str | None = None,
    include_mock: bool = False,
    include_superseded: bool = False,
) -> list[SearchHit]:
    if zone not in MAIN_ZONES:
        raise HTTPException(status_code=404, detail=f"unknown zone '{zone}'")
    if not q.strip():
        return []
    hits = kb_query(
        query=q,
        zones=[zone],
        top_k=top_k,
        project=project,
        memory_type=memory_type,
        include_mock=include_mock,
        include_superseded=include_superseded,
    )
    return [SearchHit(score=h.score, item=_to_item(h.record)) for h in hits]


def _parse_zones(raw: str | None) -> list[str]:
    if not raw:
        return list(MAIN_ZONES)
    zones = [item.strip() for item in raw.split(",") if item.strip()]
    invalid = [item for item in zones if item not in MAIN_ZONES]
    if invalid:
        raise HTTPException(status_code=404, detail=f"unknown zone '{invalid[0]}'")
    return zones or list(MAIN_ZONES)


def _filters(
    *,
    project: str | None,
    memory_type: str | None,
) -> dict[str, Any] | None:
    filters: dict[str, Any] = {}
    if project:
        filters["project"] = project
    if memory_type:
        filters["memory_type"] = memory_type
    return filters or None
