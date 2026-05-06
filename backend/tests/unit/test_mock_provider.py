"""Mock provider regression: every fake metadata MUST validate."""
from __future__ import annotations

import pytest

from app.harness.llm.mock_provider import MockProvider, build_fake_metadata
from app.harness.llm.provider_base import LLMConfig, Message
from app.harness.schema.frontmatter_parser import dumps as fm_dumps
from app.harness.schema.validator import (
    SUPPORTED_SCHEMAS,
    validate_document,
    validate_metadata,
)


@pytest.mark.parametrize("schema_id", SUPPORTED_SCHEMAS)
def test_build_fake_metadata_validates(schema_id: str) -> None:
    md = build_fake_metadata(schema_id, seed="abc123")
    result = validate_metadata(md, expected_schema=schema_id)
    assert result.valid, f"{schema_id} fake invalid: {result.errors}"


@pytest.mark.parametrize("schema_id", SUPPORTED_SCHEMAS)
def test_full_doc_through_provider_validates(schema_id: str) -> None:
    md = build_fake_metadata(schema_id, seed="seed")
    text = fm_dumps(md, "body\n")
    result = validate_document(text, expected_schema=schema_id)
    assert result.valid, result.errors


@pytest.mark.asyncio
@pytest.mark.parametrize("schema_id", SUPPORTED_SCHEMAS)
async def test_provider_complete_returns_validating_doc(schema_id: str) -> None:
    p = MockProvider(default_schema=schema_id)
    cfg = LLMConfig(provider="mock", model="x", response_schema=schema_id)
    completion = await p.complete([Message(role="user", content="hi")], cfg)
    assert completion.is_mock
    res = validate_document(completion.text, expected_schema=schema_id)
    assert res.valid


@pytest.mark.asyncio
async def test_stream_yields_chunks() -> None:
    p = MockProvider(default_schema="proposal.v1")
    cfg = LLMConfig(provider="mock", model="x", response_schema="proposal.v1")
    chunks = []
    async for delta in p.stream([Message(role="user", content="hi")], cfg):
        chunks.append(delta.text)
    assert any(chunks)
    assert chunks[-1] == "" or chunks[-1] is not None
