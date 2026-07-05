"""MemoryRecord v2 model and legacy KB compatibility helpers."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, cast

MemoryType = Literal["semantic", "episodic", "procedural"]


def utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def summarize(text: str, *, limit: int = 700) -> str:
    cleaned = " ".join(text.strip().split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "..."


@dataclass(frozen=True)
class EvalStatus:
    passed: bool = False
    checks: dict[str, bool] = field(default_factory=dict)
    scorecard: str = ""
    decision: str = ""
    blocking: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": dict(self.checks),
            "scorecard": self.scorecard,
            "decision": self.decision,
            "blocking": self.blocking,
            "reason": self.reason,
        }

    @classmethod
    def from_raw(cls, raw: object) -> "EvalStatus":
        if not isinstance(raw, dict):
            return cls()
        checks_raw = raw.get("checks", {})
        checks = (
            {str(k): bool(v) for k, v in checks_raw.items()}
            if isinstance(checks_raw, dict)
            else {}
        )
        return cls(
            passed=bool(raw.get("passed", False)),
            checks=checks,
            scorecard=str(raw.get("scorecard", "") or ""),
            decision=str(raw.get("decision", "") or ""),
            blocking=bool(raw.get("blocking", False)),
            reason=str(raw.get("reason", "") or ""),
        )


@dataclass(frozen=True)
class MemoryRecord:
    record_id: str
    zone: str
    memory_type: MemoryType
    text: str
    summary: str
    source_path: str
    run_id: str
    agent: str
    schema: str
    content_hash: str
    is_mock: bool
    confidence: float
    eval_status: EvalStatus
    supersedes: tuple[str, ...] = ()
    superseded_by: str | None = None
    salience: float = 0.5
    valid_from: str = field(default_factory=utc_now)
    ttl_days: int | None = 180
    approved: bool = False

    def to_metadata(self) -> dict[str, Any]:
        return {
            "schema": "memory_record.v2",
            "record_id": self.record_id,
            "zone": self.zone,
            "memory_type": self.memory_type,
            "summary": self.summary,
            "source_path": self.source_path,
            "run_id": self.run_id,
            "agent": self.agent,
            "content_hash": self.content_hash,
            "is_mock": self.is_mock,
            "confidence": self.confidence,
            "eval_status": self.eval_status.to_dict(),
            "supersedes": list(self.supersedes),
            "superseded_by": self.superseded_by,
            "salience": self.salience,
            "valid_from": self.valid_from,
            "ttl_days": self.ttl_days,
            "approved": self.approved,
            "artifact_schema": self.schema,
        }

    @classmethod
    def create(
        cls,
        *,
        zone: str,
        text: str,
        memory_type: MemoryType,
        source_path: str = "",
        run_id: str = "",
        agent: str = "",
        schema: str = "",
        is_mock: bool = False,
        confidence: float = 0.8,
        eval_status: EvalStatus | None = None,
        salience: float = 0.5,
        ttl_days: int | None = 180,
        approved: bool = False,
        extra_id_seed: str = "",
    ) -> "MemoryRecord":
        digest = content_hash("|".join([zone, source_path, text, extra_id_seed]))
        return cls(
            record_id="mem_" + digest.split(":", 1)[1][:16],
            zone=zone,
            memory_type=memory_type,
            text=text,
            summary=summarize(text),
            source_path=source_path,
            run_id=run_id,
            agent=agent,
            schema=schema,
            content_hash=content_hash(text),
            is_mock=is_mock,
            confidence=max(0.0, min(1.0, confidence)),
            eval_status=eval_status or EvalStatus(),
            salience=max(0.0, min(1.0, salience)),
            ttl_days=ttl_days,
            approved=approved,
        )


def memory_from_kb_record(
    *,
    record_id: str,
    zone: str,
    text: str,
    metadata: dict[str, Any],
) -> MemoryRecord:
    """Convert legacy KB metadata to a MemoryRecord-compatible view."""
    if metadata.get("schema") == "memory_record.v2":
        raw_type = str(metadata.get("memory_type", "semantic"))
        memory_type: MemoryType = (
            cast(MemoryType, raw_type)
            if raw_type in {"semantic", "episodic", "procedural"}
            else "semantic"
        )
        supersedes_raw = metadata.get("supersedes", [])
        supersedes = (
            tuple(str(item) for item in supersedes_raw)
            if isinstance(supersedes_raw, list)
            else ()
        )
        ttl_raw = metadata.get("ttl_days", 180)
        ttl_days = int(ttl_raw) if ttl_raw is not None else None
        return MemoryRecord(
            record_id=str(metadata.get("record_id", record_id)),
            zone=str(metadata.get("zone", zone)),
            memory_type=memory_type,
            text=text,
            summary=str(metadata.get("summary", "") or summarize(text)),
            source_path=str(metadata.get("source_path", "") or ""),
            run_id=str(metadata.get("run_id", "") or ""),
            agent=str(metadata.get("agent", "") or ""),
            schema=str(metadata.get("artifact_schema", metadata.get("schema", "")) or ""),
            content_hash=str(metadata.get("content_hash", "") or content_hash(text)),
            is_mock=bool(metadata.get("is_mock", False)),
            confidence=float(metadata.get("confidence", 0.8) or 0.8),
            eval_status=EvalStatus.from_raw(metadata.get("eval_status")),
            supersedes=supersedes,
            superseded_by=(
                str(metadata.get("superseded_by"))
                if metadata.get("superseded_by") is not None
                else None
            ),
            salience=float(metadata.get("salience", 0.5) or 0.5),
            valid_from=str(metadata.get("valid_from", "") or utc_now()),
            ttl_days=ttl_days,
            approved=bool(metadata.get("approved", False)),
        )
    raw_kind = str(metadata.get("kind", "") or "")
    memory_type = infer_memory_type(zone=zone, kind=raw_kind)
    return MemoryRecord(
        record_id=record_id,
        zone=zone,
        memory_type=memory_type,
        text=text,
        summary=str(metadata.get("summary", "") or summarize(text)),
        source_path=str(metadata.get("source_path", "") or ""),
        run_id=str(metadata.get("run_id", "") or ""),
        agent=str(metadata.get("agent", "") or ""),
        schema=str(metadata.get("schema", "") or ""),
        content_hash=str(metadata.get("content_hash", "") or content_hash(text)),
        is_mock=bool(metadata.get("is_mock", False)),
        confidence=float(metadata.get("confidence", 0.45) or 0.45),
        eval_status=EvalStatus.from_raw(metadata.get("eval_status")),
        salience=float(metadata.get("salience", 0.5) or 0.5),
        valid_from=str(metadata.get("created", "") or metadata.get("valid_from", "") or utc_now()),
        ttl_days=None if zone == "literature" else 180,
        approved=bool(metadata.get("approved", True)),
    )


def infer_memory_type(*, zone: str, kind: str = "") -> MemoryType:
    lowered = f"{zone}:{kind}".lower()
    if "code" in lowered or "method" in lowered or "spec" in lowered or "prompt" in lowered:
        return "procedural"
    if "run" in lowered or "episode" in lowered or "example" in lowered or "report" in lowered:
        return "episodic"
    return "semantic"
