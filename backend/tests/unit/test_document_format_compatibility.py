"""Parser/validator contracts; no provider or executed-tool substitutes."""
from copy import deepcopy
import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.agents.idea.protocol import protocol_errors
from app.harness.agent_loop.document_revision import apply_document_revision, revision_feedback, REVISE_DOCUMENT
from app.harness.agent_loop.native_protocol import native_decision, SUBMIT_DOCUMENT
from app.harness.agent_loop.protocol import parse_review
from app.harness.agent_loop.trace import digest
from app.harness.llm.provider_base import Completion, ToolCall
from app.harness.schema.frontmatter_parser import dumps, parse
from backend.tests.unit.test_idea_protocol import proposal


def submitted(arguments: str) -> dict[str, Any]:
    return native_decision(Completion('', 'parser', 'parser', tool_calls=(
        ToolCall('authored-parser-input', SUBMIT_DOCUMENT, arguments),)), (), structured_final=True)


def test_identical_document_fields_are_coalesced_with_audit_receipt() -> None:
    result = submitted('{"metadata":{"flag":true,"flag":true,"nested":{"a":[1,"x"]},'
                       '"nested":{"a":[1,"x"]}},"body":"Authored content"}')
    assert parse(result['final']).metadata == {'flag': True, 'nested': {'a': [1, 'x']}}
    assert result['format_compatibility'] == [
        {'kind': 'identical_duplicate_key', 'key': 'flag'},
        {'kind': 'identical_duplicate_key', 'key': 'nested'}]
    assert 'accept' not in result


@pytest.mark.parametrize('values', ['true,"flag":false', 'true,"flag":1', '1,"flag":1.0',
                                    '{"a":1},"flag":{"a":2}', '[1,2],"flag":[2,1]'])
def test_conflicting_values_and_types_remain_rejected(values: str) -> None:
    with pytest.raises(ValueError, match='conflicting duplicate'):
        submitted('{"metadata":{"flag":' + values + '},"body":"Authored content"}')


def test_ordinary_tool_and_review_duplicates_stay_strict() -> None:
    with pytest.raises(ValueError, match='duplicate'):
        native_decision(Completion('', 'parser', 'parser', tool_calls=(
            ToolCall('authored-parser-input', 'mars_read', '{"path":"x","path":"x"}'),)), ('read',))
    with pytest.raises(ValueError, match='duplicate'):
        parse_review('{"accept":false,"accept":false,"issues":["open issue"],"rationale":"Unresolved"}')


def test_equal_body_and_reordered_object_fields_preserve_all_authored_values() -> None:
    result = submitted('{"metadata":{"data":{"a":1,"b":2},"data":{"b":2,"a":1}},'
                       '"body":"Keep this text","body":"Keep this text"}')
    assert parse(result['final']).metadata == {'data': {'a': 1, 'b': 2}}
    assert parse(result['final']).body.strip() == 'Keep this text'
    assert [x['key'] for x in result['format_compatibility']] == ['data', 'body']


def test_equivalent_revision_paths_and_replace_keep_supplied_content() -> None:
    original = dumps({'method_spec': {'values': [1, 2]}, 'unchanged': 'Keep exactly'}, 'Body')
    edits = {'base_sha256': digest(original), 'operations': [
        {'op': 'replace', 'path': '/method_spec/values/0', 'value': 3},
        {'op': 'set', 'path': '/metadata/method_spec/values', 'value': [3, 4]}]}
    saved = deepcopy(edits)
    result = native_decision(Completion('', 'parser', 'parser', tool_calls=(
        ToolCall('authored-parser-input', REVISE_DOCUMENT, json.dumps(edits)),)), (),
        structured_final=True, allow_revisions=True, candidate=original)
    assert parse(result['final']).metadata == {'method_spec': {'values': [3, 4]}, 'unchanged': 'Keep exactly'}
    assert parse(result['final']).body == parse(original).body
    assert result['revision'] == saved == edits
    assert [x['kind'] for x in result['format_compatibility']] == ['metadata_prefix', 'replace_alias']


@pytest.mark.parametrize('operation', [
    {'op': 'replace', 'path': '/metadata/method_spec/missing', 'value': 1},
    {'op': 'replace', 'path': '/metadata/method_spec/values/2', 'value': 1},
    {'op': 'set', 'path': '/unknown/values', 'value': 1},
    {'op': 'set', 'path': '/metadata', 'value': {}},
    {'op': 'set', 'path': 'method_spec/values', 'value': []},
    {'op': 'set', 'path': '/method_spec/invalid~2key', 'value': []},
    {'op': 'remove', 'path': '/body'},
])
def test_compatibility_cannot_guess_targets_or_partially_apply(operation: dict[str, Any]) -> None:
    original = dumps({'method_spec': {'values': [1, 2]}}, 'Body')
    edits = {'base_sha256': digest(original), 'operations': [
        {'op': 'replace', 'path': '/method_spec/values/0', 'value': 3}, operation]}
    saved = deepcopy(edits)
    changes: list[dict[str, Any]] = []
    with pytest.raises(ValueError):
        apply_document_revision(original, edits, compatibility=changes)
    assert changes == [] and edits == saved
    assert parse(original).metadata['method_spec']['values'] == [1, 2]


def test_data_role_blockers_survive_format_compatibility() -> None:
    metadata = proposal()
    metadata['evaluation_protocol']['objectives']['loss']['data_refs'] = ['held']
    original = dumps(metadata, 'Authored content with a substantive blocker')
    edited = apply_document_revision(original, {'base_sha256': digest(original), 'operations': [
        {'op': 'replace', 'path': '/method_spec/optimizer', 'value': 'Authored optimizer update'}]})
    assert any('training objectives' in error for error in protocol_errors(parse(edited).metadata, required=True))
    feedback = revision_feedback('revision path must be a valid pointer')
    assert 'No rejected revision was applied' in feedback
    assert '/metadata/' in feedback and 'op=set' in feedback and 'pinned content-validation' in feedback


def test_actual_failed_replace_is_parsed_without_changing_saved_run() -> None:
    configured = os.environ.get('MARS_TEST_PROTOCOL_RECOVERY_RUN')
    if not configured:
        pytest.skip('requires the actual failed Idea archive; no successful response is invented')
    root = Path(configured).resolve()
    traces = list((root / 'agent_traces/idea').glob('*/checkpoint.json'))
    path = next(p for p in traces if json.loads(p.read_text()).get('status') == 'protocol_exhausted')
    before = path.read_bytes(), (path.parent / 'events.jsonl').read_bytes()
    state = json.loads(before[0])
    events = [json.loads(row) for row in before[1].decode().splitlines()]
    last = next(row for row in reversed(events) if row['kind'] == 'model_response')
    wire = last['visible']['tool_calls'][0]
    completion = Completion(last['visible']['text'], last['model'], last['provider'], tool_calls=(
        ToolCall(wire['id'], wire['function']['name'], wire['function']['arguments']),))
    decision = native_decision(completion, (), structured_final=True, allow_revisions=True,
                               candidate=state['candidate'], body_field='human_summary')
    assert any(item['kind'] == 'replace_alias' for item in decision['format_compatibility'])
    assert decision['revision']['operations'] == json.loads(wire['function']['arguments'])['operations']
    assert parse(decision['final']).metadata['project'] == parse(state['candidate']).metadata['project']
    assert 'accept' not in decision
    assert (path.read_bytes(), (path.parent / 'events.jsonl').read_bytes()) == before
