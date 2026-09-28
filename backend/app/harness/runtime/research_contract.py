"""Domain-neutral, immutable project and task contracts; no execution policy."""
from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

PositiveCount = Annotated[int, Field(strict=True, gt=0)]
NonNegativeCount = Annotated[int, Field(strict=True, ge=0)]
PositiveAmount = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
RunStatus = Literal["queued", "running", "pausing", "paused", "cancelling", "cancelled", "blocked", "failed", "completed"]
ResearchOutcome = Literal["unknown", "goal_met", "goal_not_met", "budget_stopped", "user_cancelled", "blocked", "failed"]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False, str_strip_whitespace=True)


def relative_scope(value: str) -> str:
    """Portable literal subtree scopes; glob and traversal semantics are forbidden."""
    path = PurePosixPath(value)
    if (not value or "\\" in value or "\x00" in value or path.is_absolute()
            or PureWindowsPath(value).drive or any(part in {"", ".", ".."} for part in value.split("/"))
            or any(char in value for char in "*?[]")):
        raise ValueError("Use a literal relative file/directory path without traversal, globs or drive prefixes")
    return path.as_posix()


def absolute_path(value: str) -> str:
    if not value or "\x00" in value or not Path(value).is_absolute() or ".." in Path(value).parts:
        raise ValueError("An absolute local path without traversal is required")
    return str(Path(value))


class ResearchBudget(ContractModel):
    search_candidates: PositiveCount
    deep_read_papers: PositiveCount
    concurrent_readers: PositiveCount
    proposal_candidates: PositiveCount
    implemented_candidates: PositiveCount
    debate_rounds: PositiveCount
    automatic_iterations: NonNegativeCount
    model_requests: PositiveCount
    tool_executions: PositiveCount
    research_activity_seconds: PositiveCount
    input_tokens: PositiveCount
    billed_output_tokens: PositiveCount
    model_cost_cny: PositiveAmount
    request_input_tokens: PositiveCount
    request_output_tokens: PositiveCount
    coding_output_tokens: PositiveCount
    training_job_seconds: PositiveCount
    concurrent_training_jobs: PositiveCount
    max_gpus: NonNegativeCount
    training_process_seconds: PositiveCount
    gpu_seconds: PositiveCount
    operation_retries: Annotated[int, Field(strict=True, ge=0, le=2)]
    repeated_error_limit: Annotated[int, Field(strict=True, gt=0, le=2)]

    @model_validator(mode="after")
    def coherent_limits(self) -> Self:
        pairs = ((self.deep_read_papers, self.search_candidates),
                 (self.concurrent_readers, self.deep_read_papers),
                 (self.implemented_candidates, self.proposal_candidates),
                 (self.request_input_tokens, self.input_tokens),
                 (self.request_output_tokens, self.billed_output_tokens),
                 (self.coding_output_tokens, self.billed_output_tokens),
                 (self.training_job_seconds, self.training_process_seconds))
        if any(smaller > total for smaller, total in pairs):
            raise ValueError("A per-action or subset limit cannot exceed its task total")
        return self


class ProjectPaths(ContractModel):
    code: str
    knowledge: tuple[str, ...]
    data: tuple[str, ...]
    output: str

    @field_validator("code", "output")
    @classmethod
    def validate_root(cls, value: str) -> str:
        return absolute_path(value)

    @field_validator("knowledge", "data")
    @classmethod
    def validate_inputs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(absolute_path(value) for value in values)


class CommandContract(ContractModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    purpose: Literal["check", "train", "evaluate"]
    executable: str
    arguments: tuple[Annotated[str, StringConstraints(strip_whitespace=False)], ...] = ()
    cwd: str = "."
    entrypoint_files: tuple[str, ...] = Field(min_length=1)

    @field_validator("executable")
    @classmethod
    def validate_executable(cls, value: str) -> str:
        return absolute_path(value)

    @field_validator("cwd")
    @classmethod
    def validate_cwd(cls, value: str) -> str:
        return value if value == "." else relative_scope(value)

    @field_validator("entrypoint_files")
    @classmethod
    def validate_entries(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(relative_scope(value) for value in values)

    @field_validator("arguments")
    @classmethod
    def validate_arguments(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any("\x00" in value for value in values):
            raise ValueError("Command arguments cannot contain NUL")
        return values


class MetricContract(ContractModel):
    name: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    direction: Literal["minimize", "maximize"]
    target: float = Field(allow_inf_nan=False, strict=True)
    tolerance: float = Field(ge=0, allow_inf_nan=False, strict=True)


class ExecutionContract(ContractModel):
    kind: Literal["local", "ssh"]
    device: Literal["cpu", "gpu"]
    connection_ref: str | None = None

    @model_validator(mode="after")
    def connection(self) -> Self:
        if self.kind == "ssh" and not self.connection_ref:
            raise ValueError("SSH requires a saved connection reference, never inline credentials")
        if self.kind == "local" and self.connection_ref is not None:
            raise ValueError("Local execution cannot carry an SSH connection reference")
        return self


class ProjectContract(ContractModel):
    schema_id: Literal["research_project.v1"] = "research_project.v1"
    project_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    display_name: str = Field(min_length=1)
    paths: ProjectPaths
    commands: tuple[CommandContract, ...] = Field(min_length=1)
    metrics: tuple[MetricContract, ...] = Field(min_length=1)
    baseline_files: tuple[str, ...] = Field(min_length=1)
    allowed_paths: tuple[str, ...] = Field(min_length=1)
    protected_paths: tuple[str, ...]
    execution: ExecutionContract

    @field_validator("baseline_files", "allowed_paths", "protected_paths")
    @classmethod
    def validate_scopes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(relative_scope(value) for value in values)

    @model_validator(mode="after")
    def identifiers(self) -> Self:
        if len({item.name for item in self.commands}) != len(self.commands):
            raise ValueError("Command names must be unique")
        if len({item.name for item in self.metrics}) != len(self.metrics):
            raise ValueError("Metric names must be unique")
        if not {"check", "train", "evaluate"}.issubset({item.purpose for item in self.commands}):
            raise ValueError("Declare check, train and evaluate commands")
        return self


class FileFingerprint(ContractModel):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ResearchTaskContract(ContractModel):
    schema_id: Literal["research_task.v1"] = "research_task.v1"
    goal: str = Field(min_length=1)
    mode: Literal["bounded_auto", "manual"]
    project: ProjectContract
    budget: ResearchBudget
    project_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_fingerprints: tuple[FileFingerprint, ...]


class ResearchIdentity(ContractModel):
    """Stable identifiers for later orchestration/storage integration."""
    run_id: str = Field(min_length=1)
    stage_id: str = Field(min_length=1)
    attempt: PositiveCount
    job_id: str | None = None


class ResearchResultStatus(ContractModel):
    run_status: RunStatus
    outcome: ResearchOutcome
