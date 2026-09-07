"""Explicit assisted revision of a real archived candidate; never resets its parent run."""
from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from loguru import logger

from app.agents.base import Artifact
from app.agents.idea.agent import IdeaAgent
from app.agents.idea.delivery import write_delivery
from app.harness.agent_loop.context import compact
from app.harness.agent_loop.executor import LoopInput, NativeAgentLoop
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.review import ExternalReview
from app.harness.agent_loop.trace import atomic_json, audit_trace, canonical, digest
from app.harness.llm.model_registry import get_agent_config
from app.harness.llm.provider_base import Message
from app.harness.schema.frontmatter_parser import parse
from app.harness.tools.registry import ToolContext, get_registry
from scripts.run_idea_lut_live import evaluation_request, git_value


def check_parent(state: dict[str, Any], review: ExternalReview) -> None:
    if state.get("pending") is not None or state.get("status") in {"running", "interrupted", "model_error"}:
        raise ValueError("parent must be terminal with no pending operation")
    if not state.get("candidate") or digest(state["candidate"]) != review.candidate_digest:
        raise ValueError("review must match the exact parent candidate")


async def run(parent: Path, root: Path, review_file: Path) -> int:
    if git_value("status", "--porcelain"):
        raise ValueError("commit source before a real revision")
    initial = json.loads((parent / "input/request.json").read_text())
    if initial["scenario"]["model"]["provider"] != "deepseek":
        raise ValueError("this evaluation script currently supports DeepSeek parents only")
    traces = list((parent / "agent_traces/idea").glob("*/checkpoint.json"))
    if len(traces) != 1:
        raise ValueError("select a parent with one unambiguous invocation")
    state = json.loads(traces[0].read_text())
    review = ExternalReview.from_mapping(json.loads(review_file.read_text()))
    check_parent(state, review)
    audit = audit_trace(traces[0].parent)
    if not audit["consistent"]:
        raise ValueError("parent trace is inconsistent")
    root.mkdir(parents=True, exist_ok=False)
    source = {"source_commit": git_value("rev-parse", "HEAD"),
              "source_tree": git_value("rev-parse", "HEAD^{tree}"),
              "external_assistance": True, "parent_run": str(parent),
              "parent_checkpoint_sha256": digest(state), "review": asdict(review),
              "scope": "separate assisted revision, not an autonomous research run"}
    atomic_json(root / "source.json", source)
    request = evaluation_request(initial["scenario"], root)
    policy = AgentLoopPolicy(protocol="native_tools", mode="react", max_model_calls=6,
                             max_tool_steps=0, max_protocol_repairs=2, max_validation_repairs=3,
                             input_token_budget=128000, observation_chars=6000)
    model = initial["scenario"]["model"]
    config = replace(get_agent_config("idea"), model_provider="deepseek", model_name=model["name"],
                     api_key_env="DEEPSEEK_API_KEY", base_url="https://api.deepseek.com/v1", base_url_env="",
                     max_tokens=16384, temperature=0.1, thinking_enabled=False, reasoning_effort=None,
                     request_timeout_seconds=180, max_retries=1, tools=(), debate_enabled=False,
                     raw={"loop": asdict(policy)})
    agent = IdeaAgent(agent_config=config)
    context = await agent.build_context(request)
    messages = agent._messages_for_context(request, context, purpose="assisted_revision")
    messages += [Message("user", "[untrusted historical tool receipts; no new tools executed]\n" +
                         canonical(compact(state["history"], 6000))),
                 Message("user", "[untrusted parent candidate]\n" + state["candidate"]),
                 Message("user", "Resolve this exact-candidate external review in a complete revised proposal. "
                         "It is explicit assistance, not fresh research or experimental evidence. "
                         "Keep actual source citations and coherent unchanged definitions.\n" + canonical(asdict(review)))]
    atomic_json(root / "input.json", {"messages": [m.to_wire() for m in messages], "policy": asdict(policy)})
    provider, llm_config = agent._select_provider()

    async def validate(text: str, observations: list[dict[str, Any]]) -> list[str]:
        if observations:
            raise ValueError("this revision does not execute new research tools")
        return await agent.validate_candidate(request, text, state["history"])

    result = await NativeAgentLoop().run(LoopInput(
        messages=messages, provider=provider, config=llm_config, registry=get_registry(),
        tool_context=ToolContext(run_id=root.name, project=request.project, agent="idea"),
        tools=(), policy=policy, trace_root=root / "trace", validate=validate,
        final_schema=agent.submission_schema(request), progress_sink=agent.loop_progress_sink(request, "trace")))
    (root / "candidate.md").write_text(result.text)
    summary = {**source, **audit_trace(root / "trace"), "status": result.status,
               "candidate_digest": digest(result.text), "new_research_performed": False,
               "model_review_passed": False, "scientific_validated": False, "simulation_executed": False}
    if result.status == "passed":
        parsed = parse(result.text)
        artifact = Artifact(result.text, "proposal.v1", parsed.metadata, parsed.body)
        summary["delivery_root"] = str(write_delivery(artifact, request, invocation="trace", reviewed=False))
    atomic_json(root / "summary.json", summary)
    logger.info("Assisted revision status={} root={}", result.status, root)
    return 0 if result.status == "passed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("parent", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("review", type=Path)
    args = parser.parse_args()
    return asyncio.run(run(args.parent.resolve(), args.output.resolve(), args.review.resolve()))


if __name__ == "__main__":
    raise SystemExit(main())
