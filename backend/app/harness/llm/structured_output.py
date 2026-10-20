"""Narrow transport cleanup for model-authored text, never machine receipts.

Only enclosing presentation and redundant punctuation are removed. Missing
values, quotes or closing delimiters are not supplied by this module.
"""
from __future__ import annotations

from collections.abc import Callable
import json
import re
from typing import Any


class StructuredOutputError(ValueError):
    """No single complete, unambiguous model-authored object was present."""


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StructuredOutputError('duplicate structured output key: ' + key)
        result[key] = value
    return result


def _invalid_constant(value: str) -> Any:
    raise StructuredOutputError('non-finite structured output number: ' + value)


def _finite_float(value: str) -> float:
    import math
    parsed = float(value)
    if not math.isfinite(parsed):
        raise StructuredOutputError('non-finite structured output number')
    return parsed


def _object_span(text: str, start: int) -> tuple[str, int]:
    """Scan a complete root without treating braces inside strings as syntax."""
    stack: list[str] = []
    parts: list[str] = []
    quoted = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if quoted:
            parts.append(char)
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char in '{[':
            stack.append('}' if char == '{' else ']')
        elif char in '}]':
            if not stack:
                raise StructuredOutputError('unexpected structured output delimiter')
            if char != stack[-1]:
                # Skip only an existing extra closer. The complete original
                # stack must still close later; no delimiter is ever supplied.
                continue
            stack.pop()
            # A comma immediately before a real closer has no value to alter.
            while parts and parts[-1].isspace():
                parts.pop()
            if parts and parts[-1] == ',':
                parts.pop()
            parts.append(char)
            if not stack:
                return ''.join(parts), index + 1
            continue
        parts.append(char)
    raise StructuredOutputError('structured output is incomplete or truncated')


def parse_json_object(
    text: str, *, object_pairs_hook: Callable[[list[tuple[str, Any]]], Any] | None = None,
    parse_constant: Callable[[str], Any] | None = None,
    parse_float: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Read one model object through fences/prose, without guessing its values.

    Callers may retain their stricter duplicate/number hooks. Multiple root
    containers, array wrappers and truncated roots remain invalid.
    """
    value = text.lstrip('\ufeff \t\r\n')
    if len(value) > 4 * 1024 * 1024:
        raise StructuredOutputError('structured output exceeds its text limit')
    containers: list[str] = []
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char in '{[':
            start = index
            candidate, index = _object_span(value, index)
            containers.append(candidate)
            spans.append((start, index))
            if len(containers) > 1:
                raise StructuredOutputError('multiple structured output objects are ambiguous')
        else:
            index += 1
    if len(containers) != 1 or not containers[0].startswith('{'):
        raise StructuredOutputError('expected one complete structured output object')
    outside = value[:spans[0][0]] + value[spans[0][1]:]
    if re.search(r'"(?:[^"\\]|\\.)*"\s*:', outside):
        raise StructuredOutputError('structured output has ambiguous fields outside its root')
    try:
        parsed = json.loads(containers[0], object_pairs_hook=object_pairs_hook or _unique_pairs,
                            parse_constant=parse_constant or _invalid_constant,
                            parse_float=parse_float or _finite_float)
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(f'JSON {exc.msg} at line {exc.lineno}, column {exc.colno}') from exc
    if not isinstance(parsed, dict):
        raise StructuredOutputError('structured output root must be an object')
    return parsed


def normalize_document_output(text: str) -> str:
    """Remove only model document packaging, preserving body/code/YAML bytes."""
    value = text.lstrip('\ufeff \t\r\n')
    lines = value.splitlines(keepends=True)
    last = len(lines) - 1
    while last >= 0 and not lines[last].strip():
        last -= 1
    if (len(lines) >= 2 and re.fullmatch(r'```(?:markdown|md|yaml|yml)?[ \t]*(?:\r?\n)?', lines[0], re.I)
            and last > 0 and lines[last].strip() == '```'):
        value = ''.join(lines[1:last])
        value = value.lstrip('\ufeff \t\r\n')
    # The native document contract checks this first delimiter. Do not normalize
    # the YAML values or Markdown body, whose exact bytes are reviewed and hashed.
    if value.startswith('---\r\n'):
        value = '---\n' + value[5:]
    return value
