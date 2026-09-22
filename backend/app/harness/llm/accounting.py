"""Shared, durable pre-call reservations for a run and all inherited child tasks.

Unknown usage retains its reservation. A lost response never becomes zero cost.
Amounts are conservative bounds, not estimates of the provider's final invoice.
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Any
import uuid

import yaml
from filelock import BaseFileLock, FileLock, Timeout as FileLockTimeout

from app.harness.llm.provider_base import Completion, LLMConfig, LLMProvider, Message, MAX_LLM_RETRIES
from app.harness.persistence import atomic_write_json, path_lock
from app.settings import repo_root

_RUN_ROOT: ContextVar[Path | None] = ContextVar("mars_model_budget_root", default=None)


class ResourceBudgetError(RuntimeError):
    """No model request was sent because the run's hard limit was reached."""


class ModelConcurrencyBusy(ResourceBudgetError):
    pass


class ResourceReconciliationRequired(ResourceBudgetError):
    """A vanished owner left an unknown request; no automatic replay is allowed."""


def require_accounted_model_backend(backend: str) -> None:
    """External model processes cannot promise this host's per-call hard limits."""
    if _RUN_ROOT.get() is not None:
        raise ResourceBudgetError(f"{backend} has no enforceable shared model budget adapter; use native_llm for governed runs")


@contextmanager
def run_resource_scope(run_root: Path) -> Iterator[None]:
    # Nested Discovery/Agent work must share its ancestor's quota.
    token = _RUN_ROOT.set(_RUN_ROOT.get() or run_root.resolve())
    try:
        yield
    finally:
        _RUN_ROOT.reset(token)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _number(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        raise ValueError(f"invalid {name}")
    return result


@dataclass(frozen=True)
class Reservation:
    request_id: str
    tokens: int
    cost: float | None


class RunModelBudget:
    def __init__(self, root: Path, *, configuration: Mapping[str, Any] | None = None) -> None:
        self.root = root.resolve()
        self.path = self.root / "resources" / "model_budget.v1.json"
        self.lock_path = self.path.with_name("." + self.path.name + ".lock")
        if configuration is None:
            raw = yaml.safe_load((repo_root() / "configs/resources.yaml").read_text())
        else:
            raw = dict(configuration)
        if not isinstance(raw, dict) or raw.get("schema") != "runtime.resources.v1":
            raise ValueError("invalid resources configuration")
        limits = raw.get("limits", {})
        if not isinstance(limits, dict):
            raise ValueError("resource limits must be an object")
        for key in ("max_model_requests", "max_total_tokens", "max_parallel_model_calls"):
            if key == "max_model_requests" and key in limits and limits[key] is None:
                continue
            if type(limits.get(key)) is not int or limits[key] < 1:
                raise ValueError(f"{key} must be a positive integer")
        _number(limits.get("max_elapsed_seconds"), "max_elapsed_seconds", positive=True)
        if limits.get("max_cost") is not None:
            _number(limits["max_cost"], "max_cost", positive=True)
        if not isinstance(raw.get("prices", {}), dict):
            raise ValueError("prices must be an object")
        self.configuration = json.loads(_canonical(raw))
        self.configuration_hash = hashlib.sha256(_canonical(raw).encode()).hexdigest()
        self._leases: dict[str, BaseFileLock] = {}

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema": "runtime.model_budget.v1", "configuration": self.configuration,
                    "configuration_sha256": self.configuration_hash, "started_at": time.time(), "requests": {}}
        state: dict[str, Any] = json.loads(self.path.read_text())
        if (not isinstance(state, dict) or state.get("schema") != "runtime.model_budget.v1"
                or not isinstance(state.get("configuration"), dict)
                or not isinstance(state.get("requests"), dict)):
            raise ResourceBudgetError("invalid resource ledger; reconcile its persisted records")
        if hashlib.sha256(_canonical(state["configuration"]).encode()).hexdigest() != state.get("configuration_sha256"):
            raise ResourceBudgetError("resource ledger configuration is corrupt")
        if state.get("configuration_sha256") != self.configuration_hash:
            raise ResourceBudgetError("resource policy changed; reconcile the existing run before continuing")
        _number(state.get("started_at"), "resource ledger start time")
        for identifier, row in state["requests"].items():
            if (not isinstance(identifier, str) or not identifier.isalnum() or not isinstance(row, dict)
                    or row.get("status") not in {"in_flight", "completed", "failed", "cancelled",
                                                "reconciliation_required", "abandoned"}
                    or type(row.get("charged_tokens")) is not int or row["charged_tokens"] < 0):
                raise ResourceBudgetError("invalid resource reservation record")
            if row.get("charged_cost") is not None:
                _number(row["charged_cost"], "charged model cost")
        return state

    def _lease_path(self, request_id: str) -> Path:
        return self.path.parent / "requests" / f"{request_id}.lock"

    def _mark_abandoned(self, state: dict[str, Any]) -> bool:
        changed = False
        for identifier, row in state["requests"].items():
            if row["status"] != "in_flight":
                continue
            lease_path = self._lease_path(identifier)
            lease_path.parent.mkdir(parents=True, exist_ok=True)
            probe = FileLock(lease_path, timeout=0)
            try:
                probe.acquire()
            except FileLockTimeout:
                continue
            try:
                row.update(status="reconciliation_required", usage_complete=False,
                           owner_lost_at=time.time(), recovery_reason="owner lease is no longer held")
                changed = True
            finally:
                probe.release()
        return changed

    def recover_abandoned(self) -> tuple[str, ...]:
        """Expose orphaned requests without inventing usage or replaying them."""
        with path_lock(self.lock_path):
            state = self._read()
            if self._mark_abandoned(state):
                atomic_write_json(self.path, state)
            return tuple(key for key, row in state["requests"].items()
                         if row["status"] == "reconciliation_required")

    def reconcile_abandoned(self, request_id: str, *, actor: str, reason: str,
                            evidence_refs: tuple[str, ...]) -> None:
        """Acknowledge an unknown outcome; retain its full reserved token/cost.

        This operation never resends a request or declares provider success.
        Independent work may proceed afterwards within the remaining budget.
        """
        if not actor.strip() or not reason.strip() or not evidence_refs or any(not ref.strip() for ref in evidence_refs):
            raise ValueError("reconciliation requires actor, reason and evidence references")
        with path_lock(self.lock_path):
            state = self._read()
            self._mark_abandoned(state)
            row = state["requests"].get(request_id)
            if row is None or row["status"] not in {"reconciliation_required", "abandoned"}:
                raise ResourceBudgetError("only an abandoned reservation can be reconciled")
            receipt = {"actor": actor, "reason": reason, "evidence_refs": list(evidence_refs)}
            if row["status"] == "abandoned":
                if row.get("reconciliation") != receipt:
                    raise ResourceBudgetError("reservation reconciliation is immutable")
                return
            row.update(status="abandoned", reconciled_at=time.time(), reconciliation=receipt,
                       usage_complete=False)
            atomic_write_json(self.path, state)

    def reserve(self, messages: list[Message], config: LLMConfig,
                correlation: Mapping[str, Any]) -> Reservation:
        if type(config.max_tokens) is not int or config.max_tokens < 1:
            raise ValueError("max_tokens must bound a positive output length")
        attempts = min(max(config.max_retries, 0), MAX_LLM_RETRIES) + 1
        prompt_bound = len(_canonical({"messages": [m.to_wire() for m in messages],
                                      "tools": config.tools}).encode()) + 32 * (len(messages) + 1)
        reserved_tokens = (prompt_bound + config.max_tokens) * attempts
        price = self.configuration.get("prices", {}).get(config.provider + "/" + config.model)
        cost: float | None = None
        if price is not None:
            if not isinstance(price, dict):
                raise ValueError("model price must be an object")
            input_rate = _number(price.get("input_per_million"), "input price")
            output_rate = _number(price.get("output_per_million"), "output price")
            cost = (prompt_bound * input_rate + config.max_tokens * output_rate) * attempts / 1_000_000
        identifier = uuid.uuid4().hex
        with path_lock(self.lock_path):
            state = self._read()
            if self._mark_abandoned(state):
                atomic_write_json(self.path, state)
            unresolved = [key for key, row in state["requests"].items() if row["status"] == "reconciliation_required"]
            if unresolved:
                raise ResourceReconciliationRequired("unknown model requests require explicit reconciliation: " + ", ".join(unresolved))
            limits = state["configuration"]["limits"]
            if time.time() - state["started_at"] >= limits["max_elapsed_seconds"]:
                raise ResourceBudgetError("run elapsed-time budget exhausted")
            rows = list(state["requests"].values())
            if limits["max_model_requests"] is not None and len(rows) >= limits["max_model_requests"]:
                raise ResourceBudgetError("run model-request budget exhausted")
            if sum(row["charged_tokens"] for row in rows) + reserved_tokens > limits["max_total_tokens"]:
                raise ResourceBudgetError("run total-token reservation exceeds remaining budget")
            if limits.get("max_cost") is not None:
                if cost is None or any(row["charged_cost"] is None for row in rows):
                    raise ResourceBudgetError("monetary limit requires explicit prices for every selected model")
                if sum(row["charged_cost"] for row in rows) + cost > limits["max_cost"]:
                    raise ResourceBudgetError("run monetary reservation exceeds remaining budget")
            if sum(row["status"] == "in_flight" for row in rows) >= limits["max_parallel_model_calls"]:
                raise ModelConcurrencyBusy("run model concurrency slots are occupied")
            state["requests"][identifier] = {"status": "in_flight", "started_at": time.time(),
                "provider": config.provider, "model": config.model, "correlation": dict(correlation),
                "reserved_tokens": reserved_tokens, "charged_tokens": reserved_tokens,
                "reserved_cost": cost, "charged_cost": cost, "usage": None, "usage_complete": False,
                "price": price, "max_sdk_attempts": attempts}
            lease_path = self._lease_path(identifier)
            lease_path.parent.mkdir(parents=True, exist_ok=True)
            lease = FileLock(lease_path, timeout=0, thread_local=False)
            lease.acquire()
            try:
                atomic_write_json(self.path, state)
            except BaseException:
                lease.release()
                raise
            self._leases[identifier] = lease
        return Reservation(identifier, reserved_tokens, cost)

    def settle(self, reservation: Reservation, *, usage: Any, complete: bool,
               outcome: str) -> None:
        if outcome not in {"completed", "failed", "cancelled"}:
            raise ValueError("invalid model settlement outcome")
        with path_lock(self.lock_path):
            state = self._read()
            row = state["requests"][reservation.request_id]
            if row["status"] != "in_flight":
                raise ResourceBudgetError("model reservation already settled")
            if reservation.request_id not in self._leases:
                raise ResourceBudgetError("model reservation belongs to another owner; use explicit reconciliation after owner loss")
            valid = (isinstance(usage, dict)
                     and all(type(usage.get(k)) is int and usage[k] >= 0
                             for k in ("prompt_tokens", "completion_tokens", "total_tokens"))
                     and usage["total_tokens"] >= usage["prompt_tokens"] + usage["completion_tokens"])
            known = bool(complete and valid)
            row.update(status=outcome, finished_at=time.time(), usage=usage if valid else None,
                       usage_complete=known)
            if known:
                row["charged_tokens"] = usage["total_tokens"]
                if row["price"] is not None:
                    price = row["price"]
                    row["charged_cost"] = (usage["prompt_tokens"] * price["input_per_million"]
                        + usage["completion_tokens"] * price["output_per_million"]) / 1_000_000
            row["reservation_exceeded"] = known and row["charged_tokens"] > row["reserved_tokens"]
            atomic_write_json(self.path, state)
            lease = self._leases.pop(reservation.request_id, None)
            if lease is not None:
                lease.release()


async def guarded_complete(provider: LLMProvider, messages: list[Message], config: LLMConfig,
                           *, run_root: Path | None = None,
                           correlation: Mapping[str, Any] | None = None) -> Completion:
    root = _RUN_ROOT.get() or run_root
    if root is None:
        raise ResourceBudgetError("production model calls require a run resource scope")
    budget = RunModelBudget(root)
    while True:
        try:
            reservation = budget.reserve(messages, config, correlation or {})
            break
        except ModelConcurrencyBusy:
            await asyncio.sleep(0.05)
    observer = config.attempt_observer
    unknown_attempt = False

    def observe(kind: str, data: dict[str, Any]) -> None:
        nonlocal unknown_attempt
        if kind == "sdk_attempt_failed":
            unknown_attempt = True
        if observer is not None:
            observer(kind, data)

    # Config objects may be shared across concurrent roles; never mutate their observers.
    from dataclasses import replace
    actual = replace(config, attempt_observer=observe)
    try:
        result = await provider.complete(messages, actual)
    except BaseException as exc:
        budget.settle(reservation, usage=getattr(exc, "usage", None), complete=False,
                      outcome="cancelled" if isinstance(exc, asyncio.CancelledError) else "failed")
        raise
    budget.settle(reservation, usage=result.raw.get("usage"), complete=not unknown_attempt,
                  outcome="completed")
    return result
