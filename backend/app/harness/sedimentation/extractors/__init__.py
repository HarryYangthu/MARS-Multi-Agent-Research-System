"""Per-Agent extractors that route sedimented content to the right KB zone."""
from __future__ import annotations

from typing import Any, Callable

from app.harness.kb.fingerprint import compute as compute_fingerprint
from app.harness.kb.memory_writer import write_to_zone
from app.harness.sedimentation.asset_metadata import make as make_metadata

ExtractorResult = list[tuple[str, str, dict[str, Any]]]
Extractor = Callable[[str, dict[str, Any], str], ExtractorResult]


def idea_extractor(text: str, metadata: dict[str, Any], run_id: str) -> ExtractorResult:
    out: ExtractorResult = []
    rq = str(metadata.get("research_question", ""))
    if rq:
        out.append(("literature", rq + "\n\n" + text[:2000], {"kind": "research_question"}))
    out.append(("methodology", text[:3000], {"kind": "idea_proposal"}))
    return out


def experiment_extractor(text: str, metadata: dict[str, Any], run_id: str) -> ExtractorResult:
    return [("methodology", text[:3000], {"kind": "experiment_plan"})]


def coding_extractor(text: str, metadata: dict[str, Any], run_id: str) -> ExtractorResult:
    return [("code_assets", text[:3000], {"kind": "code_spec"})]


def execution_extractor(text: str, metadata: dict[str, Any], run_id: str) -> ExtractorResult:
    fp = metadata.get("fingerprint_hash") or compute_fingerprint(
        plan={}, code_spec={}, metrics=metadata.get("metrics", {})
    )
    extra = {"kind": "run_log", "fingerprint_hash": fp}
    return [("run_archive", text[:3000], extra)]


def writing_extractor(text: str, metadata: dict[str, Any], run_id: str) -> ExtractorResult:
    return [("methodology", text[:3000], {"kind": "research_report"})]


REGISTRY: dict[str, Extractor] = {
    "idea": idea_extractor,
    "experiment": experiment_extractor,
    "coding": coding_extractor,
    "execution": execution_extractor,
    "writing": writing_extractor,
}


def run_extractor(
    *, agent: str, project: str, run_id: str, schema: str, text: str, metadata: dict[str, Any]
) -> int:
    extractor = REGISTRY.get(agent)
    if extractor is None:
        return 0
    pieces = extractor(text, metadata, run_id)
    written = 0
    for zone, body, extra in pieces:
        meta = make_metadata(
            project=project, agent=agent, run_id=run_id, schema=schema, extra=extra
        )
        written += write_to_zone(zone=zone, text=body, metadata=meta)
    return written
