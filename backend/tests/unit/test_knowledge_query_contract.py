"""Every query tool advertises the arguments its real implementation requires."""
from __future__ import annotations

from jsonschema import Draft202012Validator
import pytest

from app.harness.tools.config import tool_config


@pytest.mark.parametrize("tool", ["knowledge.kb_query", "knowledge.experiment_memory",
    "knowledge.code_assets", "knowledge.methodology", "knowledge.run_archive"])
def test_knowledge_query_schema_explains_and_requires_query(tool: str) -> None:
    schema = tool_config(tool).input_schema
    assert schema is not None
    validator = Draft202012Validator(schema)
    assert not validator.is_valid({})
    assert not validator.is_valid({"q": ""})
    assert validator.is_valid({"q": "actual project baseline"})
    assert validator.is_valid({"query": "actual project baseline", "top_k": 3})
