"""Pure ModelGenome mutation and deterministic Candidate construction."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any, Literal

from pydantic import ValidationError

from app.harness.discovery.canonical import stable_hash
from app.harness.discovery.models import CandidateRecord, ModelGenome

DeltaKind = Literal["set", "remove", "merge"]
_MUTABLE_ROOTS = frozenset({"structure", "hyperparameters", "recipe"})
_FORBIDDEN_SEGMENTS = frozenset({"", ".", "..", "__class__", "__dict__", "__globals__"})


@dataclass(frozen=True)
class DeltaOperation:
    kind: DeltaKind
    path: tuple[str, ...]
    value: Any = None


@dataclass(frozen=True)
class ConfigDelta:
    operations: tuple[DeltaOperation, ...]


class DeltaValidationError(ValueError):
    def __init__(self, errors: tuple[str, ...]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def config_path(operation: DeltaOperation) -> str:
    return ".".join(operation.path)


def validate_config_delta(
    genome: ModelGenome,
    delta: ConfigDelta,
    *,
    allowed_zones: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    """Return every mutation error without changing the input genome."""
    errors: list[str] = []
    simulated_payload = deepcopy(genome.model_dump(mode="python"))
    contract_zones = allowed_zones
    for index, operation in enumerate(delta.operations):
        label = f"operation[{index}]"
        previous_error_count = len(errors)
        if operation.kind not in {"set", "remove", "merge"}:
            errors.append(f"{label}: unsupported operation '{operation.kind}'")
            continue
        if not operation.path:
            errors.append(f"{label}: path must not be empty")
            continue
        if any(segment in _FORBIDDEN_SEGMENTS or "/" in segment or "\\" in segment for segment in operation.path):
            errors.append(f"{label}: unsafe path '{config_path(operation)}'")
            continue
        if operation.path[0] not in _MUTABLE_ROOTS:
            errors.append(f"{label}: immutable root '{operation.path[0]}'")
            continue
        dotted = config_path(operation)
        if not _matches_any_zone(dotted, genome.mutable_zones):
            errors.append(f"{label}: path '{dotted}' is outside genome mutable zones")
        if contract_zones is not None and not _matches_any_zone(dotted, contract_zones):
            errors.append(f"{label}: path '{dotted}' is outside contract evolution zones")
        if operation.kind == "merge" and not isinstance(operation.value, dict):
            errors.append(f"{label}: merge value must be an object")
        if len(errors) == previous_error_count:
            try:
                _apply_operation(simulated_payload, operation)
            except DeltaValidationError as exc:
                errors.extend(f"{label}: {error}" for error in exc.errors)
    if not errors:
        try:
            ModelGenome.model_validate(simulated_payload)
        except ValidationError as exc:
            errors.append(f"materialized genome is invalid: {exc.errors()[0]['msg']}")
    return tuple(errors)


def apply_config_delta(
    genome: ModelGenome,
    delta: ConfigDelta,
    *,
    allowed_zones: tuple[str, ...] | None = None,
) -> ModelGenome:
    """Apply a validated config-only delta and return a new frozen genome."""
    errors = validate_config_delta(genome, delta, allowed_zones=allowed_zones)
    if errors:
        raise DeltaValidationError(errors)

    payload = deepcopy(genome.model_dump(mode="python"))
    for operation in delta.operations:
        _apply_operation(payload, operation)
    return ModelGenome.model_validate(payload)


def genome_fingerprint(genome: ModelGenome) -> str:
    return stable_hash(genome.model_dump(mode="json"))


def derive_candidate_id(
    *,
    run_id: str,
    genome: ModelGenome,
    parent_ids: tuple[str, ...],
    generation: int,
    iteration: int,
    creator: str,
    operator: str,
) -> str:
    """Derive a stable identifier independent of mapping and parent ordering."""
    digest = stable_hash(
        {
            "run_id": run_id,
            "genome": genome.model_dump(mode="json"),
            "parent_ids": sorted(set(parent_ids)),
            "generation": generation,
            "iteration": iteration,
            "creator": creator,
            "operator": operator,
        },
        prefix="",
    )
    return f"cand_{digest[:24]}"


def build_candidate_record(
    *,
    run_id: str,
    genome: ModelGenome,
    creator: str,
    operator: str,
    parent_ids: tuple[str, ...] = (),
    generation: int = 0,
    iteration: int = 0,
    model_provider: str = "",
    model_name: str = "",
    prompt_hash: str = "",
    context_manifest_ref: str = "",
    artifact_refs: dict[str, str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> CandidateRecord:
    """Construct a CandidateRecord with stable identity and exact fingerprint."""
    normalized_parents = tuple(sorted(set(parent_ids)))
    candidate_id = derive_candidate_id(
        run_id=run_id,
        genome=genome,
        parent_ids=normalized_parents,
        generation=generation,
        iteration=iteration,
        creator=creator,
        operator=operator,
    )
    return CandidateRecord(
        candidate_id=candidate_id,
        run_id=run_id,
        parent_ids=normalized_parents,
        generation=generation,
        iteration=iteration,
        creator=creator,
        model_provider=model_provider,
        model_name=model_name,
        prompt_hash=prompt_hash,
        context_manifest_ref=context_manifest_ref,
        operator=operator,
        genome=genome,
        artifact_refs=dict(artifact_refs or {}),
        fingerprints={"exact": genome_fingerprint(genome)},
        idempotency_key=f"candidate:{candidate_id}",
        metadata=dict(metadata or {}),
    )


def _matches_any_zone(path: str, zones: tuple[str, ...]) -> bool:
    for raw_zone in zones:
        zone = raw_zone.strip().replace("/", ".").rstrip(".")
        if not zone:
            continue
        if fnmatchcase(path, zone):
            return True
        if not any(token in zone for token in "*?[") and path.startswith(zone + "."):
            return True
    return False


def _apply_operation(payload: dict[str, Any], operation: DeltaOperation) -> None:
    cursor: dict[str, Any] = payload
    for segment in operation.path[:-1]:
        child = cursor.get(segment)
        if child is None and operation.kind in {"set", "merge"}:
            child = {}
            cursor[segment] = child
        if not isinstance(child, dict):
            raise DeltaValidationError((f"path '{config_path(operation)}' crosses a scalar value",))
        cursor = child

    leaf = operation.path[-1]
    if operation.kind == "remove":
        if leaf not in cursor:
            raise DeltaValidationError((f"path '{config_path(operation)}' does not exist",))
        del cursor[leaf]
        return
    if operation.kind == "set":
        cursor[leaf] = deepcopy(operation.value)
        return

    current = cursor.get(leaf)
    if current is None:
        current = {}
        cursor[leaf] = current
    if not isinstance(current, dict) or not isinstance(operation.value, dict):
        raise DeltaValidationError((f"path '{config_path(operation)}' is not mergeable",))
    for key, value in operation.value.items():
        current[str(key)] = deepcopy(value)
