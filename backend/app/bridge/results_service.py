"""Read-only, domain-neutral results backed by existing execution evidence.

Receipt verification establishes persisted identity/hash consistency, not a
scientific result, a signature from a trusted machine, or an independent rerun.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import re
import stat
import statistics
from typing import Any

import yaml

from app.harness.llm.accounting import (
    ResourceBudgetError, charged_model_attempts, charged_token_component,
    reserved_model_attempts, validate_token_components,
)
from app.harness.runtime.state_journal import StateJournal
from app.harness.schema.validator import validate_document
from app.settings import repo_root
from app.storage.run_state_store import RunStateStore
from app.storage.run_store import RunHandle


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def public_text(value: Any) -> str:
    """Project fields are data; never publish host paths or credential values."""
    text = str(value)
    for name, secret in os.environ.items():
        if len(secret) >= 8 and re.search(r"key|token|password|secret|credential", name, re.I):
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|\Z)", "[REDACTED PRIVATE KEY]", text, flags=re.S)
    text = re.sub(r"(?i)(?:https?|file|ssh)://[^\s<>\"']+", "[external reference]", text)
    text = re.sub(r"(?i)(?:authorization|api[_-]?key|private[_-]?key|password|passwd|secret|(?:session[_-]?)?token)[\"']?\s*[:=]\s*[^\n,;]+", "[REDACTED]", text)
    text = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{12,}|Bearer\s+\S+)", "[REDACTED]", text, flags=re.I)
    text = re.sub(r"(?<![\w])(?:[A-Za-z]:[\\/]|\\\\|/)[^\s<>\"'`,;|]*", "[host path]", text)
    return "".join(char for char in text if char in "\n\t" or ord(char) >= 32 and ord(char) != 127)


def finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except (OverflowError, ValueError):
        return None


def load_results_policy() -> dict[str, int]:
    raw = yaml.safe_load((repo_root() / "configs/results.yaml").read_text(encoding="utf-8"))
    names = {"max_input_file_bytes", "max_evidence_file_bytes", "max_total_read_bytes", "max_jobs", "max_metrics",
             "max_curve_points", "max_export_bytes"}
    if (not isinstance(raw, dict) or raw.get("schema_id") != "results_policy.v1"
            or set(raw) != names | {"schema_id"} or any(type(raw.get(name)) is not int or raw[name] <= 0 for name in names)):
        raise ValueError("Invalid results policy")
    return {name: raw[name] for name in names}


def safe_path(root: Path, relative: str) -> Path:
    path = Path(relative)
    if (not relative or path.is_absolute() or "\\" in relative or ":" in relative
            or any(part in {"", ".", ".."} for part in relative.split("/"))):
        raise ValueError("Unsafe evidence reference")
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Unsafe run root")
    base = root.resolve(strict=True)
    target = base
    for part in path.parts:
        target = target / part
        if target.is_symlink():
            raise ValueError("Symlink evidence is not admitted")
    if not target.resolve().is_relative_to(base):
        raise ValueError("Evidence escapes its run")
    return target


class ResultReader:
    def __init__(self, run: RunHandle, policy: dict[str, int]) -> None:
        self.run = run
        self.policy = policy
        self.used_bytes = 0
        self.sources: list[dict[str, Any]] = []
        self.limitations: list[str] = []

    def read(self, relative: str, *, evidence: bool = False) -> bytes:
        path = safe_path(self.run.root, relative)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            limit = self.policy["max_evidence_file_bytes" if evidence else "max_input_file_bytes"]
            if (not stat.S_ISREG(info.st_mode) or info.st_size > limit
                    or self.used_bytes + info.st_size > self.policy["max_total_read_bytes"]):
                raise ValueError("Evidence exceeds the read policy")
            data = stream.read(limit + 1)
            if len(data) > limit or self.used_bytes + len(data) > self.policy["max_total_read_bytes"]:
                raise ValueError("Evidence exceeds the read policy")
            self.used_bytes += len(data)
            return data

    def record(self, relative: str) -> tuple[dict[str, Any], bytes]:
        data = self.read(relative)
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError("Evidence must be a JSON object")
        canonical(value)  # Reject NaN, Infinity and overflowed exponents.
        return value, data

    def source(self, kind: str, data: bytes) -> str:
        identifier = f"source_{len(self.sources) + 1:04d}"
        self.sources.append({"id": identifier, "kind": kind, "sha256": sha256(data), "bytes": len(data)})
        return identifier

    def state(self) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
        result: dict[str, Any] = {"status": "unknown", "authority": "missing", "revision": None,
                                  "read_only": True, "updated_at": None}
        for name in ("run_state.authority.json", "run_state.sqlite3", "run_state.json"):
            try:
                safe_path(self.run.root, name)
            except ValueError:
                result["authority"] = "invalid"
                self.limitations.append("状态来源包含不安全路径，未读取或回退。")
                return result, {}, []
        try:
            for name in ("run_state.authority.json", "run_state.json"):
                if (self.run.root / name).exists():
                    self.read(name)  # Bound authority metadata before the journal parser reads it.
            database = self.run.root / "run_state.sqlite3"
            if database.exists() and database.stat().st_size > self.policy["max_total_read_bytes"]:
                raise ValueError("State journal exceeds the results read policy")
            journal = StateJournal.from_authority(self.run.root, run_id=self.run.run_id)
            if journal is not None:
                raw = journal.read()
                # Pure validation: load() additionally recovers artifact transactions.
                snapshot = RunStateStore(self.run)._snapshot(raw)
                source_id = self.source("committed_state_payload", canonical(raw))
                result.update(status=public_text(snapshot.status), authority="sqlite", revision=snapshot.revision,
                              read_only=True, updated_at=public_text(snapshot.updated_at), source_id=source_id,
                              read_only_reason="results_view_is_not_execution_admission")
                events = [row for row in journal.all_events() if row["revision"] <= snapshot.revision]
                node_states = {"pending", "running", "waiting_review", "approved", "done", "failed", "skipped"}
                history = [{"node": public_text(row["agent"]),
                            "from_state": row.get("from_state") if isinstance(row.get("from_state"), str) and row["from_state"] in node_states else "unknown",
                            "to_state": row.get("to_state") if isinstance(row.get("to_state"), str) and row["to_state"] in node_states else "unknown",
                            "revision": row["revision"]} for row in events]
                return result, raw, history
            legacy = self.run.root / "run_state.json"
            if legacy.exists():
                raw, data = self.record("run_state.json")
                snapshot = RunStateStore(self.run)._snapshot(raw, legacy=True)
                result.update(status=public_text(snapshot.status), authority="legacy_json", updated_at=public_text(snapshot.updated_at),
                              source_id=self.source("legacy_state_unmigrated", data))
                self.limitations.append("旧 JSON 状态只读展示，尚未迁入权威状态；没有自动恢复或执行。")
                return result, raw, []
            self.limitations.append("没有可信执行状态，不能由产物文件推定流程完成。")
        except (OSError, ValueError, TypeError, KeyError):
            result.update(status="unknown", authority="invalid")
            self.limitations.append("权威状态不可用或校验失败；没有回退为成功状态。")
        return result, {}, []

    def jobs(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        experiments: list[dict[str, Any]] = []
        metrics: list[dict[str, Any]] = []
        curves: list[dict[str, Any]] = []
        directory = safe_path(self.run.root, "execution/local_commands")
        receipts = list(itertools.islice(directory.glob("*/*/execution_receipt.json"), self.policy["max_jobs"] + 1))
        if len(receipts) > self.policy["max_jobs"]:
            self.limitations.append("作业数量超过读取上限，结果不完整。")
        for path in sorted(receipts[:self.policy["max_jobs"]]):
            relative = path.relative_to(self.run.root.resolve()).as_posix()
            parent = path.parent.relative_to(self.run.root.resolve()).as_posix()
            experiment: dict[str, Any] = {"experiment_id": "unknown", "job_id": None, "role": "unknown",
                "status": "unknown", "verification": "invalid", "duration_seconds": None, "seed": None, "source_id": None}
            try:
                receipt, raw = self.record(relative)
                source_id = self.source("local_command_receipt", raw)
                experiment["source_id"] = source_id
                request, request_data = self.record(parent + "/job.json")
                identifiers = ("run_id", "experiment_id", "invocation_id")
                if (receipt.get("schema") != "local_command_receipt.v1" or request.get("schema") != "local_command_request.v1"
                        or receipt.get("run_id") != self.run.run_id or request.get("project") != self.run.project
                        or any(not isinstance(receipt.get(key), str) or not receipt[key] or receipt[key] != request.get(key) for key in identifiers)
                        or re.fullmatch(r"[a-f0-9]{32}", receipt["invocation_id"]) is None
                        or path.parent.name != receipt["invocation_id"]
                        or receipt.get("request_sha256") != "sha256:" + sha256(request_data)):
                    raise ValueError("Receipt identity mismatch")
                config = request.get("config", {})
                if not isinstance(config, dict):
                    raise ValueError("Invalid request configuration")
                if receipt.get("status") not in {"completed", "failed", "cancelled"}:
                    raise ValueError("Unknown receipt state")
                role = config.get("role") if config.get("role") in {"baseline", "candidate", "ablation"} else "unknown"
                experiment.update(experiment_id=public_text(request["experiment_id"]), job_id=public_text(request["invocation_id"]),
                    role=role, status=receipt.get("status", "unknown"), duration_seconds=finite_number(receipt.get("duration_seconds")),
                    seed=request.get("seed") if type(request.get("seed")) is int else None,
                    steps=request.get("steps") if type(request.get("steps")) is int and request["steps"] > 0 else None,
                    configuration_sha256=sha256(canonical(config)),
                    execution_backend="local_process" if receipt.get("execution_backend") == "local_process" else "unknown",
                    os_isolated=receipt.get("os_isolated") if type(receipt.get("os_isolated")) is bool else None)
                if receipt["status"] != "completed":
                    experiment["verification"] = "unverified"
                    experiments.append(experiment)
                    continue
                result, result_data = self.record(parent + "/result.json")
                if (receipt.get("returncode") != 0 or type(receipt.get("returncode")) is not int
                        or receipt.get("result_sha256") != "sha256:" + sha256(result_data)
                        or result.get("schema") != "local_command_result.v1" or result.get("status") != "completed"
                        or any(result.get(key) != request[key] for key in identifiers)):
                    raise ValueError("Result identity or checksum mismatch")
                measurements = result.get("metrics")
                if (not isinstance(measurements, dict) or not measurements
                        or len(measurements) + len(metrics) > self.policy["max_metrics"]
                        or any(not isinstance(name, str) or not name or finite_number(value) is None for name, value in measurements.items())
                        or not set(measurements) - {"returncode", "dry_run", "max_iters"}):
                    raise ValueError("Missing finite research measurements")
                evidence = receipt.get("evidence")
                paths = result.get("evidence_paths")
                if (not isinstance(evidence, list) or not evidence or not isinstance(paths, list) or not paths
                        or len(paths) != len(set(paths)) or len(paths) != len(evidence)):
                    raise ValueError("Missing measurement evidence")
                expected: dict[str, dict[str, Any]] = {}
                for item in evidence:
                    if not isinstance(item, dict) or item.get("kind") != "measurement_evidence":
                        raise ValueError("Invalid measurement evidence")
                    declared = Path(str(item.get("path", "")))
                    # Legacy host receipts use absolute paths. Only the exact
                    # original run location is accepted, never followed outside.
                    relative_evidence = declared.relative_to(self.run.root.resolve()).as_posix() if declared.is_absolute() else parent + "/" + str(declared)
                    if not relative_evidence.startswith(parent + "/") or relative_evidence in expected:
                        raise ValueError("Evidence belongs to another attempt")
                    expected[relative_evidence] = item
                for name in paths:
                    if not isinstance(name, str):
                        raise ValueError("Invalid measurement path")
                    admitted = parent + "/" + name
                    if Path(name).name in {"result.json", "job.json", "execution_receipt.json", "stdout.log", "stderr.log"}:
                        raise ValueError("Metadata is not measurement evidence")
                    data = self.read(admitted, evidence=True)
                    item = expected.get(admitted, {})
                    if item.get("sha256") != "sha256:" + sha256(data) or type(item.get("bytes")) is not int or item["bytes"] != len(data):
                        raise ValueError("Measurement evidence checksum mismatch")
                    self.source("measurement_file_not_exported", data)
                self.source("local_command_request", request_data)
                self.source("local_command_result", result_data)
                experiment["verification"] = "verified_local_receipt"
                experiment["comparison_group"] = sha256(canonical({"experiment_id": request["experiment_id"], "config": config, "steps": request.get("steps"),
                    "command_hashes": [item.get("sha256") for item in receipt.get("command_files", []) if isinstance(item, dict)]}))
                for name, value in measurements.items():
                    metrics.append({"experiment_id": experiment["experiment_id"], "job_id": experiment["job_id"],
                        "name": public_text(name), "value": finite_number(value), "unit": None, "direction": None,
                        "role": role, "verification": "verified_local_receipt", "source_id": source_id})
                points = result.get("loss_curve", [])
                if isinstance(points, list) and points:
                    if len(points) <= self.policy["max_curve_points"] and all(finite_number(point) is not None for point in points):
                        curves.append({"experiment_id": experiment["experiment_id"], "job_id": experiment["job_id"],
                            "metric": "loss", "points": points, "source_id": source_id})
                    else:
                        self.limitations.append("一条曲线存在非法点或超过展示上限，未生成图表。")
            except (OSError, ValueError, TypeError, KeyError):
                self.limitations.append("一项本地作业的身份、回执或测量文件未通过校验，其数值不纳入已验证结果。")
            experiments.append(experiment)
        return experiments, metrics, curves

    def unverified_metrics(self, verified: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not (self.run.root / "execution/metrics.json").exists():
            return []
        try:
            data = self.read("execution/metrics.json")
            rows = json.loads(data)
            if not isinstance(rows, list):
                raise ValueError("Invalid metrics projection")
            source_id = self.source("metrics_projection", data)
            known = {(row["experiment_id"], row["name"], row["value"]) for row in verified}
            result: list[dict[str, Any]] = []
            for row in rows[:self.policy["max_jobs"]]:
                if not isinstance(row, dict):
                    continue
                experiment = str(row.get("experiment_id") or row.get("run_id") or "unknown")
                prefix = self.run.run_id + "_"
                experiment = experiment[len(prefix):] if experiment.startswith(prefix) else experiment
                values = row.get("metrics", {})
                if not isinstance(values, dict):
                    continue
                for name, value in values.items():
                    if len(result) + len(verified) >= self.policy["max_metrics"]:
                        raise ValueError("Metrics exceed the report policy")
                    identifier, metric = public_text(experiment), public_text(name)
                    number = finite_number(value)
                    if (identifier, metric, number) in known:
                        continue
                    result.append({"experiment_id": identifier, "job_id": None, "name": metric, "value": number,
                        "unit": None, "direction": None, "role": "unknown", "verification": "unverified", "source_id": source_id})
            if result:
                self.limitations.append("汇总 metrics 中有缺少已核验作业回执的数值，仅作为未验证记录展示。")
            return result
        except (OSError, ValueError, TypeError):
            self.limitations.append("metrics 汇总文件不可读或格式无效。")
            return []

    def resources(self) -> dict[str, Any]:
        result: dict[str, Any] = {"status": "missing", "model_requests": None, "input_tokens": None,
            "billed_output_tokens": None, "cost": None, "currency": None, "usage_complete": None,
            "logical_records": None, "observed_sdk_attempts": None, "observed_attempts_complete": None,
            "charged_sdk_attempts": None, "reserved_sdk_attempts": None, "calls_with_unknown_attempt_count": None,
            "request_count_scope": "logical_records", "cost_scope": "recorded_estimate_not_invoice"}
        if not (self.run.root / "resources/model_budget.v1.json").exists():
            return result
        try:
            ledger, data = self.record("resources/model_budget.v1.json")
            configuration = ledger.get("configuration")
            requests = ledger.get("requests")
            if (ledger.get("schema") != "runtime.model_budget.v1" or not isinstance(configuration, dict)
                    or sha256(canonical(configuration)) != ledger.get("configuration_sha256") or not isinstance(requests, dict)):
                raise ValueError("Invalid ledger")
            inputs: list[int] = []
            outputs: list[int] = []
            observed: list[int] = []
            unknown_attempts = 0
            for identifier, row in requests.items():
                if (not isinstance(row, dict) or row.get("status") not in {"in_flight", "completed", "failed", "cancelled", "reconciliation_required", "abandoned"}
                        or not isinstance(identifier, str) or not identifier.isalnum()
                        or type(row.get("charged_tokens")) is not int or row["charged_tokens"] < 0):
                    raise ValueError("Invalid request")
                validate_token_components(row)
                charged = charged_model_attempts(row)
                count = row.get("observed_attempts")
                if count is not None and (type(count) is not int or not 0 <= count <= reserved_model_attempts(row)):
                    raise ValueError("Invalid observed model attempts")
                if row.get("attempts_complete") is True:
                    if type(count) is not int or count <= 0 or charged != count:
                        raise ValueError("Inconsistent observed model attempts")
                else:
                    unknown_attempts += 1
                if type(count) is int:
                    observed.append(count)
                if row.get("charged_cost") is not None and (finite_number(row["charged_cost"]) is None or row["charged_cost"] < 0):
                    raise ValueError("Invalid recorded model cost")
                usage = row.get("usage")
                if row.get("usage_complete") is True:
                    if (not isinstance(usage, dict)
                            or not all(type(usage.get(key)) is int and usage[key] >= 0 for key in ("prompt_tokens", "completion_tokens", "total_tokens"))
                            or usage["total_tokens"] < usage["prompt_tokens"] + usage["completion_tokens"]
                            or row["charged_tokens"] != usage["total_tokens"]):
                        raise ValueError("Invalid complete token usage")
                    # The accounting policy charges unexplained total-token
                    # remainder as output. Legacy complete rows can establish
                    # actual components from usage, never from quota fallback.
                    actual_input = usage["prompt_tokens"]
                    actual_output = usage["total_tokens"] - actual_input
                    if "charged_input_tokens" in row and (
                            charged_token_component(row, "input") != actual_input
                            or charged_token_component(row, "output") != actual_output):
                        raise ValueError("Token usage and settlement disagree")
                    inputs.append(actual_input)
                    outputs.append(actual_output)
            complete = len(inputs) == len(requests)
            result.update(status="recorded", model_requests=len(requests), logical_records=len(requests), usage_complete=complete,
                          observed_sdk_attempts=sum(observed) if len(observed) == len(requests) else None,
                          observed_attempts_complete=unknown_attempts == 0,
                          charged_sdk_attempts=sum(charged_model_attempts(row) for row in requests.values()),
                          reserved_sdk_attempts=sum(reserved_model_attempts(row) for row in requests.values()),
                          calls_with_unknown_attempt_count=unknown_attempts,
                          source_id=self.source("model_resource_ledger", data), currency=public_text(configuration.get("currency", "unknown")))
            if complete:
                result.update(input_tokens=sum(inputs), billed_output_tokens=sum(outputs))
                costs = [finite_number(row.get("charged_cost")) for row in requests.values()]
                if all(cost is not None and cost >= 0 for cost in costs):
                    result["cost"] = finite_number(sum(cost for cost in costs if cost is not None))
            else:
                self.limitations.append("模型账本包含未知用量，完整 token 与费用合计保持未知；预留额度不冒充实际消耗。")
        except (OSError, ValueError, TypeError, KeyError, ResourceBudgetError):
            result["status"] = "invalid"
            self.limitations.append("模型资源账本校验失败，未展示可能误导的费用或 token 合计。")
        return result


def _statistics(experiments: list[dict[str, Any]], metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    jobs = {row["job_id"]: row for row in experiments if row["verification"] == "verified_local_receipt"}
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in metrics:
        if row["verification"] == "verified_local_receipt":
            groups[(row["experiment_id"], row["name"], jobs[row["job_id"]]["comparison_group"])].append(row)
    summaries = []
    for (experiment, name, _), rows in groups.items():
        values = [row["value"] for row in rows]
        seeds = [jobs[row["job_id"]]["seed"] for row in rows]
        independent = len(rows) > 1 and all(seed is not None for seed in seeds) and len(set(seeds)) == len(seeds)
        try:
            deviation = finite_number(statistics.stdev(values)) if independent else None
        except (ValueError, OverflowError):
            deviation = None
        summaries.append({"experiment_id": experiment, "metric": name, "n": len(rows),
                          "mean": statistics.mean(values), "standard_deviation": deviation,
                          "independent_repeats": False, "distinct_recorded_seeds": independent,
                          "repeat_basis": "matching_declared_configuration_and_command_hashes"})
    return summaries


def collect_run_results(run: RunHandle) -> dict[str, Any]:
    reader = ResultReader(run, load_results_policy())
    state, raw_state, history = reader.state()
    experiments, metrics, curves = reader.jobs()
    metrics.extend(reader.unverified_metrics(metrics))
    question: str | None = None
    hypotheses: list[str] = []
    interpretations: list[str] = []
    protocol: dict[str, Any] = {"status": "missing", "metrics": [], "commands": [], "declared_environment": None,
                                "data_fingerprints_verified": False}
    for stage, stem, schema in (("idea", "idea_proposal", "proposal.v1"), ("writing", "research_report", "report.v1")):
        path = run.root / stage / (stem + ".approved.md")
        if path.exists():
            try:
                data = reader.read(f"{stage}/{stem}.approved.md")
                document = validate_document(data.decode("utf-8"), expected_schema=schema)
                if not document.valid or document.metadata.get("project") != run.project:
                    raise ValueError("Invalid artifact")
                reader.source("approved_" + schema, data)
                if stage == "idea":
                    question = public_text(document.metadata.get("research_question", "")) or None
                    hypotheses.append(public_text(document.metadata.get("hypothesis", "")))
                else:
                    interpretations.append("存在 schema 合规的 Writing 文档；文档内容本身不作为实验有效或目标达成证明。")
            except (OSError, ValueError, UnicodeError):
                reader.limitations.append("一份研究文档未通过安全路径或 schema 校验，未纳入报告。")
    try:
        from app.bridge.research_run_service import load_run_research_contract

        # The shared loader additionally cross-checks these persisted records.
        # Bound and reject symlinks before letting it parse any of them.
        for relative in ("input/research_task.v1.json", "input/run_request_options.v1.json", "run_meta.json"):
            if (run.root / relative).exists():
                reader.read(relative)
        contract = load_run_research_contract(run, raw_state.get("request", {}).get("extra"))
        if contract is not None:
            question = public_text(contract.task.goal)
            definitions = {item.name: item for item in contract.task.project.metrics}
            for row in metrics:
                definition = definitions.get(row["name"])
                if definition is not None:
                    row.update(unit=public_text(definition.unit), direction=definition.direction)
            contract_source = reader.source("frozen_research_task", reader.read("input/research_task.v1.json"))
            protocol.update(status="declared_contract", source_id=contract_source,
                metrics=[{"name": public_text(item.name), "unit": public_text(item.unit), "direction": item.direction,
                          "target": item.target, "tolerance": item.tolerance} for item in contract.task.project.metrics],
                commands=[{"name": public_text(item.name), "purpose": item.purpose, "external_arguments_required": True}
                          for item in contract.task.project.commands],
                declared_environment={"kind": contract.task.project.execution.kind, "device": contract.task.project.execution.device},
                external_data_source_count=len(contract.task.project.paths.data))
    except (OSError, ValueError, TypeError, KeyError):
        reader.limitations.append("冻结研究合同未通过校验，未使用其中的指标协议。")
    if question is None and (run.root / "input/user_request.md").exists():
        try:
            question = public_text(reader.read("input/user_request.md").decode("utf-8"))
        except (OSError, ValueError, UnicodeError):
            reader.limitations.append("研究问题输入缺失或不可安全读取。")
    verified = sum(row["verification"] == "verified_local_receipt" for row in experiments)
    outcome = {"status": "unknown", "reason": "没有经过独立验证的目标判定；完成流程不等于目标达成。"}
    if state["authority"] == "sqlite":
        termination = raw_state.get("termination") or {}
        if termination.get("type") == "cancelled" and termination.get("cleanup_complete") is True:
            outcome = {"status": "user_cancelled", "reason": "权威状态记录取消且本地任务清理完成。"}
        elif state["status"] in {"failed", "blocked"}:
            outcome = {"status": state["status"], "reason": "权威运行状态记录失败或阻塞；不推断科学结论。"}
    reader.limitations.extend(["单次测量不提供统计显著性；只对相同声明配置/命令计算描述性波动，未认证数据与协议匹配或统计独立性。",
        "作业回执校验验证已存证据一致性，不证明训练协议科学有效或完成独立复现。",
        "原始私有数据、完整源码、原始日志、模型上下文和凭据不进入默认离线包。",
        "未绑定可信测量回执的旧领域或远端结果保留未验证，不推断执行成功。"])
    resources = reader.resources()
    return {"schema_id": "run_results.v1", "identity": {"run_id": public_text(run.run_id), "project": public_text(run.project),
        "task": public_text(run.task), "question": question}, "state": state, "outcome": outcome,
        "evidence": {"level": "receipt_verified" if verified else "unverified" if metrics or experiments else "missing",
                     "verified_jobs": verified, "total_jobs": len(experiments)},
        "experiments": experiments, "metrics": metrics, "curves": curves, "statistics": _statistics(experiments, metrics), "protocol": protocol,
        "resources": resources, "conclusions": {"facts": [f"已校验 {verified} 项本地作业的身份、请求/结果哈希及测量文件。"],
            "hypotheses": hypotheses, "interpretations": interpretations}, "history": history,
        "limitations": list(dict.fromkeys(reader.limitations)), "sources": reader.sources,
        "reproduction": {"status": "reviewable_only", "independent_rerun_verified": False,
            "external_requirements": ["原始授权代码、数据划分、冻结配置与运行环境需由项目持有人另行提供。",
                                      "需在独立环境重跑声明的基线/候选并按预先约定容差核对。"]}}
