"""Synchronous memory consolidation helpers.

These functions are intentionally pure-ish and callable from scripts/tests. V2
can later schedule them in a background worker without changing semantics.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from loguru import logger

from app.harness.kb.config import lifecycle_config, mock_policy
from app.harness.kb.models import memory_from_kb_record
from app.harness.kb.stores import KBStores, QUARANTINE_ZONE, get_stores


@dataclass(frozen=True)
class ConsolidationReport:
    expired: int
    decayed: int
    prune_candidates: int
    mock_deleted: int


def consolidate(stores: KBStores | None = None) -> ConsolidationReport:
    s = stores or get_stores()
    cfg = lifecycle_config()
    half_life = float(cfg.get("decay_half_life_days", 90) or 90)
    prune_below = float(cfg.get("prune_below_salience", 0.15) or 0.15)
    mock_ttl = int(mock_policy().get("ttl_days", 3) or 3)
    expired = 0
    decayed = 0
    prune_candidates = 0
    mock_deleted = 0
    now = datetime.now(tz=timezone.utc)
    for zone_name in s.all_zones(include_quarantine=True):
        zone = s.zone(zone_name)
        for record in zone.all(exclude_mock=False, exclude_superseded=False):
            memory = memory_from_kb_record(
                record_id=record.id,
                zone=record.zone,
                text=record.text,
                metadata=record.metadata,
            )
            age_days = _age_days(memory.valid_from, now)
            ttl = mock_ttl if zone_name == QUARANTINE_ZONE or memory.is_mock else memory.ttl_days
            if ttl is not None and age_days > ttl:
                if zone_name == QUARANTINE_ZONE or memory.is_mock:
                    if zone.delete(record.id):
                        mock_deleted += 1
                    continue
                else:
                    zone.update_metadata(
                        record.id,
                        {
                            "expired": True,
                            "archived": True,
                            "archived_at": now.isoformat(),
                        },
                    )
                    expired += 1
            decay = math.pow(0.5, age_days / half_life) if half_life > 0 else 1.0
            new_confidence = max(0.0, min(memory.confidence, memory.confidence * decay))
            new_salience = max(0.0, min(memory.salience, memory.salience * decay))
            if new_confidence < memory.confidence or new_salience < memory.salience:
                zone.update_metadata(
                    record.id,
                    {
                        "confidence": round(new_confidence, 4),
                        "salience": round(new_salience, 4),
                    },
                )
                decayed += 1
            if new_salience < prune_below:
                zone.update_metadata(record.id, {"prune_candidate": True})
                prune_candidates += 1
    return ConsolidationReport(
        expired=expired,
        decayed=decayed,
        prune_candidates=prune_candidates,
        mock_deleted=mock_deleted,
    )


def _age_days(raw: str, now: datetime) -> float:
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return max(0.0, (now - dt).total_seconds() / 86400)


def main() -> int:
    report = consolidate()
    logger.info(
        "memory consolidation complete: expired={} decayed={} prune_candidates={} mock_deleted={}",
        report.expired,
        report.decayed,
        report.prune_candidates,
        report.mock_deleted,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
