"""Convert evaluation findings into self-evolution work items."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_run_candidates(run_root: Path) -> list[dict[str, Any]]:
    path = run_root / "events" / "evaluation_self_evolution_candidates.json"
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def build_suite_self_evolution_export(
    *,
    suite_id: str,
    run_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in run_items:
        for candidate in item.get("self_evolution_candidates", []):
            if isinstance(candidate, dict):
                key = (
                    str(candidate.get("suggested_lever", "human_review")),
                    str(candidate.get("category", "unknown")),
                )
                grouped[key].append(candidate)

    created = datetime.now(tz=timezone.utc).isoformat()
    exports: list[dict[str, Any]] = []
    for (lever, category), candidates in sorted(grouped.items()):
        severity_counts = Counter(str(c.get("severity", "unknown")) for c in candidates)
        evidence_refs: list[str] = []
        run_ids: list[str] = []
        for candidate in candidates:
            run_id = str(candidate.get("run_id", ""))
            if run_id:
                run_ids.append(run_id)
            refs = candidate.get("evidence_refs", [])
            if isinstance(refs, list):
                evidence_refs.extend(str(ref) for ref in refs)
        exports.append(
            {
                "schema": "evaluation_self_evolution_export.v1",
                "suite": suite_id,
                "created": created,
                "suggested_lever": lever,
                "category": category,
                "occurrence_count": len(candidates),
                "affected_runs": sorted(dict.fromkeys(run_ids)),
                "severity_counts": dict(severity_counts),
                "evidence_refs": sorted(dict.fromkeys(evidence_refs))[:20],
                "status": "pending_review",
                "recommended_action": _recommended_action(lever, category),
            }
        )
    return exports


def write_jsonl(path: Path, items: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    return path


def _recommended_action(lever: str, category: str) -> str:
    if lever == "writing_prompt_or_rubric_mutation":
        return "Review writing prompt, report rubric, and claim-support examples before promoting a mutation."
    if lever == "harness_or_observability_regression":
        return "Add or tighten deterministic regression coverage for trajectory, context, and tool audit capture."
    if lever == "run_store_contract_regression":
        return "Add a run-store contract test so required run directories and metadata remain stable."
    if lever == "task_fixture_or_agent_feedback":
        return "Convert this failure into an evaluation task fixture or agent feedback packet."
    return f"Review repeated `{category}` findings and decide whether to create memory, prompt, or regression updates."
