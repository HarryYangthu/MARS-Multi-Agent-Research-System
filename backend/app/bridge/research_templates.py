"""Reuse declarations from saved authority, never old observations or admission."""
from __future__ import annotations

import json
import heapq
from pathlib import Path
import re
import sqlite3
from typing import Any, Literal

from pydantic import Field
import yaml

from app.bridge.research_run_service import load_run_research_contract, validate_saved_run_journal_schema
from app.harness.runtime.project_scope import safe_scope_path
from app.harness.runtime.research_contract import ContractModel, ProjectContract, ResearchBudget
from app.harness.runtime.state_journal import StateJournal
from app.settings import repo_root
from app.storage.run_state_store import RunStateStore
from app.storage.run_store import RunHandle, RunStore


class TemplateUnavailable(ValueError):
    """Saved authority is incomplete; do not derive settings from projections."""


class TemplateMissing(ValueError):
    """No saved contract exists for this identifier."""


class TemplateLimits(ContractModel):
    default_page_size: int = Field(strict=True, gt=0)
    max_page_size: int = Field(strict=True, gt=0)
    max_record_bytes: int = Field(strict=True, gt=0)
    sqlite_timeout_seconds: float = Field(strict=True, gt=0)


def template_limits() -> TemplateLimits:
    value = yaml.safe_load((repo_root() / "configs/research_templates.yaml").read_text(encoding="utf-8"))
    result = TemplateLimits.model_validate(value["research_templates"])
    if result.default_page_size > result.max_page_size:
        raise ValueError("Template page limits are inconsistent")
    return result


class SavedResearchSettings(ContractModel):
    schema_id: Literal["research_settings.v1"] = "research_settings.v1"
    source_run_id: str
    project: ProjectContract
    budget: ResearchBudget
    mode: Literal["bounded_auto", "manual"]
    requires_preflight: Literal[True] = True
    research_started: Literal[False] = False


class SavedResearchSummary(ContractModel):
    run_id: str
    name: str
    project_id: str
    display_name: str
    created_at: str


class SavedResearchPage(ContractModel):
    items: tuple[SavedResearchSummary, ...]
    next_cursor: str | None
    unavailable_count: int


def _identifier(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_.-]+", value) is None or value in {".", ".."}:
        raise ValueError("Invalid saved research identifier")
    return value


def _record(root: Path, relative: str, limits: TemplateLimits) -> dict[str, Any]:
    path = safe_scope_path(root, relative, must_exist=True)
    if not path.is_file() or path.stat().st_size > limits.max_record_bytes:
        raise TemplateUnavailable("Saved settings record is not a bounded ordinary file")
    with path.open("rb") as stream:
        data = stream.read(limits.max_record_bytes + 1)
    if len(data) > limits.max_record_bytes:
        raise TemplateUnavailable("Saved settings record exceeds the configured limit")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise TemplateUnavailable("Saved settings record is not an object")
    return value


def _load(store: RunStore, run_id: str, limits: TemplateLimits) -> tuple[SavedResearchSettings, RunHandle]:
    root = store.runs_root / _identifier(run_id)
    if not root.exists() and not root.is_symlink():
        raise TemplateMissing("Saved research was not found")
    try:
        if root.is_symlink():
            raise TemplateUnavailable("Saved run cannot redirect to another directory")
        metadata = _record(root, "run_meta.json", limits)
        if metadata.get("run_id") != run_id:
            raise TemplateUnavailable("Saved run identity differs")
        run = store.get(run_id)
        if run is None:
            raise TemplateMissing("Saved research was not found")
        # Metadata is not sufficient authority. Missing SQL must not turn a
        # contract run into a reusable legacy configuration.
        marked = metadata.get("research_task_sha256") is not None
        frozen_path = root / "input/research_task.v1.json"
        marked = marked or frozen_path.exists() or frozen_path.is_symlink()
        options_path = root / "input/run_request_options.v1.json"
        if options_path.exists() or options_path.is_symlink():
            options = _record(root, "input/run_request_options.v1.json", limits)
            options_extra = options.get("extra")
            marked = marked or isinstance(options_extra, dict) and options_extra.get("research_task_sha256") is not None
        if not marked and not any((root / name).exists() or (root / name).is_symlink()
                                  for name in ("run_state.authority.json", "run_state.sqlite3")):
            raise TemplateMissing("Run has no reusable frozen research settings")
        _record(root, "run_state.authority.json", limits)
        if marked:
            _record(root, "input/research_task.v1.json", limits)
            _record(root, "input/run_request_options.v1.json", limits)
        database = safe_scope_path(root, "run_state.sqlite3", must_exist=True)
        if not database.is_file():
            raise TemplateUnavailable("Saved authority is not an ordinary database")
        # SQLite mode=ro may create WAL/SHM sidecars. This reader supports only
        # the runtime's rollback-journal database; never ignore a live WAL via
        # immutable=1 or open an unsupported database to discover its mode.
        with database.open("rb") as stream:
            header = stream.read(100)
        if (len(header) != 100 or header[:16] != b"SQLite format 3\x00" or header[18:20] != b"\x01\x01"
                or any(path.exists() or path.is_symlink()
                       for path in (database.with_name(database.name + suffix) for suffix in ("-wal", "-shm", "-journal")))):
            raise TemplateUnavailable("Saved settings require a complete rollback-journal authority without sidecars")
        journal = StateJournal.from_authority(root, run_id=run_id)
        if journal is None:
            raise TemplateUnavailable("Saved authority is unavailable")
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=limits.sqlite_timeout_seconds)
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            validate_saved_run_journal_schema(connection)
            if connection.execute("SELECT run_id,journal_id,schema_version FROM identity WHERE id=1").fetchone() != (run_id, journal.journal_id, 1):
                raise TemplateUnavailable("Saved authority identity differs")
            length = connection.execute("SELECT length(CAST(payload AS BLOB)) FROM run_state WHERE id=1").fetchone()
            if length is None or type(length[0]) is not int or length[0] > limits.max_record_bytes:
                raise TemplateUnavailable("Saved state is unavailable or exceeds the configured limit")
            snapshot = RunStateStore(run)._snapshot(StateJournal._read(connection))
            extra = snapshot.request.get("extra")
            marked = marked or isinstance(extra, dict) and extra.get("research_task_sha256") is not None
            if not marked:
                raise TemplateMissing("Run has no reusable frozen research settings")
            _record(root, "input/research_task.v1.json", limits)
            _record(root, "input/run_request_options.v1.json", limits)
            if (not isinstance(extra, dict) or snapshot.request.get("project") != run.project
                    or snapshot.request.get("task") != run.task or snapshot.request.get("entrypoint") != "pipeline"):
                raise TemplateUnavailable("Saved request identity differs")
            frozen = load_run_research_contract(run, extra)
            if frozen is None or snapshot.request.get("user_request") != frozen.task.goal:
                raise TemplateUnavailable("Saved contract binding is unavailable")
            return SavedResearchSettings(source_run_id=run_id, project=frozen.task.project,
                budget=frozen.task.budget, mode=frozen.task.mode), run
        finally:
            connection.close()
    except (TemplateMissing, TemplateUnavailable):
        raise
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
        raise TemplateUnavailable("Saved research settings cannot be verified") from exc


def load_research_settings(store: RunStore, run_id: str, *, limits: TemplateLimits | None = None) -> SavedResearchSettings:
    """No source access, initialization, projection repair or state transition."""
    return _load(store, run_id, limits or template_limits())[0]


def list_research_settings(store: RunStore, *, cursor: str | None = None, limit: int | None = None,
                           limits: TemplateLimits | None = None) -> SavedResearchPage:
    policy = limits or template_limits()
    size = policy.default_page_size if limit is None else limit
    if not 0 < size <= policy.max_page_size:
        raise ValueError("Saved settings page size is outside configured limits")
    if cursor is not None:
        _identifier(cursor)
    if not store.runs_root.exists():
        return SavedResearchPage(items=(), next_cursor=None, unavailable_count=0)
    names = heapq.nsmallest(size + 1, (path.name for path in store.runs_root.iterdir()
                   if not path.name.startswith(".") and re.fullmatch(r"[A-Za-z0-9_.-]+", path.name)
                   and (cursor is None or path.name > cursor) and (path.is_dir() or path.is_symlink())))
    selected = names[:size]
    items: list[SavedResearchSummary] = []
    unavailable = 0
    for name in selected:
        try:
            settings, run = _load(store, name, policy)
            items.append(SavedResearchSummary(run_id=run.run_id, name=run.task, project_id=settings.project.project_id,
                display_name=settings.project.display_name, created_at=run.created_at))
        except TemplateMissing:
            continue
        except TemplateUnavailable:
            unavailable += 1
    return SavedResearchPage(items=tuple(items), next_cursor=selected[-1] if len(names) > size else None,
                             unavailable_count=unavailable)
