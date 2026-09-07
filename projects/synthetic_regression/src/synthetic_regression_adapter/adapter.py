"""Standard-library implementation of the public synthetic adapter.v1."""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import random
import sys
import time
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Mapping, Sequence, cast


class ContractError(ValueError):
    """Raised when a request or packaged contract is invalid."""


@dataclass(frozen=True)
class CandidateConfig:
    candidate_id: str
    fingerprint: str
    config: dict[str, object]


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def content_hash(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def candidate_configs() -> tuple[CandidateConfig, ...]:
    discovery = _resource_json("discovery.yaml")
    if discovery.get("schema_id") != "discovery_config.v1":
        raise ContractError("invalid discovery schema")
    family = _required_string(discovery, "family")
    expected_count = _integer(discovery.get("candidate_count"), "candidate_count")
    search_space = _mapping(discovery.get("search_space"), "search_space")
    names = tuple(sorted(search_space))
    axes: list[tuple[object, ...]] = []
    for name in names:
        raw_axis = search_space[name]
        if not isinstance(raw_axis, list) or not raw_axis:
            raise ContractError(f"search_space.{name} must be a non-empty list")
        axes.append(tuple(raw_axis))
    candidates: list[CandidateConfig] = []
    for values in itertools.product(*axes):
        hyperparameters = dict(zip(names, values, strict=True))
        config: dict[str, object] = {
            "schema_id": "synthetic_candidate_config.v1",
            "family": family,
            "hyperparameters": hyperparameters,
        }
        fingerprint = content_hash(canonical_bytes(config))
        candidates.append(
            CandidateConfig(
                candidate_id=f"syn_{fingerprint.removeprefix('sha256:')[:20]}",
                fingerprint=fingerprint,
                config=config,
            )
        )
    if len(candidates) != expected_count:
        raise ContractError(
            f"search space yields {len(candidates)} candidates; expected {expected_count}"
        )
    return tuple(candidates)


def evaluate_candidate(
    candidate: CandidateConfig,
    *,
    seed: int,
    candidate_id: str | None = None,
    fidelity: str = "F0",
    mode: str = "synthetic",
) -> dict[str, object]:
    if mode != "synthetic":
        raise ContractError("only real synthetic fitting is supported; mock/config-only evaluation is removed")
    if fidelity not in {"F0", "F1"}:
        raise ContractError("synthetic fidelity must be F0 or F1")
    dataset_bytes = _resource_bytes("dataset.json")
    dataset = _mapping(json.loads(dataset_bytes), "dataset")
    x_values = _number_list(dataset.get("x"), "dataset.x")
    y_values = _number_list(dataset.get("y"), "dataset.y")
    if len(x_values) != len(y_values) or not x_values:
        raise ContractError("dataset x/y lengths must match and be non-empty")
    hyperparameters = _mapping(candidate.config.get("hyperparameters"), "hyperparameters")
    degree = _integer(hyperparameters.get("degree"), "degree")
    regularization = _number(hyperparameters.get("regularization"), "regularization")
    if degree < 1 or degree > 5 or regularization < 0.0:
        raise ContractError("candidate hyperparameters are outside the packaged search space")
    indices = list(range(len(x_values)))
    random.Random(seed).shuffle(indices)
    if len(indices) < 11:
        raise ContractError("packaged fit requires at least eleven distinct samples")
    train_indices = indices[:6 if fidelity == "F0" else 8]
    validation_indices = indices[-3:]
    train_x = [x_values[i] for i in train_indices]
    train_y = [y_values[i] for i in train_indices]
    mean = sum(train_x) / len(train_x)
    scale = max(abs(x - mean) for x in train_x)
    if scale == 0:
        raise ContractError("training inputs are rank deficient")
    design = [[((x - mean) / scale)**p for p in range(degree + 1)] for x in train_x]
    validation_design = [[((x_values[i] - mean) / scale)**p for p in range(degree + 1)]
                         for i in validation_indices]
    coefficients = fit_ridge(design, train_y, regularization)
    predictions = [sum(c * x for c, x in zip(coefficients, row, strict=True)) for row in validation_design]
    targets = [y_values[i] for i in validation_indices]
    mse = sum(
        (prediction - target) ** 2
        for prediction, target in zip(predictions, targets, strict=True)
    ) / len(targets)
    # This is an explicitly defined coefficient-norm proxy, not a statistical or
    # physical stability guarantee. Its value comes from the fitted coefficients.
    coefficient_norm = math.sqrt(sum(c*c for c in coefficients))
    raw_values = {
        "validation_mse": mse,
        "model_terms": float(degree + 1),
        "stability_score": 1.0 / (1.0 + coefficient_norm),
    }
    metrics = _metric_definitions()
    dataset_hash = content_hash(dataset_bytes)
    evaluator_hash = content_hash(Path(__file__).read_bytes())
    common: dict[str, object] = {
        "seed": seed,
        "fidelity": fidelity,
        "mode": mode,
        "dataset_hash": dataset_hash,
        "evaluator_hash": evaluator_hash,
        "candidate_hash": candidate.fingerprint,
        "training_indices": train_indices,
        "validation_indices": validation_indices,
        "fit": "ridge_qr_normalized_inputs_all_coefficients_penalized",
        "stability_score_definition": "1/(1+L2 fitted coefficient norm); proxy only",
    }
    raw_metrics: dict[str, object] = {}
    canonical_metrics: dict[str, object] = {}
    for name in sorted(metrics):
        definition = metrics[name]
        value = raw_values[name]
        if not math.isfinite(value):
            raise ContractError(f"metric {name} is not finite")
        record = {
            "value": value,
            "unit": definition["unit"],
            "direction": definition["direction"],
            **common,
        }
        raw_metrics[name] = dict(record)
        canonical_metrics[name] = dict(record)
    envelope: dict[str, object] = {
        "schema_id": "metric_envelope.v1",
        "candidate_id": candidate_id or candidate.candidate_id,
        "raw_metrics": raw_metrics,
        "canonical_metrics": canonical_metrics,
        "provenance": common,
        "hard_constraints_passed": True,
    }
    envelope["envelope_hash"] = content_hash(canonical_bytes(envelope))
    return envelope


def handle_request(payload: Mapping[str, object]) -> dict[str, object]:
    started = time.monotonic()
    request_id = _required_string(payload, "request_id")
    if payload.get("protocol", "adapter.v1") != "adapter.v1":
        return _response(
            request_id,
            status="blocked",
            error_code="unsupported_protocol",
            error="protocol must be adapter.v1",
        )
    if payload.get("project") != "synthetic_regression":
        return _response(
            request_id,
            status="blocked",
            error_code="project_mismatch",
            error="adapter only accepts synthetic_regression",
        )
    action = payload.get("action")
    if action == "readiness":
        try:
            candidate_configs()
            _metric_definitions()
            dataset = _resource_json("dataset.json")
            if len(_number_list(dataset.get("x"), "dataset.x")) != len(_number_list(dataset.get("y"), "dataset.y")):
                raise ContractError("dataset x/y length mismatch")
        except (ContractError, OSError, ValueError) as exc:
            return _response(request_id, status="blocked", error_code="resources_unavailable", error=str(exc))
        return _response(
            request_id,
            status="ready",
            findings=("Packaged dataset and evaluator are ready.",),
        )
    try:
        config = _mapping(payload.get("config", {}), "config")
        project_inputs = _project_inputs(config)
        candidate = _candidate_from_request(config)
        declared_id = payload.get("candidate_id", "")
        if not isinstance(declared_id, str):
            raise ContractError("candidate_id must be a string")
        if "model_genome" not in config and declared_id not in {"", candidate.candidate_id}:
            raise ContractError("candidate_id does not match canonical candidate config")
        if action == "preflight":
            return _response(
                request_id,
                status="ok",
                artifacts={"candidate_fingerprint": candidate.fingerprint},
                findings=("Synthetic candidate preflight passed.",),
            )
        if action == "execute":
            return _response(
                request_id,
                status="ok",
                artifacts={"candidate_fingerprint": candidate.fingerprint},
                findings=("Synthetic candidate requires no materialized workspace.",),
            )
        if action not in {"evaluate", "profile"}:
            raise ContractError(f"unsupported action: {action!r}")
        seed = _integer(project_inputs.get("seed", payload.get("seed", 0)), "seed")
        fidelity = str(project_inputs.get("fidelity", payload.get("fidelity", "F0")))
        mode = str(project_inputs.get("mode", "synthetic"))
        envelope = evaluate_candidate(
            candidate,
            seed=seed,
            candidate_id=declared_id or candidate.candidate_id,
            fidelity=fidelity,
            mode=mode,
        )
        return _response(
            request_id,
            status="ok",
            raw_metrics=envelope,
            artifacts={
                "candidate_fingerprint": candidate.fingerprint,
                "dataset_hash": cast(str, _mapping(envelope["provenance"], "provenance")["dataset_hash"]),
            },
            resource_usage={"wall_seconds": time.monotonic() - started, "gpu_seconds": 0.0},
            findings=("Actual CPU ridge fit and held-out synthetic evaluation completed; no production validation.",),
        )
    except ContractError as exc:
        return _response(
            request_id,
            status="blocked",
            error_code="synthetic_preflight_blocked",
            error=str(exc),
        )


def batch_payload(*, count: int = 20, seed: int = 0) -> dict[str, object]:
    candidates = candidate_configs()
    if count < 1 or count > len(candidates):
        raise ContractError(f"count must be between 1 and {len(candidates)}")
    records = [
        {
            "candidate_id": candidate.candidate_id,
            "fingerprint": candidate.fingerprint,
            "config": candidate.config,
            "metric_envelope": evaluate_candidate(candidate, seed=seed + index),
        }
        for index, candidate in enumerate(candidates[:count])
    ]
    return {
        "schema_id": "synthetic_candidate_batch.v1",
        "seed": seed,
        "candidate_count": len(records),
        "candidates": records,
        "batch_hash": content_hash(canonical_bytes(records)),
    }


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        return _serve_adapter_request()
    parser = argparse.ArgumentParser(description="Synthetic regression adapter")
    subparsers = parser.add_subparsers(dest="command", required=True)
    batch = subparsers.add_parser("batch")
    batch.add_argument("--count", type=int, default=20)
    batch.add_argument("--seed", type=int, default=0)
    parsed = parser.parse_args(arguments)
    if parsed.command != "batch":
        parser.error("unsupported command")
    try:
        payload = batch_payload(count=cast(int, parsed.count), seed=cast(int, parsed.seed))
    except ContractError as exc:
        sys.stderr.write(f"blocked: {exc}\n")
        return 2
    sys.stdout.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


def _serve_adapter_request() -> int:
    try:
        decoded = json.loads(sys.stdin.buffer.read())
        request = _mapping(decoded, "adapter request")
        response = handle_request(request)
    except (ContractError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        sys.stderr.write(f"invalid adapter request: {exc}\n")
        return 2
    sys.stdout.write(json.dumps(response, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


def _candidate_from_request(config: Mapping[str, object]) -> CandidateConfig:
    candidates = candidate_configs()
    if "model_genome" in config:
        genome = _mapping(config["model_genome"], "config.model_genome")
        if genome.get("schema_id") != "model_genome.v1":
            raise ContractError("model_genome.schema_id must be model_genome.v1")
        family = _required_string(genome, "family")
        hyperparameters = _mapping(
            genome.get("hyperparameters"), "model_genome.hyperparameters"
        )
        normalized: dict[str, object] = {
            "schema_id": "synthetic_candidate_config.v1",
            "family": family,
            "hyperparameters": hyperparameters,
        }
        fingerprint = content_hash(canonical_bytes(normalized))
        for candidate in candidates:
            if candidate.fingerprint == fingerprint:
                return candidate
        raise ContractError("model_genome is not in the packaged search space")
    if "candidate_index" in config:
        index = _integer(config["candidate_index"], "candidate_index")
        if index < 0 or index >= len(candidates):
            raise ContractError(f"candidate_index must be in [0, {len(candidates) - 1}]")
        return candidates[index]
    raw_candidate = _mapping(config.get("candidate"), "config.candidate")
    encoded = canonical_bytes(raw_candidate)
    fingerprint = content_hash(encoded)
    for candidate in candidates:
        if candidate.fingerprint == fingerprint:
            return candidate
    raise ContractError("candidate is not in the packaged search space")


def _project_inputs(config: Mapping[str, object]) -> dict[str, object]:
    nested_raw = config.get("project_inputs", {})
    nested = _mapping(nested_raw, "config.project_inputs")
    values = dict(nested)
    for key in ("mode", "candidate_count", "seed", "fidelity"):
        if key in config:
            values[key] = config[key]
    if "mode" in values and values["mode"] != "synthetic":
        raise ContractError("synthetic mode must be synthetic; mock/config-only execution is removed")
    if "candidate_count" in values:
        count = _integer(values["candidate_count"], "candidate_count")
        if count < 1 or count > len(candidate_configs()):
            raise ContractError("candidate_count is outside the packaged search space")
    if "seed" in values:
        _integer(values["seed"], "seed")
    if "fidelity" in values and values["fidelity"] not in {"F0", "F1"}:
        raise ContractError("synthetic fidelity must be F0 or F1")
    return values


def fit_ridge(design: list[list[float]], targets: list[float], regularization: float) -> tuple[float, ...]:
    """Solve the actual augmented least-squares problem using reorthogonalized QR.

    The tiny public fixture remains standard-library-only. No target coefficients,
    fabricated optimizer progress or seed-dependent metric perturbations are used.
    """
    if not design or len(design) != len(targets) or not design[0] or regularization < 0:
        raise ContractError("invalid regression dimensions or regularization")
    width = len(design[0])
    if any(len(row) != width for row in design):
        raise ContractError("ragged design matrix")
    if not all(math.isfinite(x) for row in design for x in row) or not all(math.isfinite(y) for y in targets):
        raise ContractError("regression inputs must be finite")
    if not math.isfinite(regularization):
        raise ContractError("regularization must be finite")
    rows = [list(row) for row in design]
    values = list(targets)
    if regularization:
        root = math.sqrt(regularization)
        rows.extend([[root if i == j else 0.0 for j in range(width)] for i in range(width)])
        values.extend([0.0] * width)
    q: list[list[float]] = []
    r = [[0.0] * width for _ in range(width)]
    for j in range(width):
        column = [row[j] for row in rows]
        original_norm = math.sqrt(sum(x*x for x in column))
        for _ in range(2):
            for i in range(j):
                projection = sum(a*b for a, b in zip(q[i], column, strict=True))
                r[i][j] += projection
                column = [a - projection*b for a, b in zip(column, q[i], strict=True)]
        norm = math.sqrt(sum(x*x for x in column))
        if norm <= 1e-12 * max(original_norm, 1.0):
            raise ContractError("rank-deficient regression design")
        r[j][j] = norm
        q.append([x / norm for x in column])
    rhs = [sum(a*b for a, b in zip(column, values, strict=True)) for column in q]
    solution = [0.0] * width
    for i in reversed(range(width)):
        solution[i] = (rhs[i] - sum(r[i][j] * solution[j] for j in range(i+1, width))) / r[i][i]
    return tuple(solution)


def _metric_definitions() -> dict[str, dict[str, str]]:
    payload = _resource_json("metrics.yaml")
    if payload.get("schema_id") != "metric_catalog.v1":
        raise ContractError("invalid metric catalog schema")
    raw_metrics = _mapping(payload.get("metrics"), "metrics")
    definitions: dict[str, dict[str, str]] = {}
    for name, raw_definition in raw_metrics.items():
        definition = _mapping(raw_definition, f"metrics.{name}")
        unit = _required_string(definition, "unit")
        direction = _required_string(definition, "direction")
        if direction not in {"minimize", "maximize"}:
            raise ContractError(f"invalid direction for metric {name}")
        definitions[name] = {"unit": unit, "direction": direction}
    return definitions


def _response(
    request_id: str,
    *,
    status: str,
    raw_metrics: Mapping[str, object] | None = None,
    artifacts: Mapping[str, str] | None = None,
    resource_usage: Mapping[str, float] | None = None,
    findings: tuple[str, ...] = (),
    error_code: str = "",
    error: str = "",
) -> dict[str, object]:
    return {
        "protocol": "adapter.v1",
        "request_id": request_id,
        "status": status,
        "raw_metrics": dict(raw_metrics or {}),
        "artifacts": dict(artifacts or {}),
        "resource_usage": dict(resource_usage or {}),
        "findings": list(findings),
        "error_code": error_code,
        "error": error,
    }


def _resource_json(name: str) -> dict[str, object]:
    return _mapping(json.loads(_resource_bytes(name)), name)


def _resource_bytes(name: str) -> bytes:
    return files("synthetic_regression_adapter").joinpath("resources", name).read_bytes()


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ContractError(f"{label} must be an object")
    return cast(dict[str, object], value)


def _required_string(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ContractError(f"{key} must be a non-empty string")
    return item


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{label} must be an integer")
    return value


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ContractError(f"{label} must be finite")
    return number


def _number_list(value: object, label: str) -> tuple[float, ...]:
    if not isinstance(value, list):
        raise ContractError(f"{label} must be a list")
    return tuple(_number(item, label) for item in value)


if __name__ == "__main__":
    raise SystemExit(main())
