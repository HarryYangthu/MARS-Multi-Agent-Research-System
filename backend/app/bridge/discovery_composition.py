"""Project-Pack-driven composition for the domain-neutral Discovery Service."""
from __future__ import annotations

import itertools
import math
import random
import re
from dataclasses import asdict
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.bridge.discovery_types import CandidateProposalRequest
from app.bridge.extension_runtime import ExtensionRuntime
from app.execution.adapters.base import AdapterRequest, AdapterResponse, ProjectAdapter
from app.harness.discovery.candidate_builder import build_candidate_record
from app.harness.discovery.canonical import stable_hash
from app.harness.discovery.models import CandidateRecord, ModelGenome
from app.harness.discovery.sampling import (
    BanditArm,
    ParentCandidate,
    UCBSelectionAudit,
    sample_parent,
    select_ucb,
    update_arm,
)
from app.harness.project_packs.registry import LoadedProjectPack, ProjectPackError

SearchValue = str | int | float | bool
_SAFE_SEARCH_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class DiscoveryCompositionError(ValueError):
    """A Pack cannot be composed into a safe model-discovery runtime."""


class _PackDiscoveryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["discovery_config.v1"] = "discovery_config.v1"
    family: str = Field(min_length=1)
    candidate_count: int = Field(ge=1, le=10_000)
    operator: str = Field(min_length=1)
    mutable_zones: tuple[str, ...] = Field(min_length=1)
    search_space: dict[str, tuple[SearchValue, ...]] = Field(min_length=1)
    models: tuple[str, ...] = ()
    operators: tuple[str, ...] = ()
    objectives: tuple[str, ...] = ()
    novelty: dict[str, Any] = Field(default_factory=dict)
    archive: dict[str, Any] = Field(default_factory=dict)
    promotion: dict[str, Any] = Field(default_factory=dict)
    stop: dict[str, Any] = Field(default_factory=dict)

    @field_validator("search_space")
    @classmethod
    def validate_search_space(
        cls,
        value: dict[str, tuple[SearchValue, ...]],
    ) -> dict[str, tuple[SearchValue, ...]]:
        for name, axis in value.items():
            if _SAFE_SEARCH_KEY.fullmatch(name) is None:
                raise ValueError(f"unsafe search-space key: {name!r}")
            if not axis:
                raise ValueError(f"search_space.{name} must not be empty")
            for item in axis:
                if isinstance(item, float) and not math.isfinite(item):
                    raise ValueError(f"search_space.{name} contains a non-finite value")
        return value

    @model_validator(mode="after")
    def validate_candidate_capacity(self) -> _PackDiscoveryConfig:
        combinations = math.prod(len(axis) for axis in self.search_space.values())
        if self.candidate_count > combinations:
            raise ValueError(
                f"candidate_count {self.candidate_count} exceeds {combinations} combinations"
            )
        required_zones = {
            f"hyperparameters.{name}" for name in self.search_space
        }
        if not required_zones.issubset(set(self.mutable_zones)):
            missing = ", ".join(sorted(required_zones - set(self.mutable_zones)))
            raise ValueError(f"mutable_zones do not cover search space: {missing}")
        return self


class ProjectPackCandidateAgent:
    """Generate deterministic config candidates and persist Shinka selection audit."""

    def __init__(self, runtime: ExtensionRuntime) -> None:
        self.runtime = runtime
        self._configs: dict[str, _PackDiscoveryConfig] = {}

    async def propose(self, request: CandidateProposalRequest) -> CandidateRecord:
        config = self._config(request.contract.project)
        if request.ordinal >= config.candidate_count:
            raise DiscoveryCompositionError(
                f"ordinal {request.ordinal} exceeds Pack candidate_count "
                f"{config.candidate_count}"
            )
        combinations = _candidate_combinations(config)
        randomizer = random.Random(request.contract.seed + request.iteration)
        randomizer.shuffle(combinations)
        hyperparameters = combinations[request.ordinal]
        audit_index = request.iteration * config.candidate_count + request.ordinal
        audit_seed = request.contract.seed + audit_index * 10
        model_audit = _bandit_audit(
            config.models or ("project_pack",),
            index=audit_index,
            seed=request.contract.seed + 1,
        )
        operator_audit = _bandit_audit(
            config.operators or (config.operator,),
            index=audit_index,
            seed=request.contract.seed + 2,
        )
        parent_ids: tuple[str, ...] = ()
        if request.parent_candidate_ids:
            parent_audit = sample_parent(
                tuple(
                    ParentCandidate(candidate_id=candidate_id)
                    for candidate_id in request.parent_candidate_ids
                ),
                seed=audit_seed,
            )
            parent_ids = (parent_audit.selected_id,)
            parent_payload: dict[str, Any] = asdict(parent_audit)
        else:
            parent_payload = {
                "seed": audit_seed,
                "selected_id": "",
                "choices": (),
                "reason": "root generation has no parent candidates",
            }

        genome = ModelGenome(
            family=config.family,
            hyperparameters=hyperparameters,
            mutable_zones=config.mutable_zones,
        )
        selection_audit = {
            "parent": parent_payload,
            "model": asdict(model_audit),
            "operator": asdict(operator_audit),
            "search": {
                "seed": request.contract.seed + request.iteration,
                "ordinal": request.ordinal,
                "candidate_count": config.candidate_count,
                "reason": "seeded Pack search-space permutation",
            },
        }
        return build_candidate_record(
            run_id=request.contract.run_id,
            genome=genome,
            creator="project_pack_candidate_agent",
            operator=operator_audit.selected_id,
            parent_ids=parent_ids,
            generation=request.iteration,
            iteration=request.iteration,
            model_provider="project_pack",
            model_name=model_audit.selected_id,
            prompt_hash=stable_hash(selection_audit),
            context_manifest_ref=(
                f"project_pack://{request.contract.project}/discovery.yaml"
            ),
            metadata={"selection_audit": selection_audit},
        )

    def _config(self, project: str) -> _PackDiscoveryConfig:
        existing = self._configs.get(project)
        if existing is not None:
            return existing
        try:
            pack = self.runtime.project_packs.get(project)
        except ProjectPackError as exc:
            raise DiscoveryCompositionError(str(exc)) from exc
        config = _load_discovery_config(pack)
        self._configs[project] = config
        return config


class ProjectPackRoutingAdapter(ProjectAdapter):
    """Route one generic adapter.v1 request to its Pack-owned trusted adapter."""

    def __init__(self, runtime: ExtensionRuntime) -> None:
        self.runtime = runtime

    @property
    def name(self) -> str:
        return "project_pack_router"

    async def invoke(self, request: AdapterRequest) -> AdapterResponse:
        try:
            qualified = self._qualified_name(request)
            adapter = self.runtime.adapters.get(qualified)
        except (KeyError, ProjectPackError, DiscoveryCompositionError) as exc:
            return AdapterResponse(
                request_id=request.request_id,
                status="blocked",
                error_code="project_adapter_unavailable",
                error=str(exc),
            )
        return await adapter.invoke(request)

    def _qualified_name(self, request: AdapterRequest) -> str:
        pack = self.runtime.project_packs.get(request.project)
        aliases = tuple(sorted(pack.manifest.adapters))
        project_inputs = request.config.get("project_inputs", {})
        requested = ""
        if isinstance(project_inputs, dict):
            requested = str(project_inputs.get("adapter_alias") or "").strip()
        requested = requested or str(request.config.get("adapter_alias") or "").strip()
        if requested:
            if requested not in aliases:
                raise DiscoveryCompositionError(
                    f"Pack '{request.project}' has no adapter alias '{requested}'"
                )
            alias = requested
        elif len(aliases) == 1:
            alias = aliases[0]
        elif not aliases:
            raise DiscoveryCompositionError(
                f"Pack '{request.project}' declares no trusted adapter"
            )
        else:
            raise DiscoveryCompositionError(
                f"Pack '{request.project}' has multiple adapters; adapter_alias is required"
            )
        return self.runtime.adapter_name(request.project, alias)


def _load_discovery_config(pack: LoadedProjectPack) -> _PackDiscoveryConfig:
    path = pack.file("discovery")
    try:
        payload: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        return _PackDiscoveryConfig.model_validate(payload)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        raise DiscoveryCompositionError(
            f"invalid discovery config for Pack '{pack.manifest.project_id}': {exc}"
        ) from exc


def _candidate_combinations(
    config: _PackDiscoveryConfig,
) -> list[dict[str, SearchValue]]:
    names = tuple(sorted(config.search_space))
    axes = tuple(config.search_space[name] for name in names)
    return [
        dict(zip(names, values, strict=True))
        for values in itertools.product(*axes)
    ]


def _bandit_audit(
    arm_ids: tuple[str, ...],
    *,
    index: int,
    seed: int,
) -> UCBSelectionAudit:
    normalized = tuple(sorted(set(item.strip() for item in arm_ids if item.strip())))
    if not normalized:
        raise DiscoveryCompositionError("bandit arm list must not be empty")
    arms = tuple(BanditArm(arm_id=item) for item in normalized)
    selected: UCBSelectionAudit | None = None
    for step in range(index + 1):
        selected = select_ucb(arms, seed=seed + step)
        arms = tuple(
            update_arm(arm, reward=0.0)
            if arm.arm_id == selected.selected_id
            else arm
            for arm in arms
        )
    assert selected is not None
    return selected
