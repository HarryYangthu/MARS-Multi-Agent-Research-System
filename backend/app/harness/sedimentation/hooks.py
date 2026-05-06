"""Sedimentation hook fired by the orchestrator after each Agent completes."""
from __future__ import annotations

from typing import Any

from loguru import logger

from app.harness.schema.frontmatter_parser import parse as fm_parse
from app.harness.sedimentation.extractors import run_extractor


def on_agent_completed(
    *, agent: str, project: str, run_id: str, artifact_text: str
) -> dict[str, Any]:
    """Parse the artifact, route through the per-agent extractor."""
    parsed = fm_parse(artifact_text)
    schema = str(parsed.metadata.get("schema") or "")
    written = run_extractor(
        agent=agent,
        project=project,
        run_id=run_id,
        schema=schema,
        text=artifact_text,
        metadata=parsed.metadata,
    )
    logger.info(
        "sedimentation: agent={} project={} run={} schema={} chunks_written={}",
        agent, project, run_id, schema, written,
    )
    return {"agent": agent, "schema": schema, "chunks_written": written}
