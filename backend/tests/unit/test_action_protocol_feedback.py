"""Check the production feedback against a real, isolated protocol recovery trial."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.harness.agent_loop.protocol import action_protocol_feedback, invalid_output_context, parse_action


def test_actual_json_recovery_keeps_invalid_output_and_verifies_only_format() -> None:
    archive = os.environ.get("MARS_TEST_JSON_ACTION_FORMAT_COMPONENT")
    if not archive:
        pytest.skip("requires the actual single-call JSON actions component archive")
    root = Path(archive)
    hashes = json.loads((root / "artifact_hashes.json").read_text())
    for name, expected in hashes.items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected
    request = json.loads((root / "request.json").read_text())
    old = json.loads((root / "original_request.json").read_text())
    original = json.loads((root / "original_response.json").read_text())
    result = json.loads((root / "result.json").read_text())
    messages = request["wire_request"]["messages"]
    changes = [index for index, (before, after) in enumerate(zip(old["visible"], messages, strict=True))
               if before != after]
    assert changes == request["changed_message_indices"] == [16]
    feedback = action_protocol_feedback(request["original_parse_errors"]["6"])
    assert request["new_feedback"] == "[host validation/review feedback]\n" + feedback
    assert messages[16]["content"] == request["new_feedback"]
    with pytest.raises(ValueError, match="Extra data"):
        parse_action(original["visible"])
    packed_invalid = invalid_output_context(original["visible"])
    assert json.loads(packed_invalid.content.split("\n", 1)[1])["invalid_output"] == original["visible"]
    action = parse_action((root / "raw_output.txt").read_text())
    Draft202012Validator(request["tool_schemas"][action["tool"]]).validate(action["args"])
    assert result["parse_action_valid"] and result["tool_args_schema_valid"]
    assert result["tool_execution_performed"] is False and result["full_agent_acceptance"] is False
    assert result["source_run_updated"] is False and result["immutable_sources_unchanged"] is True
    assert result["sdk_attempts_started"] == result["sdk_attempts_succeeded"] == 1
    assert result["sdk_attempts_failed"] == 0 and result["usage_complete"] is True
    # The model selected another paper. Syntax/schema validity does not establish
    # preservation of the original action, source acceptance, or research success.
    assert action["args"]["sources"][0]["pdf_url"] not in original["visible"]
    assert request["configuration_differences"] == {"max_retries": {"original": 1, "component": 0}}
