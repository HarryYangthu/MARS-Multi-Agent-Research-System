"""Frozen Discovery event names and machine-readable error codes."""
from __future__ import annotations

from enum import Enum


class DiscoveryEventName(str, Enum):
    RUN_CREATED = "discovery.run.created"
    RUN_STARTED = "discovery.run.started"
    RUN_PAUSED = "discovery.run.paused"
    RUN_RESUMED = "discovery.run.resumed"
    RUN_STOPPED = "discovery.run.stopped"
    ITERATION_STARTED = "discovery.iteration.started"
    ITERATION_COMPLETED = "discovery.iteration.completed"
    CANDIDATE_CREATED = "discovery.candidate.created"
    CANDIDATE_TRANSITIONED = "discovery.candidate.transitioned"
    CANDIDATE_EVALUATED = "discovery.candidate.evaluated"
    CANDIDATE_QUARANTINED = "discovery.candidate.quarantined"
    PROMOTION_ENQUEUED = "discovery.promotion.enqueued"
    PROMOTION_STARTED = "discovery.promotion.started"
    PROMOTION_COMPLETED = "discovery.promotion.completed"
    PROMOTION_FAILED = "discovery.promotion.failed"
    BUDGET_DEBITED = "discovery.budget.debited"
    ARCHIVE_UPDATED = "discovery.archive.updated"
    HITL_REQUESTED = "discovery.hitl.requested"
    HITL_RESOLVED = "discovery.hitl.resolved"


class DiscoveryErrorCode(str, Enum):
    INVALID_CONTRACT = "discovery.invalid_contract"
    INVALID_STATE = "discovery.invalid_state"
    IDEMPOTENCY_CONFLICT = "discovery.idempotency_conflict"
    BUDGET_EXHAUSTED = "discovery.budget_exhausted"
    PACK_NOT_FOUND = "discovery.pack_not_found"
    PACK_INCOMPATIBLE = "discovery.pack_incompatible"
    ADAPTER_NOT_FOUND = "discovery.adapter_not_found"
    ADAPTER_NOT_READY = "discovery.adapter_not_ready"
    PREFLIGHT_REJECTED = "discovery.preflight_rejected"
    EVALUATION_INVALID = "discovery.evaluation_invalid"
    CANDIDATE_QUARANTINED = "discovery.candidate_quarantined"
    CHECKPOINT_CORRUPT = "discovery.checkpoint_corrupt"


DISCOVERY_EVENT_SCHEMA = "discovery_event.v1"
