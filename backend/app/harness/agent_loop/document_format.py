"""Lossless document-argument compatibility; ordinary tools/reviews stay strict."""
from __future__ import annotations

import json
from typing import Any

from app.harness.agent_loop.protocol import DuplicateJSONKeyError, _finite_float, _reject_constant


def document_arguments(text: str, changes: list[dict[str, Any]]) -> Any:
    """Coalesce only repeated values with identical JSON types and content."""
    normalized: list[dict[str, Any]] = []

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                before = json.dumps(result[key], sort_keys=True, ensure_ascii=False, allow_nan=False)
                after = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
                if before != after:
                    raise DuplicateJSONKeyError(f"conflicting duplicate JSON key: {key}")
                normalized.append({"kind": "identical_duplicate_key", "key": key})
            else:
                result[key] = value
        return result

    try:
        result = json.loads(text, object_pairs_hook=object_pairs,
                            parse_constant=_reject_constant, parse_float=_finite_float)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON {exc.msg} at line {exc.lineno}, column {exc.colno}") from exc
    changes.extend(normalized)
    return result
