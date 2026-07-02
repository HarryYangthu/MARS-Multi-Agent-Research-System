"""JSON Schema validation for Agent / human-authored markdown frontmatter.

Schema files live in `schemas/`. Each artifact's frontmatter must contain
a `schema` field matching one of the registered schema ids
(e.g. `proposal.v1`, `experiment_plan.v1`).
"""
from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema
from jsonschema import Draft202012Validator


def _to_jsonable(obj: Any) -> Any:
    """Coerce YAML-native types (datetime, date, set) into JSON-safe ones.

    YAML auto-parses unquoted ISO timestamps as ``datetime`` objects, but our
    schemas declare those fields as ``string``. We normalize before validation
    so both ``created: 2026-05-04T10:32:00Z`` and ``created: "2026-..."`` work.
    """
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, tuple):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, set):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    return obj

from app.harness.schema.frontmatter_parser import (
    FrontmatterError,
    ParsedDoc,
    parse,
)

SCHEMAS_DIR = Path(__file__).parent / "schemas"

SUPPORTED_SCHEMAS: tuple[str, ...] = (
    "proposal.v1",
    "experiment_plan.v1",
    "code_spec.v1",
    "run_log.v1",
    "diagnosis.v1",
    "feedback_packet.v1",
    "evaluation_report.v1",
    "report.v1",
    "report_bundle.v1",
)


@dataclass
class ValidationError:
    path: str  # JSON-pointer-ish path: "/metrics/primary"
    message: str


@dataclass
class ValidationResult:
    schema_id: str | None
    valid: bool
    errors: list[ValidationError] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    body: str = ""

    def first_error(self) -> str | None:
        return self.errors[0].message if self.errors else None


class SchemaNotFoundError(ValueError):
    """Raised when frontmatter declares an unknown `schema` value."""


@lru_cache(maxsize=None)
def _load_schema(schema_id: str) -> dict[str, Any]:
    if schema_id not in SUPPORTED_SCHEMAS:
        raise SchemaNotFoundError(
            f"unknown schema id '{schema_id}'. supported: {SUPPORTED_SCHEMAS}"
        )
    path = SCHEMAS_DIR / f"{schema_id}.json"
    raw = path.read_text(encoding="utf-8")
    parsed: dict[str, Any] = json.loads(raw)
    return parsed


def get_schema(schema_id: str) -> dict[str, Any]:
    """Public accessor for tests / UI."""
    return _load_schema(schema_id)


def _format_path(error: jsonschema.ValidationError) -> str:
    if error.validator == "required":
        parts = str(error.message).split("'")
        if len(parts) >= 2 and parts[1]:
            return f"/{parts[1]}"
    if not error.absolute_path:
        return "/"
    return "/" + "/".join(str(p) for p in error.absolute_path)


def validate_metadata(
    metadata: dict[str, Any],
    *,
    expected_schema: str | None = None,
) -> ValidationResult:
    """Validate a frontmatter metadata dict.

    If ``expected_schema`` is given, the metadata must declare that schema.
    Otherwise the schema id is taken from ``metadata['schema']``.
    """
    metadata = _to_jsonable(metadata)
    schema_id_raw = metadata.get("schema")
    schema_id = str(schema_id_raw) if schema_id_raw is not None else None

    if not schema_id:
        return ValidationResult(
            schema_id=None,
            valid=False,
            errors=[
                ValidationError(
                    path="/schema",
                    message="frontmatter is missing required 'schema' field",
                )
            ],
            metadata=metadata,
        )

    if expected_schema is not None and schema_id != expected_schema:
        return ValidationResult(
            schema_id=schema_id,
            valid=False,
            errors=[
                ValidationError(
                    path="/schema",
                    message=(
                        f"expected schema '{expected_schema}' but got '{schema_id}'"
                    ),
                )
            ],
            metadata=metadata,
        )

    try:
        schema = _load_schema(schema_id)
    except SchemaNotFoundError as exc:
        return ValidationResult(
            schema_id=schema_id,
            valid=False,
            errors=[ValidationError(path="/schema", message=str(exc))],
            metadata=metadata,
        )

    validator = Draft202012Validator(schema)
    errors_raw = sorted(validator.iter_errors(metadata), key=lambda e: list(e.path))
    errors = [
        ValidationError(path=_format_path(e), message=e.message) for e in errors_raw
    ]
    return ValidationResult(
        schema_id=schema_id,
        valid=not errors,
        errors=errors,
        metadata=metadata,
    )


def validate_document(
    text: str,
    *,
    expected_schema: str | None = None,
) -> ValidationResult:
    """Parse and validate a full markdown document with frontmatter."""
    try:
        doc: ParsedDoc = parse(text)
    except FrontmatterError as exc:
        return ValidationResult(
            schema_id=None,
            valid=False,
            errors=[ValidationError(path="/", message=str(exc))],
        )
    result = validate_metadata(doc.metadata, expected_schema=expected_schema)
    result.body = doc.body
    return result
