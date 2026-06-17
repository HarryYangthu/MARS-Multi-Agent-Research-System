"""Bridge-facing post-training export service."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from app.harness.evaluation.post_training_export import (
    PostTrainingExportOptions,
    read_post_training_export,
    write_post_training_export,
)
from app.harness.runtime.event_bus import EventBus
from app.settings import repo_root
from app.storage.run_store import RunHandle

_DEFAULT_EXPORT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "destination": "events/post_training_export.jsonl",
    "include_drafts": False,
    "include_body_chars": 6000,
    "min_artifact_score": 0.65,
    "allowed_decisions": ["pass", "warn"],
}


def load_post_training_export_options(
    *,
    include_drafts: bool | None = None,
    config_path: Path | None = None,
) -> tuple[bool, PostTrainingExportOptions]:
    path = config_path or repo_root() / "configs" / "evaluation.yaml"
    raw_config = _load_yaml_dict(path)
    export_config = _deep_merge(
        _DEFAULT_EXPORT_CONFIG,
        _as_dict(raw_config.get("post_training_export")),
    )
    enabled = _as_bool(export_config.get("enabled"), True)
    option_include_drafts = (
        include_drafts
        if include_drafts is not None
        else _as_bool(export_config.get("include_drafts"), False)
    )
    return enabled, PostTrainingExportOptions(
        include_drafts=option_include_drafts,
        include_body_chars=_as_int(export_config.get("include_body_chars"), 6000),
        min_artifact_score=_as_float(export_config.get("min_artifact_score"), 0.65),
        allowed_decisions=tuple(
            _as_str_list(export_config.get("allowed_decisions"), ("pass", "warn"))
        ),
        destination=str(
            export_config.get("destination", "events/post_training_export.jsonl")
        ),
    )


async def create_run_post_training_export(
    *,
    run: RunHandle,
    include_drafts: bool | None = None,
    bus: EventBus | None = None,
) -> dict[str, Any]:
    enabled, options = load_post_training_export_options(include_drafts=include_drafts)
    if not enabled:
        raise ValueError("post-training export is disabled by config")
    manifest = write_post_training_export(
        run_root=run.root,
        run_id=run.run_id,
        project=run.project,
        options=options,
    )
    payload = {
        "event": "evaluation.post_training_export_written",
        "run_id": run.run_id,
        "project": run.project,
        "path": manifest["path"],
        "record_count": manifest["record_count"],
        "eligible_count": manifest["eligible_count"],
        "include_drafts": manifest["include_drafts"],
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }
    run.write_event("evaluation_events", payload)
    if bus is not None:
        await bus.publish(f"run.{run.run_id}.evaluation", payload)
    return manifest


def get_run_post_training_export(
    *,
    run: RunHandle,
    preview_limit: int = 5,
) -> dict[str, Any] | None:
    _, options = load_post_training_export_options()
    return read_post_training_export(
        run_root=run.root,
        run_id=run.run_id,
        project=run.project,
        destination=options.destination,
        preview_limit=preview_limit,
    )


def _load_yaml_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    parsed = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return parsed if isinstance(parsed, dict) else {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(_as_dict(out[key]), value)
        else:
            out[key] = value
    return out


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_bool(value: object, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _as_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    return default


def _as_float(value: object, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return float(value)
    return default


def _as_str_list(value: object, default: tuple[str, ...]) -> list[str]:
    if not isinstance(value, list):
        return list(default)
    return [item for item in value if isinstance(item, str)]


__all__ = [
    "create_run_post_training_export",
    "get_run_post_training_export",
    "load_post_training_export_options",
]
