"""Pure model-text transport cases; no substituted provider or tool results."""
from __future__ import annotations

import json

import pytest

from app.harness.llm.structured_output import StructuredOutputError, normalize_document_output, parse_json_object


@pytest.mark.parametrize('text', [
    '{"value":7}', '\ufeff\r\n{"value":7}',
    '```JSON\r\n{"value":7}\r\n```',
    '核对如下：\n```json\n{"value":7}\n```\n以上。',
    '{"value":7,}', '{"value":7}}]}', '{"value":7]}',
    '处理结果（格式说明）：{"value":7}。',
])
def test_single_complete_object_keeps_actual_values(text: str) -> None:
    assert parse_json_object(text) == {'value': 7}


def test_nested_trailing_commas_and_literal_delimiters_are_not_string_edits() -> None:
    assert parse_json_object('{"rows":[{"literal":"a,} ] { \\\" ",},],}') == {
        'rows': [{'literal': 'a,} ] { " '}]}


def test_stray_nested_closer_is_removed_without_inventing_a_value() -> None:
    value = '{"reply":"继续","actions":[{"tool":"run.existing_model","args":{"managed":false}]}]}'
    assert parse_json_object(value) == {'reply': '继续', 'actions': [
        {'tool': 'run.existing_model', 'args': {'managed': False}}]}


@pytest.mark.parametrize('text', [
    '{"value":}', '{"value":7', '{"value":"unfinished}',
    '{"rows":[1}', '[{"value":7}]', '{"value":1}{"value":2}',
    '```json\n{"value":1}\n```\n```json\n{"value":2}\n```',
    '{"value":1,"value":2}', '{"value":NaN}', '{"value":1e999}', '',
    '{"value":1},"value":2}',
])
def test_no_truncation_value_invention_or_ambiguous_selection(text: str) -> None:
    with pytest.raises(ValueError):
        parse_json_object(text)


def test_original_strict_hooks_remain_authoritative() -> None:
    def reject(pairs: list[tuple[str, object]]) -> dict[str, object]:
        raise LookupError('original domain hook')
    with pytest.raises(LookupError, match='original domain hook'):
        parse_json_object('```json\n{"value":7,}\n```', object_pairs_hook=reject)


def test_document_wrapper_cleanup_preserves_yaml_and_markdown_code_bytes() -> None:
    document = ('---\r\nschema: report.v1\r\nproject: original\r\n---\r\n'
        '# 正文\r\n```python\r\nprint("x\\r\\n")\r\n```\r\n')
    result = normalize_document_output('\ufeff\r\n```MARKDOWN\r\n' + document + '```\r\n')
    assert result == '---\n' + document[5:]
    assert normalize_document_output(result) == result
    assert json.loads('{"body":' + json.dumps(result) + '}')['body'] == result


def test_document_cleanup_does_not_close_frontmatter_or_discard_explanations() -> None:
    value = 'Explanation\n---\nschema: report.v1\n# Body without closing YAML fence\n'
    assert normalize_document_output(value) == value
    assert normalize_document_output('---\nschema: report.v1\n# missing YAML close\n') == (
        '---\nschema: report.v1\n# missing YAML close\n')
