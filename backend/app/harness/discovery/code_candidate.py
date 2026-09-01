"""Domain-neutral contract for code-generating discovery candidates."""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.harness.discovery.canonical import stable_hash
from app.harness.discovery.code_materialization import code_identity_fingerprint

_SAFE_FACTORY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class TensorInterfaceSpec(BaseModel):
    """Observable tensor ABI; Project Packs own the concrete values."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_rank: int = Field(ge=1, le=8)
    output_rank: int = Field(ge=1, le=8)
    input_dtype: str = Field(min_length=1)
    output_dtype: str = Field(min_length=1)
    preserve_shape: bool = True


class CodeCandidateSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_id: Literal["code_candidate.v1"] = "code_candidate.v1"
    base_snapshot_id: str = Field(pattern=r"^snap_[0-9a-f]{24}$")
    entrypoint: str = Field(min_length=1)
    factory: str = Field(default="build_model", min_length=1)
    patch_ref: str = Field(min_length=1)
    patch_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    touched_paths: tuple[str, ...] = Field(min_length=1)
    interface: TensorInterfaceSpec

    @field_validator("entrypoint")
    @classmethod
    def validate_entrypoint(cls, value: str) -> str:
        normalized = _safe_relative_path(value)
        if not normalized.endswith(".py"):
            raise ValueError("entrypoint must be a Python source file")
        return normalized

    @field_validator("factory")
    @classmethod
    def validate_factory(cls, value: str) -> str:
        if _SAFE_FACTORY.fullmatch(value) is None:
            raise ValueError("factory must be a Python identifier")
        return value

    @field_validator("touched_paths")
    @classmethod
    def validate_touched_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_safe_relative_path(path) for path in value)
        if len(normalized) != len(set(normalized)):
            raise ValueError("touched_paths must be unique")
        return normalized

    @model_validator(mode="after")
    def require_entrypoint_change(self) -> CodeCandidateSpec:
        if self.entrypoint not in self.touched_paths:
            raise ValueError("entrypoint must be included in touched_paths")
        return self


@dataclass(frozen=True)
class CodeCandidateCheck:
    check_id: str
    passed: bool
    reason: str = ""


@dataclass(frozen=True)
class CodeCandidateReport:
    passed: bool
    checks: tuple[CodeCandidateCheck, ...]

    @property
    def blockers(self) -> tuple[CodeCandidateCheck, ...]:
        return tuple(check for check in self.checks if not check.passed)


def code_candidate_spec_sha256(spec: CodeCandidateSpec) -> str:
    """Hash the complete canonical code-candidate declaration."""
    return stable_hash(spec.model_dump(mode="json"))


def code_candidate_implementation_fingerprint(
    *,
    genome_exact_sha256: str,
    bundle_hash: str,
) -> str:
    """Bind an exact genome to one exact materialization bundle."""
    return code_identity_fingerprint(
        genome_fingerprint=genome_exact_sha256,
        bundle_hash=bundle_hash,
    )


def inspect_code_candidate(
    spec: CodeCandidateSpec,
    *,
    source: str,
    allowed_paths: tuple[str, ...],
    forbidden_paths: tuple[str, ...] = (),
) -> CodeCandidateReport:
    """Perform filesystem-free path and AST checks before sandbox execution."""
    checks: list[CodeCandidateCheck] = []
    for path in spec.touched_paths:
        allowed = _matches(path, allowed_paths)
        forbidden = _matches(path, forbidden_paths)
        checks.append(
            CodeCandidateCheck(
                check_id=f"path:{path}",
                passed=allowed and not forbidden,
                reason=(
                    "path matches a forbidden pattern"
                    if forbidden
                    else "path is outside allowed patterns"
                    if not allowed
                    else ""
                ),
            )
        )

    try:
        tree = ast.parse(source, filename=spec.entrypoint)
    except SyntaxError as exc:
        checks.append(
            CodeCandidateCheck(
                check_id="python_ast",
                passed=False,
                reason=f"syntax error at line {exc.lineno or 0}: {exc.msg}",
            )
        )
        return CodeCandidateReport(False, tuple(checks))

    factories = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == spec.factory
    ]
    valid_factory = len(factories) == 1 and _valid_factory_signature(factories[0])
    checks.append(
        CodeCandidateCheck(
            check_id="factory",
            passed=valid_factory,
            reason=(
                "entrypoint must define exactly one synchronous factory with one positional config argument"
                if not valid_factory
                else ""
            ),
        )
    )
    return CodeCandidateReport(
        passed=all(check.passed for check in checks),
        checks=tuple(checks),
    )


def _valid_factory_signature(function: ast.FunctionDef) -> bool:
    arguments = function.args
    positional = (*arguments.posonlyargs, *arguments.args)
    return (
        len(positional) == 1
        and arguments.vararg is None
        and arguments.kwarg is None
        and not arguments.kwonlyargs
    )


def _safe_relative_path(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    pure = PurePosixPath(normalized)
    if (
        not normalized
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or "\x00" in normalized
    ):
        raise ValueError("path must be a safe relative POSIX path")
    return pure.as_posix()


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    for raw in patterns:
        pattern = raw.strip().replace("\\", "/")
        if not pattern:
            continue
        prefix = pattern.rstrip("/")
        if fnmatchcase(path, pattern) or path == prefix:
            return True
        if not any(token in pattern for token in "*?[") and path.startswith(prefix + "/"):
            return True
    return False
