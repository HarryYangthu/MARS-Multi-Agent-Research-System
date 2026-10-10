"""Index real archived source-reading observations."""
from __future__ import annotations
from typing import Any

def reading_sources(observations: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for observation in observations:
        if observation.get("tool") != "search.fetch_sources":
            continue
        output = observation.get("output", {})
        if not isinstance(output, dict):
            continue
        for row in output.get("sources", []):
            if not isinstance(row, dict) or not row.get("ok") or not row.get("archive_complete"):
                continue
            source_id = row.get("source_id")
            if isinstance(source_id, str) and source_id:
                result.setdefault(source_id, []).append(row)
    return result
