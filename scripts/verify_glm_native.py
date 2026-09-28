"""Real bounded GLM -> registered file tool -> Observation -> validated artifact probe.

This certifies only the named read tool and native transport, not a research run.
Credentials come from the configured provider environment and are never copied.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import secrets
import sys
from typing import Any

import frontmatter
import jsonschema

from app.harness.agent_loop.executor import LoopInput, NativeAgentLoop
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.trace import atomic_json
from app.harness.llm.model_registry import get_agent_config, select_provider
from app.harness.llm.provider_base import Message
from app.harness.tools.registry import ToolContext, get_registry
from app.settings import repo_root


async def verify(output: Path, *, model_calls: int, max_tokens: int) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    code = output / "input"
    code.mkdir()
    nonce = secrets.token_hex(16)
    source = code / "baseline.py"
    source.write_text(f'VALIDATION_NONCE = "{nonce}"\n', encoding="utf-8")
    fingerprints = {name: hashlib.sha256((repo_root() / name).read_bytes()).hexdigest() for name in (
        "backend/app/harness/llm/openai_provider.py", "backend/app/harness/llm/provider_base.py",
        "backend/app/harness/llm/model_capabilities.py",
        "backend/app/harness/llm/accounting.py", "backend/app/harness/agent_loop/executor.py",
        "configs/agents.yaml", "configs/resources.yaml", "scripts/verify_glm_native.py")}
    agent = get_agent_config("idea")
    if agent.model_provider != "zhipu" or not agent.model_name.lower().startswith("glm-5.3"):
        raise ValueError("The configured Idea provider must be GLM-5.3; no fallback is permitted")
    provider, config = select_provider(replace(agent, max_tokens=max_tokens, max_retries=0))
    schema: dict[str, Any] = {"type": "object", "properties": {
        "schema_id": {"const": "native_tool_probe.v1"}, "nonce": {"type": "string"}},
        "required": ["schema_id", "nonce"], "additionalProperties": False}

    async def validate(text: str, observations: list[dict[str, Any]]) -> list[str]:
        document = frontmatter.loads(text)
        errors = [error.message for error in jsonschema.Draft202012Validator(schema).iter_errors(document.metadata)]
        if document.metadata.get("nonce") != nonce:
            errors.append("The submitted value does not match the actual input file")
        if not any(row.get("tool") == "code.repo_reader" and row.get("ok") for row in observations):
            errors.append("A successful registered code.repo_reader receipt is required")
        return errors

    try:
        result = await NativeAgentLoop().run(LoopInput(
            messages=[Message("user", "Use code.repo_reader to read baseline.py exactly once. "
                "Then submit native_tool_probe.v1 metadata with the nonce from that real file and a short Markdown summary. "
                "Do not guess the nonce. This is a transport diagnostic, not a scientific research result.")],
            provider=provider, config=config, registry=get_registry(), tools=("code.repo_reader",),
            tool_context=ToolContext("glm-native-probe", "synthetic_regression", "idea", project_repo_root=str(code)),
            policy=AgentLoopPolicy(protocol="native_tools", mode="react", native_observation_history=True,
                max_model_calls=model_calls, max_tool_steps=1, max_validation_repairs=1, max_protocol_repairs=1),
            final_schema=schema, trace_root=output / "trace", validate=validate,
        ))
    finally:
        await provider.close()
    artifact = output / "result.md"
    artifact.write_text(result.text, encoding="utf-8")
    events = [json.loads(line) for line in (output / "trace/events.jsonl").read_text().splitlines()]
    responses = [event for event in events if event.get("kind") == "model_response"]
    response_models = sorted({model for event in responses for model in event.get("response_models", [])})
    identity_complete = bool(responses) and all(event.get("response_model_status") == "consistent" for event in responses)
    identity_matches = identity_complete and {model.lower() for model in response_models} == {config.model.lower()}
    checkpoint = json.loads((output / "trace/checkpoint.json").read_text())
    report = {"schema_id": "glm_native_probe_receipt.v1",
        "status": "model_identity_unverified" if result.status == "passed" and not identity_matches else result.status,
        "transport_status": result.status,
        "provider": config.provider, "model": config.model, "scope": "code.repo_reader/native_observation_loop",
        "model_request_limit": model_calls, "output_token_limit": max_tokens,
        "counts": result.counts, "reflection_accepted": result.reflection_accepted,
        "input_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "requested_models": sorted({event["model"] for event in events if event.get("kind") == "model_request"}),
        "actual_response_models": response_models,
        "response_model_statuses": sorted({event.get("response_model_status", "unreported") for event in responses}),
        "response_model_identity_complete": identity_complete,
        "response_model_matches_requested": identity_matches,
        "started_at": events[0]["time"], "finished_at": events[-1]["time"],
        "usage": checkpoint["usage"], "usage_complete": checkpoint["usage_complete"],
        "model_cost_cny": None, "cost_status": "unknown_no_verified_price_table",
        "source_fingerprints": fingerprints, "full_research_accepted": False}
    atomic_json(output / "receipt.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-calls", type=int, required=True)
    parser.add_argument("--max-tokens", type=int, required=True)
    options = parser.parse_args()
    try:
        if not 2 <= options.model_calls <= 4 or not 1 <= options.max_tokens <= 8192:
            raise ValueError("Probe requires 2-4 model calls and at most 8192 output tokens")
        report = asyncio.run(verify(options.output.resolve(), model_calls=options.model_calls, max_tokens=options.max_tokens))
    except Exception as exc:
        # Provider messages and request objects may contain authentication material.
        sys.stderr.write(f"GLM native probe failed: {type(exc).__name__}\n")
        return 1
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
