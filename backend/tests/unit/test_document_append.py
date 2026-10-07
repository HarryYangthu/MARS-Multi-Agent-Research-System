"""Model-authored array appends keep atomic revision and hash binding."""
from copy import deepcopy

import pytest

from app.harness.agent_loop.document_revision import apply_document_revision
from app.harness.agent_loop.trace import digest
from app.harness.schema.frontmatter_parser import dumps, parse


def test_append_supplied_sources_without_replacing_existing_data() -> None:
    candidate = dumps({'schema': 'proposal.v1', 'research_context': {'sources': [{'title': 'first'}]}}, 'Body')
    revision = {'base_sha256': digest(candidate), 'operations': [
        {'op': 'append', 'path': '/metadata/research_context/sources', 'value': {'title': 'second'}},
        {'op': 'append', 'path': '/metadata/research_context/sources', 'value': {'title': 'third'}}]}
    original = deepcopy(revision)
    result = parse(apply_document_revision(candidate, revision))
    assert result.metadata['research_context']['sources'] == [
        {'title': 'first'}, {'title': 'second'}, {'title': 'third'}]
    assert result.body.strip() == 'Body'
    assert parse(candidate).metadata['research_context']['sources'] == [{'title': 'first'}]
    assert revision == original


@pytest.mark.parametrize('path', ['/body', '/metadata/research_context', '/metadata/missing'])
def test_append_cannot_create_missing_parent_or_append_to_nonarray(path: str) -> None:
    candidate = dumps({'research_context': {'sources': []}}, 'Body')
    with pytest.raises(ValueError):
        apply_document_revision(candidate, {'base_sha256': digest(candidate), 'operations': [
            {'op': 'append', 'path': path, 'value': {'title': 'new'}}]})


def test_stale_hash_and_out_of_bounds_set_remain_rejected() -> None:
    candidate = dumps({'sources': [1]}, 'Body')
    for revision in [
        {'base_sha256': '0' * 64, 'operations': [{'op': 'append', 'path': '/metadata/sources', 'value': 2}]},
        {'base_sha256': digest(candidate), 'operations': [{'op': 'set', 'path': '/metadata/sources/1', 'value': 2}]},
    ]:
        with pytest.raises(ValueError):
            apply_document_revision(candidate, revision)
