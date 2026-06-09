"""Failure diagnosis for the self-heal feedback loop.

V0 uses a deterministic heuristic (no LLM, zero-dep) that maps a failed node
to a re-route target and writes a schema-valid ``diagnosis.v1`` artifact into
``runs/<id>/diagnosis/``. The orchestrator consumes the returned Decision to
re-route the run. A real LLM-backed diagnosis agent can replace ``diagnose``
later without changing the orchestrator contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from loguru import logger

from app.harness.schema.frontmatter_parser import dumps as fm_dumps
from app.harness.schema.validator import validate_document
from app.storage.run_store import RunHandle

# failed node -> (recommended_action, re-route target node)
_ROUTE: dict[str, tuple[str, str]] = {
    "execution": ("revise_coding", "coding"),
    "coding": ("revise_experiment", "experiment"),
    "experiment": ("retry", "experiment"),
    "idea": ("retry", "idea"),
    "writing": ("retry", "writing"),
}


@dataclass
class Decision:
    action: str       # retry | revise_coding | revise_experiment | manual
    target_node: str
    root_cause: str
    artifact_path: str | None = None


def diagnose(
    run: RunHandle, *, failed_node: str, error: str, attempt: int
) -> Decision:
    action, target = _ROUTE.get(failed_node, ("retry", failed_node))
    root_cause = f"Node '{failed_node}' raised during execution: {error[:300]}"
    metadata = {
        "schema": "diagnosis.v1",
        "project": run.project,
        "agent": "diagnosis",
        "created": datetime.now(tz=timezone.utc).isoformat(),
        "failed_node": failed_node,
        "root_cause": root_cause,
        "recommended_action": action,
        "target_node": target,
        "attempt": attempt,
        "confidence": 0.6,
        "evidence": [error[:300]],
    }
    body = (
        f"# Diagnosis (attempt {attempt})\n\n"
        f"**Failed node:** `{failed_node}`\n\n"
        f"**Root cause:** {root_cause}\n\n"
        f"**Recommended action:** `{action}` → re-route to `{target}`.\n"
    )
    text = fm_dumps(metadata, body)

    artifact_path: str | None = None
    # Schema spine: only persist if it validates (it should, by construction).
    if validate_document(text, expected_schema="diagnosis.v1").valid:
        path = run.subdir("diagnosis") / f"diagnosis_{failed_node}_a{attempt}.v1.md"
        path.write_text(text, encoding="utf-8")
        artifact_path = str(path)
    else:  # pragma: no cover — defensive
        logger.error("diagnosis artifact failed schema validation for {}", failed_node)

    return Decision(
        action=action,
        target_node=target,
        root_cause=root_cause,
        artifact_path=artifact_path,
    )
