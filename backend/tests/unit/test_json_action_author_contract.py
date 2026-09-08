"""Pure schema/prompt/config checks and real CLI preparation; no model/tool substitutes."""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest
import yaml

from app.agents.base import ContextPack, RunRequest
from app.agents.idea.agent import IdeaAgent
from app.agents.idea.research_delegate import research_submission_instruction
from app.agents.idea.research_gap import research_submission_schema
from app.harness.agent_loop.context import pack_context, token_upper_bound
from app.harness.agent_loop.executor import action_instructions, phase_llm_config
from app.harness.agent_loop.native_protocol import INSTRUCTION as NATIVE_INSTRUCTION, native_specs
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.protocol import INSTRUCTION
from app.harness.agent_loop.trace import canonical
from app.harness.llm.model_registry import get_agent_config
from app.harness.llm.openai_provider import DeepSeekProvider
from app.harness.llm.provider_base import LLMConfig, Message
from scripts.run_idea_research_live import author_model_config

ROOT = Path(__file__).resolve().parents[3]
VARIANT = "configs/evaluation/idea_research_thinking_json_real.yaml"
BASELINE = "configs/evaluation/idea_research_delegated_real.yaml"


def scenario(path: str = VARIANT) -> dict[str, Any]:
    value: dict[str, Any] = yaml.safe_load((ROOT / path).read_text())
    return value


def idea(protocol: str) -> IdeaAgent:
    original = get_agent_config("idea")
    return IdeaAgent(agent_config=replace(original, raw={
        **original.raw, "loop": {**original.raw["loop"], "protocol": protocol}}))


def test_both_protocols_retain_the_complete_submission_contract() -> None:
    request = RunRequest("pimc", "Schema contract input", extra={"idea_requirements": scenario()["requirements"]})
    native = idea("native_tools").submission_schema(request)
    authored = idea("json_actions").submission_schema(request)
    assert authored == native and authored is not None
    assert {"method_spec", "evaluation_protocol", "parameter_budget", "research_assessment", "research_links"} <= set(authored["required"])


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["native_tools", "json_actions"])
async def test_json_author_cannot_bypass_expanded_host_schema(protocol: str) -> None:
    # Deliberately incomplete human-authored validation input, not a model answer.
    metadata = {"schema": "proposal.v1", "project": "pimc", "agent": "idea",
                "research_question": "Contract input?", "hypothesis": "Unknown.", "novelty": "Unknown."}
    text = "---\n" + yaml.safe_dump(metadata) + "---\n\nContract input."
    errors = await idea(protocol).validate_candidate(RunRequest("pimc", "Contract input"), text, [])
    assert any("'method_spec' is a required property" in error for error in errors)


def test_json_schema_is_complete_once_and_charged_to_input_budget() -> None:
    schema = research_submission_schema()
    instructions = action_instructions([], native=False, final_schema=schema)
    assert instructions.startswith(INSTRUCTION)
    assert json.loads(instructions.split("Complete final.metadata JSON Schema:\n", 1)[1]) == schema
    assert instructions.count(canonical(schema)) == 1
    assert {branch["properties"]["schema"]["const"] for branch in schema["oneOf"]} == {"research_report.v1", "research_gap.v1"}
    assert "mars_submit_document" not in research_submission_instruction(AgentLoopPolicy(protocol="json_actions"))
    pinned = [Message("system", instructions)]
    limit = token_upper_bound(pinned)
    messages, manifest = pack_context(pinned, [], "", "", budget=limit, observation_chars=1000)
    assert messages == pinned and manifest["estimated_upper_bound_tokens"] == limit
    with pytest.raises(ValueError, match="nothing was silently dropped"):
        pack_context(pinned, [], "", "", budget=limit - 1, observation_chars=1000)


def test_base_prompt_does_not_duplicate_or_substitute_the_expanded_schema() -> None:
    agent = idea("json_actions")
    request = RunRequest("pimc", "Contract input", extra={"idea_requirements": scenario()["requirements"]})
    schema = agent.submission_schema(request)
    assert schema is not None
    messages = agent._messages_for_context(request, ContextPack("Rules", "Project", "Task"), purpose="contract")
    messages.append(Message("system", action_instructions([], native=False, final_schema=schema)))
    joined = "\n".join(message.content for message in messages)
    assert joined.count('"$schema"') == 1
    assert joined.count(canonical(schema)) == 1
    assert "mars_submit_document" not in joined


def test_native_and_unstructured_loop_instructions_stay_unchanged() -> None:
    schema = research_submission_schema()
    assert action_instructions([], native=True, final_schema=schema) == NATIVE_INSTRUCTION
    assert action_instructions([], native=False, final_schema=None) == INSTRUCTION + "\nTools:\n[]"
    assert research_submission_instruction(AgentLoopPolicy(protocol="native_tools")) == "Submit only using mars_submit_document(metadata, body). "


@pytest.mark.parametrize("agent_name,model_key,loop_key", [
    ("idea", "model", "loop"), ("idea_research", "child_model", "child_loop")])
@pytest.mark.parametrize("path", [BASELINE, VARIANT])
def test_real_config_reaches_provider_kwargs_without_provider_execution(
    agent_name: str, model_key: str, loop_key: str, path: str,
) -> None:
    data = scenario(path)
    policy = AgentLoopPolicy.from_mapping(data[loop_key])
    agent_config = author_model_config(get_agent_config(agent_name), data[model_key], policy)
    config = LLMConfig(provider=agent_config.model_provider, model=agent_config.model_name,
                       max_tokens=agent_config.max_tokens, thinking_enabled=agent_config.thinking_enabled,
                       reasoning_effort=agent_config.reasoning_effort)
    native = policy.protocol == "native_tools"
    wire_tools = native_specs([], research_submission_schema()) if native else ()
    phase = phase_llm_config(config, policy, phase="act", native=native,
                             wire_tools=wire_tools, effort_overrides={})
    provider = DeepSeekProvider(api_key="configuration-input-not-a-key", base_url=agent_config.base_url)
    wire = provider._request_kwargs([Message("user", "Configuration serialization input")], phase)
    assert wire["model"] == data[model_key]["name"]
    assert wire["max_tokens"] == data[model_key]["max_tokens"]
    assert wire["extra_body"]["thinking"]["type"] == ("disabled" if native else "enabled")
    if native:
        assert wire["tools"] == list(wire_tools)
        assert "reasoning_effort" not in wire and "response_format" not in wire
    else:
        assert wire["reasoning_effort"] == "high"
        assert wire["response_format"] == {"type": "json_object"}
        assert "tools" not in wire and "parallel_tool_calls" not in wire
    assert provider._client is None


@pytest.mark.parametrize("protocol,thinking,effort", [
    ("native_tools", True, "high"), ("native_tools", False, "low"),
    ("json_actions", False, "high"), ("json_actions", True, None),
    ("json_actions", True, "invalid"), ("json_actions", "false", None),
    ("json_actions", None, None), ("json_actions", 1, "high"),
])
def test_ambiguous_or_unsupported_author_settings_fail_before_execution(
    protocol: str, thinking: Any, effort: Any,
) -> None:
    model = {**scenario()["model"], "thinking": thinking, "reasoning_effort": effort}
    policy = AgentLoopPolicy.from_mapping({"protocol": protocol})
    with pytest.raises(ValueError):
        author_model_config(get_agent_config("idea"), model, policy)


def test_variant_changes_only_explicit_model_protocol_and_call_budgets() -> None:
    variant, baseline = scenario(), scenario(BASELINE)
    for model_key, loop_key, calls in (("model", "loop", 36), ("child_model", "child_loop", 16)):
        assert variant[model_key] == {**baseline[model_key], "name": "deepseek-v4-pro", "thinking": True,
                                      "reasoning_effort": "high", "max_tokens": 32768}
        assert variant[loop_key] == {**baseline[loop_key], "protocol": "json_actions", "max_model_calls": calls}
        variant[model_key] = baseline[model_key]
        variant[loop_key] = baseline[loop_key]
    assert variant == baseline


def test_real_preparation_records_thinking_for_both_authors_without_calls(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(str(ROOT / item) for item in ("", "backend", "posttrain/src", "projects/synthetic_regression/src"))
    result = subprocess.run([sys.executable, "scripts/run_idea_research_live.py", "--prepare-only",
                             "--scenario", VARIANT, "--runs-root", str(tmp_path)],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    request = json.loads(next(tmp_path.rglob("request.json")).read_text())
    for key, calls in (("lead_config", 36), ("child_config", 16)):
        config = request[key]
        assert config["thinking_enabled"] is True and config["reasoning_effort"] == "high"
        assert config["model_name"] == "deepseek-v4-pro" and config["max_tokens"] == 32768
        assert config["loop"]["protocol"] == "json_actions" and config["loop"]["max_model_calls"] == calls
    assert not list(tmp_path.rglob("events.jsonl")) and not list(tmp_path.rglob("*.pdf"))
    summary = json.loads(next(tmp_path.rglob("summary.json")).read_text())
    assert summary["status"] == "prepared" and not summary["material_ready"]


@pytest.mark.parametrize("model_key", ["model", "child_model"])
def test_preparation_rejects_ambiguous_author_settings(tmp_path: Path, model_key: str) -> None:
    data = scenario()
    data[model_key]["thinking"] = False  # Keep high effort to create a real invalid configuration.
    config_path = tmp_path / "invalid_scenario.yaml"
    config_path.write_text(yaml.safe_dump(data))
    runs = tmp_path / "runs"
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(str(ROOT / item) for item in ("", "backend"))
    result = subprocess.run([sys.executable, "scripts/run_idea_research_live.py", "--prepare-only",
                             "--scenario", str(config_path), "--runs-root", str(runs)],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert "non-thinking author must not configure reasoning_effort" in result.stderr
    assert not runs.exists()
