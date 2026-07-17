"""Run lifecycle: create / list / inspect ``runs/<timestamp>_<task>/`` directories.

The 9 mandatory subdirectories from DESIGN §8 are created up-front so any
Phase that writes into a run can rely on the structure existing.
"""
from __future__ import annotations

import builtins
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.settings import repo_root
from app.storage.data_source_store import selection_summary

RUN_SUBDIRS: tuple[str, ...] = (
    "input",
    "context",
    "idea",
    "experiment",
    "coding",
    "execution",
    "diagnosis",
    "writing",
    "hitl",
    "events",
    "memory",
)
TRASH_DIR = ".trash"
TRASH_RETENTION_DAYS = 30

_TASK_SLUG_RE = re.compile(r"[^a-z0-9_]+")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _slugify(task: str) -> str:
    s = task.strip().lower().replace("-", "_").replace(" ", "_")
    s = _TASK_SLUG_RE.sub("", s)
    return s or "task"


def _ts(now: datetime | None = None) -> str:
    now = now or datetime.now(tz=timezone.utc)
    # ISO 8601 short, no separators in time, no colons; matches DESIGN §8 example.
    return now.strftime("%Y-%m-%dT%H%M")


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _parse_dt(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _validate_run_id(run_id: str) -> None:
    if not run_id or "/" in run_id or "\\" in run_id or run_id in {".", ".."}:
        raise ValueError("invalid run_id")
    if not _RUN_ID_RE.fullmatch(run_id):
        raise ValueError("invalid run_id")


@dataclass
class RunHandle:
    run_id: str
    root: Path
    project: str
    task: str
    entrypoint: str
    created_at: str
    meta: dict[str, Any] = field(default_factory=dict)

    def subdir(self, name: str) -> Path:
        if name not in RUN_SUBDIRS:
            raise ValueError(f"unknown run subdir '{name}'. valid: {RUN_SUBDIRS}")
        return self.root / name

    def write_event(self, channel: str, payload: dict[str, Any]) -> None:
        events_dir = self.subdir("events")
        target = events_dir / f"{channel}.jsonl"
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


@dataclass
class TrashRunHandle(RunHandle):
    deleted_at: str = ""
    expires_at: str = ""

    @property
    def days_remaining(self) -> int:
        expires = _parse_dt(self.expires_at)
        if expires is None:
            return 0
        seconds = (expires - _utcnow()).total_seconds()
        return max(0, int((seconds + 86_399) // 86_400))


class RunStore:
    """Filesystem-backed run registry."""

    def __init__(self, runs_root: Path | None = None) -> None:
        self.runs_root = runs_root or (repo_root() / "runs")
        self.runs_root.mkdir(parents=True, exist_ok=True)

    @property
    def trash_root(self) -> Path:
        return self.runs_root / TRASH_DIR

    # ------------------------------------------------------------------ create

    def create(
        self,
        *,
        task: str,
        project: str,
        entrypoint: str = "pipeline",
        config_hash: str = "",
        user_request: str = "",
        data_source: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> RunHandle:
        ts = _ts(now)
        slug = _slugify(task)
        run_id = f"{ts}_{slug}"
        root = self.runs_root / run_id
        if root.exists():
            # collision — append microsecond suffix
            ms = (now or datetime.now(tz=timezone.utc)).strftime("%S%f")
            run_id = f"{ts}_{slug}_{ms}"
            root = self.runs_root / run_id

        root.mkdir(parents=True, exist_ok=False)
        for sub in RUN_SUBDIRS:
            (root / sub).mkdir(exist_ok=True)

        meta: dict[str, Any] = {
            "run_id": run_id,
            "project": project,
            "task": task,
            "entrypoint": entrypoint,
            "config_hash": config_hash,
            "created_at": (now or datetime.now(tz=timezone.utc)).isoformat(),
        }
        if data_source:
            meta["data_source"] = data_source
        (root / "run_meta.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        if user_request or data_source:
            enriched_request = user_request
            if data_source:
                enriched_request = (
                    f"{user_request.rstrip()}\n\n{selection_summary(data_source)}\n"
                    if user_request.strip()
                    else selection_summary(data_source) + "\n"
                )
            (root / "input" / "user_request.md").write_text(
                enriched_request, encoding="utf-8"
            )
        if data_source:
            payload = json.dumps(data_source, indent=2, ensure_ascii=False)
            (root / "input" / "selected_data_source.json").write_text(
                payload,
                encoding="utf-8",
            )
            (root / "context" / "selected_data_source.json").write_text(
                payload,
                encoding="utf-8",
            )
            (root / "context" / "selected_data_source.md").write_text(
                selection_summary(data_source) + "\n",
                encoding="utf-8",
            )

        return RunHandle(
            run_id=run_id,
            root=root,
            project=project,
            task=task,
            entrypoint=entrypoint,
            created_at=meta["created_at"],
            meta=meta,
        )

    # -------------------------------------------------------------------- list

    def list(self) -> list[RunHandle]:
        out: list[RunHandle] = []
        if not self.runs_root.exists():
            return out
        for entry in sorted(self.runs_root.iterdir()):
            if not entry.is_dir() or entry.name == TRASH_DIR:
                continue
            meta_path = entry / "run_meta.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            out.append(
                RunHandle(
                    run_id=str(meta.get("run_id", entry.name)),
                    root=entry,
                    project=str(meta.get("project", "")),
                    task=str(meta.get("task", "")),
                    entrypoint=str(meta.get("entrypoint", "pipeline")),
                    created_at=str(meta.get("created_at", "")),
                    meta=meta,
                )
            )
        return out

    def get(self, run_id: str) -> RunHandle | None:
        _validate_run_id(run_id)
        root = self.runs_root / run_id
        meta_path = root / "run_meta.json"
        if not meta_path.exists():
            return None
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return RunHandle(
            run_id=run_id,
            root=root,
            project=str(meta.get("project", "")),
            task=str(meta.get("task", "")),
            entrypoint=str(meta.get("entrypoint", "pipeline")),
            created_at=str(meta.get("created_at", "")),
            meta=meta,
        )

    # ------------------------------------------------------------------- trash

    def trash(self, run_id: str, *, now: datetime | None = None) -> TrashRunHandle:
        """Move an active run, including all artifacts, into the 30-day trash."""
        _validate_run_id(run_id)
        run = self.get(run_id)
        if run is None:
            trashed = self.get_trashed(run_id)
            if trashed is not None:
                return trashed
            raise FileNotFoundError(run_id)

        deleted_at = now or _utcnow()
        expires_at = deleted_at + timedelta(days=TRASH_RETENTION_DAYS)
        target = self.trash_root / run_id
        if target.exists():
            raise FileExistsError(f"trashed run already exists: {run_id}")

        self.trash_root.mkdir(parents=True, exist_ok=True)
        shutil.move(str(run.root), str(target))
        meta_path = target / "run_meta.json"
        meta = dict(run.meta)
        meta.update(
            {
                "deleted_at": _iso(deleted_at),
                "expires_at": _iso(expires_at),
                "trash_retention_days": TRASH_RETENTION_DAYS,
            }
        )
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        return self._trash_handle(target, meta)

    def list_trashed(
        self, *, purge_expired: bool = True
    ) -> builtins.list[TrashRunHandle]:
        if purge_expired:
            self.purge_expired_trash()
        out: builtins.list[TrashRunHandle] = []
        if not self.trash_root.exists():
            return out
        for entry in sorted(self.trash_root.iterdir()):
            if not entry.is_dir():
                continue
            meta_path = entry / "run_meta.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            out.append(self._trash_handle(entry, meta))
        return out

    def get_trashed(self, run_id: str) -> TrashRunHandle | None:
        _validate_run_id(run_id)
        root = self.trash_root / run_id
        meta_path = root / "run_meta.json"
        if not meta_path.exists():
            return None
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return self._trash_handle(root, meta)

    def restore(self, run_id: str) -> RunHandle:
        _validate_run_id(run_id)
        trashed = self.get_trashed(run_id)
        if trashed is None:
            raise FileNotFoundError(run_id)
        expires = _parse_dt(trashed.expires_at)
        if expires is not None and expires <= _utcnow():
            self.delete_trashed(run_id)
            raise FileNotFoundError(run_id)

        target = self.runs_root / run_id
        if target.exists():
            raise FileExistsError(f"active run already exists: {run_id}")

        shutil.move(str(trashed.root), str(target))
        meta_path = target / "run_meta.json"
        meta = dict(trashed.meta)
        for key in ("deleted_at", "expires_at", "trash_retention_days"):
            meta.pop(key, None)
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        return RunHandle(
            run_id=run_id,
            root=target,
            project=str(meta.get("project", "")),
            task=str(meta.get("task", "")),
            entrypoint=str(meta.get("entrypoint", "pipeline")),
            created_at=str(meta.get("created_at", "")),
            meta=meta,
        )

    def delete_trashed(self, run_id: str) -> None:
        _validate_run_id(run_id)
        root = self.trash_root / run_id
        if not root.exists():
            raise FileNotFoundError(run_id)
        shutil.rmtree(root)

    def purge_expired_trash(self, *, now: datetime | None = None) -> int:
        current = now or _utcnow()
        count = 0
        if not self.trash_root.exists():
            return count
        for item in self.list_trashed(purge_expired=False):
            expires = _parse_dt(item.expires_at)
            if expires is not None and expires <= current:
                shutil.rmtree(item.root)
                count += 1
        return count

    def _trash_handle(self, root: Path, meta: dict[str, Any]) -> TrashRunHandle:
        return TrashRunHandle(
            run_id=str(meta.get("run_id", root.name)),
            root=root,
            project=str(meta.get("project", "")),
            task=str(meta.get("task", "")),
            entrypoint=str(meta.get("entrypoint", "pipeline")),
            created_at=str(meta.get("created_at", "")),
            meta=meta,
            deleted_at=str(meta.get("deleted_at", "")),
            expires_at=str(meta.get("expires_at", "")),
        )
