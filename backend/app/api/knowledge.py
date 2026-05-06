"""Knowledge-base read API for the front-end.

Browse + count + search the 4 KB zones.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.harness.kb.retriever import query as kb_query
from app.harness.kb.stores import ZONES, get_stores

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class ZoneSummary(BaseModel):
    name: str
    label_zh: str
    count: int


class KBItem(BaseModel):
    id: str
    zone: str
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
            count=len(s.zone(z).all()),
        )
        for z in ZONES
    ]


@router.get("/{zone}/items", response_model=list[KBItem])
async def list_items(zone: str, limit: int = 20) -> list[KBItem]:
    s = get_stores()
    if zone not in ZONES:
        raise HTTPException(status_code=404, detail=f"unknown zone '{zone}'")
    return [_to_item(rec) for rec in s.zone(zone).all()[:limit]]


@router.get("/{zone}/search", response_model=list[SearchHit])
async def search(zone: str, q: str, top_k: int = 5) -> list[SearchHit]:
    if zone not in ZONES:
        raise HTTPException(status_code=404, detail=f"unknown zone '{zone}'")
    if not q.strip():
        return []
    hits = kb_query(query=q, zones=[zone], top_k=top_k)
    return [SearchHit(score=h.score, item=_to_item(h.record)) for h in hits]
