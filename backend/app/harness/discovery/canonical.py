"""Deterministic JSON canonicalisation and hashing for discovery records."""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel


def canonical_value(value: Any) -> Any:
    """Return a JSON-safe value with deterministic ordering and float markers."""
    if isinstance(value, BaseModel):
        return canonical_value(value.model_dump(mode="json"))
    if isinstance(value, Enum):
        return canonical_value(value.value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return {"__non_finite_float__": "nan"}
        if math.isinf(value):
            return {"__non_finite_float__": "inf" if value > 0 else "-inf"}
        return 0.0 if value == 0.0 else value
    if isinstance(value, Mapping):
        return {
            str(key): canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, set | frozenset):
        normalized = [canonical_value(item) for item in value]
        return sorted(normalized, key=canonical_json)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [canonical_value(item) for item in value]
    raise TypeError(f"value of type {type(value).__name__} is not canonically serializable")


def canonical_json(value: Any) -> str:
    """Serialize a value using the stable representation used by all hashes."""
    return json.dumps(
        canonical_value(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def stable_hash(value: Any, *, prefix: str = "sha256:") -> str:
    """Hash a canonical value with an explicit algorithm prefix."""
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}{digest}"
