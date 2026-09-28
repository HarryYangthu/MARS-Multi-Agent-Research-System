"""Shared read-only project preflight and task freeze for API/CLI callers."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from pydantic import Field
import yaml

from app.harness.runtime.research_contract import (
    ContractModel, FileFingerprint, ProjectContract, ResearchBudget, ResearchTaskContract, relative_scope,
)
from app.settings import repo_root


class PreflightIssue(ContractModel):
    field: str
    code: str
    message: str


class ProjectPreflight(ContractModel):
    schema_id: str = "project_preflight.v1"
    ready: bool
    project_sha256: str
    issues: tuple[PreflightIssue, ...]
    input_fingerprints: tuple[FileFingerprint, ...]
    # Structural admission is deliberately separate from model/environment tests.
    scope: str = "local_files_and_declared_commands"


class FrozenResearchTask(ContractModel):
    task: ResearchTaskContract
    task_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ProjectPreflightError(ValueError):
    def __init__(self, report: ProjectPreflight) -> None:
        self.report = report
        super().__init__("; ".join(issue.message for issue in report.issues))


class ResearchContractIntegrityError(ValueError):
    """The saved declaration or its independently stored identity disagrees."""


class StaleResearchContractError(ValueError):
    """Declared live files changed after the task was frozen."""


def contract_sha256(contract: ContractModel) -> str:
    payload = json.dumps(contract.model_dump(mode="json"), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_frozen_research_task(value: Any, *, check_live_files: bool = False) -> FrozenResearchTask:
    """Verify content identities; inspecting saved evidence never reads live code."""
    if isinstance(value, FrozenResearchTask):
        value = value.model_dump(mode="json")
    frozen = FrozenResearchTask.model_validate(value)
    if (contract_sha256(frozen.task) != frozen.task_sha256
            or contract_sha256(frozen.task.project) != frozen.task.project_sha256):
        raise ResearchContractIntegrityError("Research contract fingerprint does not match its contents")
    expected_files = set(frozen.task.project.baseline_files)
    expected_files.update(name for command in frozen.task.project.commands for name in command.entrypoint_files)
    fingerprint_paths = [item.path for item in frozen.task.input_fingerprints]
    if len(fingerprint_paths) != len(set(fingerprint_paths)) or set(fingerprint_paths) != expected_files:
        raise ResearchContractIntegrityError("Research contract fingerprints do not match declared source files")
    if check_live_files:
        report = preflight_project(frozen.task.project)
        if not report.ready:
            raise ProjectPreflightError(report)
        if report.input_fingerprints != frozen.task.input_fingerprints:
            raise StaleResearchContractError("Declared source files changed; freeze a new research contract")
    return frozen


def parse_project_contract(value: Any) -> ProjectContract:
    return ProjectContract.model_validate(value)


def load_project_contract(path: Path) -> ProjectContract:
    return parse_project_contract(yaml.safe_load(path.read_text(encoding="utf-8")))


def default_research_budget() -> ResearchBudget:
    raw = yaml.safe_load((repo_root() / "configs/research_defaults.yaml").read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) != {"schema_id", "budget"} or raw["schema_id"] != "research_defaults.v1":
        raise ValueError("Invalid research defaults configuration")
    return ResearchBudget.model_validate(raw["budget"])


def scoped_code_path(root: Path, relative: str) -> Path:
    """Reject symlink components even when their current target lies inside root."""
    name = relative_scope(relative)
    base = root.resolve(strict=True)
    path = base
    for part in Path(name).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("Code scopes and declared files cannot traverse symbolic links")
    if not path.resolve().is_relative_to(base):
        raise ValueError("Path escapes the selected code directory")
    return path


def source_write_allowed(project: ProjectContract, relative: str) -> bool:
    """Baseline files and protected subtrees always take priority over allows."""
    name = relative_scope(relative)
    scoped_code_path(Path(project.paths.code), name)
    def covered(scope: str) -> bool:
        return name == scope or name.startswith(scope + "/")
    return (any(covered(scope) for scope in project.allowed_paths)
            and not any(covered(scope) for scope in (*project.protected_paths, *project.baseline_files)))


def preflight_project(project: ProjectContract) -> ProjectPreflight:
    issues: list[PreflightIssue] = []
    fingerprints: dict[str, FileFingerprint] = {}

    def issue(field: str, code: str, message: str) -> None:
        issues.append(PreflightIssue(field=field, code=code, message=message))

    code = Path(project.paths.code)
    if not code.is_dir():
        issue("paths.code", "missing_directory", "The selected code directory is unavailable")
    for role in ("knowledge", "data"):
        for index, value in enumerate(getattr(project.paths, role)):
            path = Path(value)
            if not path.exists() or not (path.is_dir() or path.is_file()):
                issue(f"paths.{role}.{index}", "missing_input", f"A selected {role} input is unavailable")
            elif not os.access(path, os.R_OK):
                issue(f"paths.{role}.{index}", "unreadable_input", f"A selected {role} input is not readable")
    output = Path(project.paths.output)
    ancestor = output
    while not ancestor.exists() and ancestor != ancestor.parent:
        if ancestor.is_symlink():
            break
        ancestor = ancestor.parent
    if (output.is_symlink() or not ancestor.is_dir() or not os.access(ancestor, os.W_OK)
            or (output.exists() and not output.is_dir())):
        issue("paths.output", "unwritable_output", "Choose an output directory with an existing writable parent")
    output_has_symlink = any(part.is_symlink() for part in (output, *output.parents))
    if output_has_symlink:
        issue("paths.output", "symlink_output", "Choose a canonical output path without symbolic link components")
    if code.is_dir():
        code_root = code.resolve()
        resolved_output = output if output_has_symlink else output.resolve()
        if not output_has_symlink and resolved_output.is_relative_to(code_root):
            relative_output = resolved_output.relative_to(code_root).as_posix()
            if relative_output == "." or any(relative_output == protected or relative_output.startswith(protected + "/")
                    or protected.startswith(relative_output + "/")
                    for protected in (*project.protected_paths, *project.baseline_files)):
                issue("paths.output", "protected_output", "Output must not overwrite the code root or a protected scope")
        for role in ("allowed_paths", "protected_paths"):
            for index, relative in enumerate(getattr(project, role)):
                try:
                    scoped_code_path(code, relative)
                except (OSError, ValueError) as exc:
                    issue(f"{role}.{index}", "unsafe_scope", str(exc))
        files = [("baseline_files", name) for name in project.baseline_files]
        files.extend((f"commands.{command.name}.entrypoint_files", name)
                     for command in project.commands for name in command.entrypoint_files)
        for field, relative in files:
            try:
                path = scoped_code_path(code, relative)
                if not path.is_file():
                    raise ValueError("Declared source file is unavailable")
                with path.open("rb") as stream:
                    value = hashlib.file_digest(stream, "sha256").hexdigest()
                fingerprints[relative] = FileFingerprint(path=relative, sha256=value)
            except (OSError, ValueError) as exc:
                issue(field, "invalid_source_file", str(exc))
    for command in project.commands:
        executable = Path(command.executable)
        if not executable.is_file() or not os.access(executable, os.X_OK):
            issue(f"commands.{command.name}.executable", "missing_executable", "The selected environment executable is unavailable")
        if code.is_dir():
            try:
                cwd = code.resolve() if command.cwd == "." else scoped_code_path(code, command.cwd)
                if not cwd.is_dir():
                    raise ValueError("Command working directory is unavailable")
            except (OSError, ValueError) as exc:
                issue(f"commands.{command.name}.cwd", "invalid_working_directory", str(exc))
    if project.execution.kind == "ssh":
        issue("execution", "remote_preflight_pending", "SSH environment verification is required before admission (work package F)")
    elif project.execution.device == "gpu":
        issue("execution.device", "gpu_preflight_pending", "GPU capability verification is required before admission")
    return ProjectPreflight(ready=not issues, project_sha256=contract_sha256(project), issues=tuple(issues),
                            input_fingerprints=tuple(fingerprints[key] for key in sorted(fingerprints)))


def freeze_research_task(project: ProjectContract, *, goal: str, mode: str,
                         budget: ResearchBudget) -> FrozenResearchTask:
    # Revalidate callers' objects: model_copy(update=...) is not a validation boundary.
    project = ProjectContract.model_validate(project.model_dump(mode="json"))
    budget = ResearchBudget.model_validate(budget.model_dump(mode="json"))
    report = preflight_project(project)
    if not report.ready:
        raise ProjectPreflightError(report)
    task = ResearchTaskContract.model_validate({"goal": goal, "mode": mode, "project": project,
        "budget": budget, "project_sha256": report.project_sha256,
        "input_fingerprints": report.input_fingerprints})
    return FrozenResearchTask(task=task, task_sha256=contract_sha256(task))
