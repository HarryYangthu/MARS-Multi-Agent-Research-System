"""Frozen-contract model reservations in the shared StateJournal authority.

Identical calls never automatically replay, even after an owner disappears.
No prompt, completion or private reasoning is persisted by this adapter.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
import hashlib
import json
import math
import re
from typing import Any

from app.harness.llm.accounting import Reservation, ResourceBudgetError
from app.harness.llm.provider_base import Completion, LLMConfig, Message, MAX_LLM_RETRIES
from app.harness.persistence import atomic_write_json
from app.harness.runtime.research_budget_ledger import BudgetAmounts, BudgetReservation, BudgetSettlement
from app.harness.runtime.research_execution_scope import ResearchExecutionScope


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode()).hexdigest()


class ContractModelIdentityError(ResourceBudgetError):
    def __init__(self, usage: object) -> None:
        self.usage = usage
        super().__init__("provider response model identity mismatch")


def bounded_contract_config(scope: ResearchExecutionScope, config: LLMConfig) -> LLMConfig:
    if type(config.max_retries) is not int or config.max_retries < 0:
        raise ResourceBudgetError("max_retries must be a nonnegative integer")
    if type(config.max_tokens) is not int or config.max_tokens < 1:
        raise ResourceBudgetError("max_tokens must be a positive integer")
    if (not math.isfinite(config.request_timeout_seconds) or config.request_timeout_seconds <= 0
            or not math.isfinite(config.retry_base_delay_seconds) or config.retry_base_delay_seconds < 0):
        raise ResourceBudgetError("model timeout and backoff must be finite and bounded")
    return replace(config, max_retries=min(config.max_retries, MAX_LLM_RETRIES,
                                         scope.ledger.budget.operation_retries))


class ContractModelBudget:
    def __init__(self, scope: ResearchExecutionScope, *, endpoint: str | None) -> None:
        self.scope = scope
        # Even a nominally public URL may contain credentials in its path or
        # custom query names. Persist only its identity digest in model receipts.
        self.endpoint = _digest(endpoint) if endpoint is not None else None
        self.specifications: dict[str, BudgetReservation] = {}
        self.identities: dict[str, dict[str, object]] = {}
        self.errors: dict[str, tuple[str, int]] = {}
        self.responses: dict[str, dict[str, object]] = {}

    def observe(self, reservation: Reservation, kind: str, data: dict[str, Any]) -> None:
        if kind == "sdk_attempt_started":
            if self.scope.ledger.snapshot().activity_remaining_us <= 0:
                raise ResourceBudgetError("research activity budget exhausted before SDK attempt")
            if self.errors.get(reservation.request_id, ("", 0))[1] >= self.scope.ledger.budget.repeated_error_limit:
                raise ResourceBudgetError("identical SDK errors reached the frozen repeated_error_limit")
        elif kind == "sdk_attempt_failed":
            details = data.get("details")
            details = details if isinstance(details, dict) else {}
            digest = _digest({"error": data.get("error"), "http_status": details.get("http_status"),
                              "api_error_code": details.get("api_error_code")})
            old, count = self.errors.get(reservation.request_id, ("", 0))
            self.errors[reservation.request_id] = (digest, count + 1 if old == digest else 1)

    def record_response_identity(self, reservation: Reservation, completion: Completion) -> None:
        expected = self.identities[reservation.request_id]
        record: dict[str, object] = {"provider": completion.provider, "model": completion.model,
            "model_matches_requested": completion.model.casefold() == str(expected["model"]).casefold(),
            "provider_matches_requested": completion.provider == expected["provider"],
            "response_model_status": completion.raw.get("response_model_status")}
        self.responses[reservation.request_id] = record
        if (completion.is_mock or not record["model_matches_requested"] or not record["provider_matches_requested"]
                or record["response_model_status"] != "consistent"):
            raise ContractModelIdentityError(completion.raw.get("usage"))

    def record_failure_identity(self, reservation: Reservation, identity: object) -> None:
        if not isinstance(identity, dict) or reservation.request_id in self.responses:
            return
        models = identity.get("response_models")
        status = identity.get("response_model_status")
        if (not isinstance(models, list) or len(models) > 8
                or any(not isinstance(model, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,255}", model) is None
                       for model in models)
                or status not in {"consistent", "inconsistent", "invalid", "missing", "partial"}):
            return
        self.responses[reservation.request_id] = {"response_models": models, "response_model_status": status}

    def reserve(self, messages: list[Message], config: LLMConfig,
                correlation: Mapping[str, Any]) -> Reservation:
        # Correlation is diagnostic input, never authority for coding privileges.
        del correlation
        if self.scope.ledger.price_reference_sha256 is not None:
            raise ResourceBudgetError("priced contract model execution requires a verified price adapter")
        if bounded_contract_config(self.scope, config).max_retries != config.max_retries:
            raise ResourceBudgetError("SDK retries exceed the frozen operation limit")
        attempts = config.max_retries + 1
        request = {"messages": [message.to_wire() for message in messages], "tools": config.tools}
        prompt_bound = len(_canonical(request).encode()) + 32 * (len(messages) + 1)
        identity: dict[str, object] = {
            "task_sha256": self.scope.ledger.task_sha256, "stage": self.scope.stage,
            "provider": config.provider, "model": config.model, "endpoint_sha256": self.endpoint,
            "request_sha256": _digest(request), "max_tokens": config.max_tokens,
            "temperature": config.temperature, "top_p": config.top_p,
            "thinking_enabled": config.thinking_enabled, "reasoning_effort": config.reasoning_effort,
            "json_mode": config.json_mode, "response_schema": config.response_schema,
        }
        fingerprint = _digest(identity)
        identifier = "model-" + fingerprint.removeprefix("sha256:")
        spec = BudgetReservation(reservation_id=identifier, operation_id=identifier,
            operation_fingerprint=fingerprint, kind="model",
            amounts=BudgetAmounts(model_requests=attempts, input_tokens=prompt_bound * attempts,
                billed_output_tokens=config.max_tokens * attempts),
            request_input_tokens=prompt_bound, request_output_tokens=config.max_tokens,
            output_class="coding" if self.scope.stage == "coding" else "ordinary")
        admission = self.scope.ledger.reserve(spec)
        if not admission.admitted:
            raise ResourceBudgetError("contract model request refused: " + str(admission.reason))
        if admission.replay:
            raise ResourceBudgetError("identical model operation already reserved; reconcile its receipt instead of replaying")
        self.specifications[identifier] = spec
        self.identities[identifier] = identity
        return Reservation(identifier, spec.amounts.input_tokens + spec.amounts.billed_output_tokens, None, attempts)

    def settle(self, reservation: Reservation, *, usage: Any, complete: bool,
               outcome: str, sdk_attempts: int | None = None, attempts_complete: bool = False) -> None:
        spec = self.specifications[reservation.request_id]
        if outcome not in {"completed", "failed", "cancelled"}:
            raise ResourceBudgetError("invalid contract model outcome")
        attempts_known = (attempts_complete and type(sdk_attempts) is int
                          and 0 < sdk_attempts <= reservation.attempts)
        valid = (isinstance(usage, dict)
            and all(type(usage.get(key)) is int and usage[key] >= 0
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens"))
            and usage["total_tokens"] >= usage["prompt_tokens"] + usage["completion_tokens"])
        usage_known = bool(valid and complete and attempts_known and outcome == "completed")
        observed_usage = ({key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
                          if valid else None)
        receipt: dict[str, object] = {"schema": "runtime.contract_model_receipt.v1",
            "token_usage_mode": self.scope.ledger.token_mode,
            "run_id": self.scope.ledger.journal.run_id, "task_sha256": self.scope.ledger.task_sha256,
            "invocation_id": self.scope.invocation_id, "reservation_id": reservation.request_id,
            "identity": self.identities[reservation.request_id], "outcome": outcome,
            "usage": observed_usage, "usage_complete": usage_known,
            "observed_sdk_attempts": sdk_attempts, "attempts_complete": attempts_known,
            "max_sdk_attempts": reservation.attempts, "cost_cny": None,
            "response_identity": self.responses.get(reservation.request_id),
            "last_error_fingerprint": self.errors.get(reservation.request_id, (None, 0))[0]}
        parent = self.scope.root / "resources/contract_models"
        if parent.is_symlink() or parent.parent.is_symlink():
            raise ResourceBudgetError("model receipt directory cannot be a symlink")
        path = parent / (reservation.request_id + ".json")
        if path.exists() or path.is_symlink():
            raise ResourceBudgetError("model receipt already exists; refusing to replace evidence")
        atomic_write_json(path, receipt)
        if outcome != "completed" or not attempts_known:
            self.scope.ledger.mark_unknown(reservation.request_id,
                reason="provider outcome or SDK attempt completion requires reconciliation",
                observed_lower_bound=BudgetAmounts(model_requests=max(1, sdk_attempts or 0),
                    input_tokens=usage["prompt_tokens"] if valid else 0,
                    billed_output_tokens=usage["total_tokens"] - usage["prompt_tokens"] if valid else 0))
            return
        observed_input = usage["prompt_tokens"] if valid else 0
        observed_output = usage["total_tokens"] - usage["prompt_tokens"] if valid else 0
        amounts = BudgetAmounts(model_requests=int(sdk_attempts or reservation.attempts),
            input_tokens=observed_input if usage_known else max(spec.amounts.input_tokens, observed_input),
            billed_output_tokens=observed_output if usage_known
                else max(spec.amounts.billed_output_tokens, observed_output))
        self.scope.ledger.settle(reservation.request_id, BudgetSettlement(actual=amounts,
            outcome="success", evidence_refs=(path.relative_to(self.scope.root).as_posix(),),
            evidence_fingerprint=_digest(receipt)))
