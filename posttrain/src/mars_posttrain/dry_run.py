"""CPU/mock post-training dry-run for V2 acceptance.

The dry-run consumes MARS post-training export JSONL records, constructs
traceable preference candidates, scores three reward families, and writes a
small checkpoint manifest. It intentionally does not perform GPU training.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DryRunOptions:
    run_root: Path
    output_root: Path
    export_path: Path | None = None
    include_drafts: bool = False
    max_examples: int = 64


def run_dry_run(options: DryRunOptions) -> dict[str, Any]:
    run_root = options.run_root.resolve()
    output_root = options.output_root.resolve()
    export_path = options.export_path or _ensure_export(
        run_root=run_root,
        include_drafts=options.include_drafts,
    )
    records = _read_jsonl(export_path)
    eligible = [record for record in records if record.get("training_eligible") is True]
    selected = eligible[: max(1, options.max_examples)]
    hitl_refs = _hitl_refs(run_root)
    examples = [_training_example(record, hitl_refs=hitl_refs) for record in selected]
    reward_summary = _reward_summary(examples)

    report_dir = output_root / "reports"
    checkpoint_dir = output_root / "checkpoints"
    report_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = run_root.name
    report_path = report_dir / f"{run_id}.{stamp}.dry_run_report.json"
    checkpoint_path = checkpoint_dir / f"{run_id}.{stamp}.mock_checkpoint.json"

    checkpoint = {
        "schema": "posttrain_checkpoint.v1",
        "mode": "dry_run",
        "run_id": run_id,
        "created": datetime.now(tz=timezone.utc).isoformat(),
        "algorithm": "mock_reward_weighted_selection",
        "example_count": len(examples),
        "mean_reward": reward_summary["mean_composite_reward"],
        "source_export": _relative_or_absolute(export_path, run_root),
    }
    checkpoint_path.write_text(
        json.dumps(checkpoint, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    manifest = {
        "schema": "posttrain_dry_run_report.v1",
        "run_id": run_id,
        "created": checkpoint["created"],
        "mode": "cpu_mock",
        "source_export": _relative_or_absolute(export_path, run_root),
        "record_count": len(records),
        "eligible_count": len(eligible),
        "preference_pair_count": sum(
            1 for example in examples if example["preference_pair"]["pair_constructed"]
        ),
        "reward_summary": reward_summary,
        "checkpoint_path": checkpoint_path.relative_to(output_root).as_posix(),
        "examples_preview": examples[:5],
        "acceptance": {
            "preference_pairs_traceable_to_hitl": bool(hitl_refs),
            "reward_families": [
                "schema_validity",
                "baseline_preservation",
                "downstream_metric",
            ],
            "writes_under_posttrain": True,
            "requires_gpu": False,
        },
    }
    report_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    manifest["report_path"] = report_path.relative_to(output_root).as_posix()
    return manifest


def _ensure_export(*, run_root: Path, include_drafts: bool) -> Path:
    default_path = run_root / "events" / "post_training_export.jsonl"
    if default_path.exists():
        return default_path
    from app.harness.evaluation.post_training_export import (  # type: ignore[import-not-found]
        PostTrainingExportOptions,
        write_post_training_export,
    )

    manifest = write_post_training_export(
        run_root=run_root,
        run_id=run_root.name,
        project=_project_for_run(run_root),
        options=PostTrainingExportOptions(include_drafts=include_drafts),
    )
    return run_root / str(manifest["path"])


def _training_example(record: dict[str, Any], *, hitl_refs: list[str]) -> dict[str, Any]:
    preference = _as_dict(record.get("preference_candidate"))
    labels = _as_dict(record.get("labels"))
    artifact = _as_dict(record.get("artifact"))
    chosen_ref = str(preference.get("chosen_ref") or artifact.get("ref") or "")
    rejected_refs = [
        str(ref)
        for ref in preference.get("sibling_candidate_refs", [])
        if isinstance(ref, str)
    ]
    pair_constructed = bool(chosen_ref and rejected_refs and hitl_refs)
    rewards = _rewards(labels)
    evidence_refs = sorted(
        set(
            hitl_refs
            + _label_refs(labels, "schema_validity")
            + _label_refs(labels, "baseline_preservation")
            + _label_refs(labels, "outcome_passed")
            + [chosen_ref]
            + rejected_refs[:1]
        )
    )
    return {
        "schema": "posttrain_training_example.v1",
        "run_id": str(record.get("run_id", "")),
        "project": str(record.get("project", "")),
        "artifact_ref": chosen_ref,
        "preference_pair": {
            "pair_constructed": pair_constructed,
            "chosen_ref": chosen_ref,
            "rejected_ref": rejected_refs[0] if rejected_refs else "",
            "source_hitl_refs": hitl_refs,
        },
        "rewards": rewards,
        "composite_reward": round(
            rewards["schema_validity"] * 0.4
            + rewards["baseline_preservation"] * 0.3
            + rewards["downstream_metric"] * 0.3,
            6,
        ),
        "evidence_refs": evidence_refs,
    }


def _rewards(labels: dict[str, Any]) -> dict[str, float]:
    return {
        "schema_validity": _label_score(labels, "schema_validity", default=0.0),
        "baseline_preservation": _label_score(
            labels,
            "baseline_preservation",
            default=0.5,
        ),
        "downstream_metric": _label_score(labels, "outcome_passed", default=0.5),
    }


def _label_score(labels: dict[str, Any], name: str, *, default: float) -> float:
    label = _as_dict(labels.get(name))
    score = label.get("score")
    if isinstance(score, int | float):
        return _clamp(float(score))
    value = label.get("value")
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, int | float):
        return _clamp(float(value))
    return default


def _label_refs(labels: dict[str, Any], name: str) -> list[str]:
    label = _as_dict(labels.get(name))
    refs = label.get("evidence_refs")
    if not isinstance(refs, list):
        return []
    return [str(ref) for ref in refs if isinstance(ref, str)]


def _reward_summary(examples: list[dict[str, Any]]) -> dict[str, Any]:
    if not examples:
        return {
            "mean_schema_validity": 0.0,
            "mean_baseline_preservation": 0.0,
            "mean_downstream_metric": 0.0,
            "mean_composite_reward": 0.0,
        }
    return {
        "mean_schema_validity": _mean_reward(examples, "schema_validity"),
        "mean_baseline_preservation": _mean_reward(examples, "baseline_preservation"),
        "mean_downstream_metric": _mean_reward(examples, "downstream_metric"),
        "mean_composite_reward": round(
            sum(float(example["composite_reward"]) for example in examples) / len(examples),
            6,
        ),
    }


def _mean_reward(examples: list[dict[str, Any]], name: str) -> float:
    return round(
        sum(float(_as_dict(example["rewards"])[name]) for example in examples) / len(examples),
        6,
    )


def _hitl_refs(run_root: Path) -> list[str]:
    hitl = run_root / "hitl"
    if not hitl.exists():
        return []
    return [
        path.relative_to(run_root).as_posix()
        for path in sorted(hitl.glob("**/*"))
        if path.is_file()
    ]


def _project_for_run(run_root: Path) -> str:
    request_path = run_root / "input" / "request.json"
    if request_path.exists():
        parsed = json.loads(request_path.read_text(encoding="utf-8"))
        if isinstance(parsed, dict) and isinstance(parsed.get("project"), str):
            return str(parsed["project"])
    return "pimc"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parsed = json.loads(line)
        if isinstance(parsed, dict):
            records.append(parsed)
    return records


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def _relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run MARS posttrain CPU/mock dry-run")
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--output-root", default="posttrain")
    parser.add_argument("--export-path", default="")
    parser.add_argument("--include-drafts", action="store_true")
    parser.add_argument("--max-examples", type=int, default=64)
    args = parser.parse_args(argv)

    manifest = run_dry_run(
        DryRunOptions(
            run_root=Path(args.run_root),
            output_root=Path(args.output_root),
            export_path=Path(args.export_path) if args.export_path else None,
            include_drafts=args.include_drafts,
            max_examples=args.max_examples,
        )
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
