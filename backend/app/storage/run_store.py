"""Run lifecycle: create / list / inspect ``runs/<timestamp>_<task>/`` directories.

The 9 mandatory subdirectories from DESIGN §8 are created up-front so any
Phase that writes into a run can rely on the structure existing.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
    "diagnosis",
    "hitl",
    "events",
    "memory",
)

_TASK_SLUG_RE = re.compile(r"[^a-z0-9_]+")


def _slugify(task: str) -> str:
    s = task.strip().lower().replace("-", "_").replace(" ", "_")
    s = _TASK_SLUG_RE.sub("", s)
    return s or "task"


def _ts(now: datetime | None = None) -> str:
    now = now or datetime.now(tz=timezone.utc)
    # ISO 8601 short, no separators in time, no colons; matches DESIGN §8 example.
    return now.strftime("%Y-%m-%dT%H%M")


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


class RunStore:
    """Filesystem-backed run registry."""

    def __init__(self, runs_root: Path | None = None) -> None:
        self.runs_root = runs_root or (repo_root() / "runs")
        self.runs_root.mkdir(parents=True, exist_ok=True)

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
            if not entry.is_dir():
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
