"""Read-only contract resource summaries from one validated SQLite snapshot.

Quota charges are never converted into provider usage. Receipt validation
establishes persisted consistency, not an independent API/billing attestation.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
import sqlite3
from typing import Any

from app.bridge.research_run_service import load_run_research_contract
from app.harness.runtime.project_scope import safe_scope_path
from app.harness.runtime.research_budget_ledger import BudgetReservation, BudgetSettlement, ResearchBudgetLedger
from app.harness.runtime.state_journal import StateJournal
from app.storage.run_store import RunHandle


@dataclass(frozen=True)
class ResearchUsage:
    resources: dict[str, Any]
    limitations: tuple[str, ...]

    def evidence_bytes(self) -> bytes:
        """A derived public summary, never a copy of the private SQLite DB."""
        return _canonical(self.resources)


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _record(read: Callable[[str], bytes], relative: str) -> dict[str, Any]:
    value = json.loads(read(relative))
    if not isinstance(value, dict):
        raise ValueError("resource record is not an object")
    _canonical(value)
    return value


def _empty() -> dict[str, Any]:
    return {"status": "invalid", "authority": "sqlite", "model_requests": None, "logical_records": None,
        "request_count_scope": "logical_records", "observed_sdk_attempts": None,
        "observed_attempts_complete": None, "charged_sdk_attempts": None, "reserved_sdk_attempts": None,
        "calls_with_unknown_attempt_count": None, "input_tokens": None, "billed_output_tokens": None,
        "charged_input_tokens": None, "charged_billed_output_tokens": None,
        "cost": None, "currency": None, "cost_scope": "unverified_price_not_invoice", "usage_complete": None,
        "research_activity_seconds": None, "research_activity_remaining_seconds": None, "clock_uncertain": None}


def _verified_model_observation(run: RunHandle, task_hash: str, spec: BudgetReservation,
                                settlement: BudgetSettlement, read: Callable[[str], bytes]) -> tuple[int, int | None, int | None]:
    expected = "resources/contract_models/" + spec.reservation_id + ".json"
    if settlement.outcome != "success" or settlement.evidence_refs != (expected,):
        raise ValueError("model settlement has no supported receipt")
    safe_scope_path(run.root, expected, must_exist=True)
    receipt = _record(read, expected)
    identity = receipt.get("identity")
    response = receipt.get("response_identity")
    if (receipt.get("schema") != "runtime.contract_model_receipt.v1"
            or receipt.get("run_id") != run.run_id or receipt.get("task_sha256") != task_hash
            or receipt.get("reservation_id") != spec.reservation_id or receipt.get("outcome") != "completed"
            or not isinstance(identity, dict) or identity.get("task_sha256") != task_hash
            or _digest(identity) != spec.operation_fingerprint
            or _digest(receipt) != settlement.evidence_fingerprint
            or receipt.get("max_sdk_attempts") != spec.amounts.model_requests
            or receipt.get("attempts_complete") is not True
            or not isinstance(response, dict) or response.get("response_model_status") != "consistent"
            or response.get("model_matches_requested") is not True or response.get("provider_matches_requested") is not True
            or not isinstance(response.get("model"), str) or not isinstance(identity.get("model"), str)
            or response["model"].casefold() != identity["model"].casefold()
            or response.get("provider") != identity.get("provider")):
        raise ValueError("model observation identity or evidence differs")
    attempts = receipt.get("observed_sdk_attempts")
    if (type(attempts) is not int or not 0 < attempts <= spec.amounts.model_requests
            or settlement.actual.model_requests != attempts):
        raise ValueError("model attempt evidence differs from settlement")
    if receipt.get("usage_complete") is not True:
        return attempts, None, None
    usage = receipt.get("usage")
    if (not isinstance(usage, dict) or any(type(usage.get(key)) is not int or usage[key] < 0
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"))
            or usage["total_tokens"] < usage["prompt_tokens"] + usage["completion_tokens"]):
        raise ValueError("complete model usage is invalid")
    prompt, billed = usage["prompt_tokens"], usage["total_tokens"] - usage["prompt_tokens"]
    if settlement.actual.input_tokens != prompt or settlement.actual.billed_output_tokens != billed:
        raise ValueError("actual model usage differs from settlement")
    return attempts, prompt, billed


def read_research_resources(run: RunHandle, *, read: Callable[[str], bytes]) -> ResearchUsage | None:
    """None means verified absence of contract markers; only then allow legacy."""
    result = _empty()
    try:
        marked = run.meta.get("research_task_sha256") is not None
        for relative in ("run_meta.json", "input/run_request_options.v1.json", "input/research_task.v1.json"):
            path = safe_scope_path(run.root, relative)
            if path.exists():
                record = _record(read, relative)
                extra = record.get("extra")
                marked = marked or (relative == "input/research_task.v1.json"
                    or record.get("research_task_sha256") is not None
                    or isinstance(extra, dict) and extra.get("research_task_sha256") is not None)
        for relative in ("run_state.authority.json", "run_state.sqlite3"):
            path = safe_scope_path(run.root, relative)
            if path.exists() and relative == "run_state.authority.json":
                _record(read, relative)  # Bound the authority loader's JSON read.
        # A valid SQLite authority never depends on its regenerable JSON projection.
        if not any((run.root / name).exists() for name in ("run_state.authority.json", "run_state.sqlite3")):
            projection = safe_scope_path(run.root, "run_state.json")
            if projection.exists():
                _record(read, "run_state.json")
        journal = StateJournal.from_authority(run.root, run_id=run.run_id)
        if journal is None:
            if not marked:
                return None
            raise ValueError("contract resource authority is unavailable")
        with journal.connection() as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            state = StateJournal._read(connection)
            request = state.get("request")
            extra = request.get("extra") if isinstance(request, dict) else None
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            marked = marked or (isinstance(extra, dict) and extra.get("research_task_sha256") is not None
                                or bool(tables & {"research_budget", "research_operations", "research_reservations", "research_activity", "research_leases"}))
            if not marked:
                return None
            if not isinstance(extra, dict):
                raise ValueError("contract request binding is missing")
            frozen = load_run_research_contract(run, extra)
            if frozen is None:
                raise ValueError("contract binding is unavailable")
            if "research_budget" not in tables:
                raise ValueError("contract budget is not initialized")
            policy_row = connection.execute("SELECT policy FROM research_budget WHERE id=1").fetchone()
            if policy_row is None:
                raise ValueError("contract budget policy is missing")
            policy = json.loads(policy_row[0])
            ledger = ResearchBudgetLedger(journal, task_sha256=frozen.task_sha256, budget=frozen.task.budget,
                price_reference_sha256=policy.get("price_reference_sha256"))
            transaction = ledger.in_transaction(connection)
            snapshot = transaction.snapshot()
            # snapshot validates every specification/settlement/hash and all
            # interval identities. Read those same rows in the same DB view.
            rows = connection.execute("SELECT specification,state,settlement FROM research_reservations ORDER BY operation_id,attempt_index").fetchall()
            model_rows = 0
            attempts: list[int] = []
            inputs: list[int] = []
            outputs: list[int] = []
            reserved_attempts = 0
            invalid_receipts = 0
            for specification, phase, raw_settlement in rows:
                spec = BudgetReservation.model_validate_json(specification)
                if spec.kind != "model":
                    continue
                model_rows += 1
                reserved_attempts += spec.amounts.model_requests
                if phase != "settled" or raw_settlement is None:
                    continue
                try:
                    settlement = BudgetSettlement.model_validate_json(raw_settlement)
                    count, prompt, billed = _verified_model_observation(run, frozen.task_sha256, spec, settlement, read)
                    attempts.append(count)
                    if prompt is not None and billed is not None:
                        inputs.append(prompt)
                        outputs.append(billed)
                except (OSError, ValueError, TypeError, KeyError):
                    invalid_receipts += 1
            complete = len(inputs) == model_rows
            result.update(status="recorded", model_requests=model_rows, logical_records=model_rows,
                observed_sdk_attempts=sum(attempts) if len(attempts) == model_rows else None,
                observed_attempts_complete=len(attempts) == model_rows,
                charged_sdk_attempts=snapshot.used["model_requests"], reserved_sdk_attempts=reserved_attempts,
                calls_with_unknown_attempt_count=model_rows - len(attempts), usage_complete=complete,
                input_tokens=sum(inputs) if complete else None, billed_output_tokens=sum(outputs) if complete else None,
                charged_input_tokens=snapshot.used["input_tokens"], charged_billed_output_tokens=snapshot.used["billed_output_tokens"],
                verified_input_tokens_subtotal=sum(inputs), verified_billed_output_tokens_subtotal=sum(outputs),
                verified_usage_records=len(inputs), unverified_usage_records=model_rows - len(inputs),
                verified_sdk_attempts_subtotal=sum(attempts), invalid_model_receipts=invalid_receipts,
                charged_quantities={key: value for key, value in snapshot.used.items() if key != "model_cost_micro_cny"},
                limits={key: value for key, value in snapshot.limits.items() if key != "model_cost_micro_cny"},
                research_activity_seconds=snapshot.activity_us / 1_000_000,
                research_activity_remaining_seconds=snapshot.activity_remaining_us / 1_000_000,
                clock_uncertain=snapshot.clock_uncertain, reservation_overrun=snapshot.reservation_overrun,
                unknown_reservations=len(snapshot.unknown_reservations),
                open_activity_reservations=sum(phase in {"reserved", "unknown"} for _, phase, _ in rows),
                active_readers=snapshot.active_readers, active_jobs=snapshot.active_jobs, active_gpus=snapshot.active_gpus)
        limitations = ["合同资源来自同一 SQLite 只读快照；保守记账包括预留，不能当作模型实际消耗或研究完成证据。",
                       "未绑定可核验价目，模型费用保持未知；实际 usage 只汇总与 SQLite 结算及回执哈希一致的记录。"]
        if not complete:
            limitations.append("部分模型用量缺少完整可核验回执，实际 token 合计保持未知；已核验小计不代表全量。")
        if invalid_receipts:
            limitations.append("部分模型回执缺失或校验失败，未作为实际 SDK/token 用量展示。")
        if snapshot.clock_uncertain or snapshot.unknown_reservations:
            limitations.append("存在时钟或执行未知状态，活动时长/剩余额度采用账本保守记账，未执行恢复或退款。")
        return ResearchUsage(result, tuple(limitations))
    except (OSError, ValueError, TypeError, KeyError, AttributeError, sqlite3.Error):
        return ResearchUsage(_empty(), ("合同资源账本或冻结身份不可核验；未回退旧 JSON 账本，实际用量与费用保持未知。",))
