"""Replay one recorded evolution with its original model feedback, then review it.

This is a targeted component replay using prior model material, never a fresh
research run. It preserves the original parents/operator and performs no search,
selection, approval, or downstream delivery.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from app.agents.base import RunRequest
from app.agents.idea.discovery.backend import LLMRoleBackend, _reflection_draft, _validate_role_response
from app.agents.idea.discovery.contracts import role_schema
from app.agents.idea.discovery.models import (
    DeepDiscoveryState, DiscoveryContext, EvolutionRequest, HypothesisDraft, stable_id, stable_time,
)
from app.agents.idea.discovery.reflection import reflect_hypotheses
from app.agents.idea.discovery.workflow import build_discovery_context
from app.harness.agent_loop.trace import atomic_json, digest
from app.harness.discovery import HypothesisRecord
from app.harness.llm.model_registry import AgentConfig, get_agent_config
from app.settings import repo_root
from scripts.run_idea_roles_live import (
    LiveRoleCalls, RoleName, RoleScenario, exception_record, public_model,
    require_clean_source, role_config, source_snapshot, utc_now,
)


@dataclass(frozen=True)
class ReplayInputs:
    context: DiscoveryContext
    evolution: EvolutionRequest
    configs: dict[RoleName, AgentConfig]
    parent_hashes: dict[str, str]
    parent_run_id: str


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def request_payload(request: dict[str, Any]) -> dict[str, Any]:
    messages = request.get("messages")
    if not isinstance(messages, list) or len(messages) != 2 or [m.get("role") for m in messages] != ["system", "user"]:
        raise ValueError("recorded role request must have an isolated two-message context")
    prompt = str(messages[1]["content"])
    payload = json.loads(prompt.split("\n", 1)[1])
    if not isinstance(payload, dict):
        raise ValueError("recorded role payload must be an object")
    return payload


def load_parent(parent: Path, *, replay_run_id: str) -> ReplayInputs:
    """Read and verify completed real records without selecting a different parent."""
    hashes: dict[str, str] = {}

    def read(relative: str) -> bytes:
        path = parent / relative
        value = path.read_bytes()
        hashes[relative] = hashlib.sha256(value).hexdigest()
        return value

    def document(relative: str) -> dict[str, Any]:
        value = json.loads(read(relative))
        if not isinstance(value, dict):
            raise ValueError(f"expected object in {relative}")
        return value

    summary = document("summary.json")
    if (summary.get("status") != "completed_component" or summary.get("source_dirty") is not False
            or summary.get("model_requests") != summary.get("model_responses")
            or not summary.get("model_requests")):
        raise ValueError("parent must be a completed clean-source component run, not pending or failed")
    initial = document("input/request.json")
    raw_scenario = read("input/scenario.yaml")
    if hashlib.sha256(raw_scenario).hexdigest() != initial.get("scenario_sha256"):
        raise ValueError("parent scenario hash mismatch")
    scenario = RoleScenario.model_validate(yaml.safe_load(raw_scenario))
    if scenario.model_dump(mode="json", by_alias=True) != initial.get("scenario"):
        raise ValueError("parent scenario differs from recorded input")
    parent_run_id = str(summary["run_id"])
    expected_context = build_discovery_context(request=RunRequest(project=scenario.project,
        user_request=scenario.question, extra={"run_id": parent_run_id}),
        evidence_refs=scenario.evidence_refs, constraints=scenario.constraints)
    if asdict(expected_context) != {**initial["context"],
            "evidence_refs": tuple(initial["context"]["evidence_refs"]),
            "constraints": tuple(initial["context"]["constraints"])}:
        raise ValueError("parent task context differs from its scenario")
    if initial.get("source_commit") != summary.get("source_commit"):
        raise ValueError("parent input and summary disagree about source")
    state = DeepDiscoveryState.model_validate(document("idea/discovery/state.v1.json"))
    if state.run_id != parent_run_id or state.status != "waiting_selection" or "finalize" not in state.completed_stages:
        raise ValueError("parent workflow is not complete and unselected")

    calls: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for folder in sorted((parent / "role_calls").iterdir()):
        request = document(f"role_calls/{folder.name}/request.json")
        response = document(f"role_calls/{folder.name}/response.json")
        record = document(f"role_calls/{folder.name}/record.json")
        if (record.get("status") != "response_received" or record.get("model_dispatched") is not True
                or record.get("request_sha256") != digest(request)
                or record.get("response_sha256") != digest(response)
                or response.get("is_mock") is not False):
            raise ValueError(f"parent call is unfinished, non-real or hash-mismatched: {folder.name}")
        if request.get("role") != record.get("role"):
            raise ValueError("parent role identity mismatch")
        if record.get("prompt_sha256") != digest(request["messages"][1]["content"]):
            raise ValueError("parent prompt hash mismatch")
        calls[folder.name] = (request, response)
    if len(calls) != summary["model_requests"]:
        raise ValueError("parent call count differs from its completed summary")
    events = [json.loads(line) for line in read("role_trace/events.jsonl").decode("utf-8").splitlines()]
    if [event["event_seq"] for event in events] != list(range(1, len(events) + 1)):
        raise ValueError("parent trace sequence is discontinuous")
    for kind in ("model_request", "model_response"):
        rows = [event for event in events if event["kind"] == kind]
        if len(rows) != len(calls) or any(digest(row["visible"]) != row["visible_sha256"] for row in rows):
            raise ValueError("parent trace request/response integrity mismatch")

    original_request, _ = calls["0005_evolution"]
    payload = request_payload(original_request)
    for key, expected in {"task": scenario.question, "project": scenario.project,
                          "evidence_refs": list(scenario.evidence_refs), "constraints": list(scenario.constraints)}.items():
        if payload.get(key) != expected:
            raise ValueError(f"original evolution {key} differs from parent scenario")
    requests = payload["requests"]
    if len(requests) != 1 or requests[0]["round_index"] != 1:
        raise ValueError("this replay requires one recorded first-round evolution request")
    actual = requests[0]
    parents = tuple(HypothesisRecord.model_validate(item) for item in actual["parents"])
    if not parents or any(item.blocked or item.run_id != parent_run_id for item in parents):
        raise ValueError("original parent selection is empty, blocked or belongs to another run")
    by_id = {item.hypothesis_id: item for item in state.hypotheses}
    for item in parents:
        # Elo/cluster/blocked are mutable later-round state; preserve the original snapshot.
        excluded = {"elo", "cluster_id", "blocked"}
        if item.model_dump(exclude=excluded) != by_id[item.hypothesis_id].model_dump(exclude=excluded):
            raise ValueError("original parent method differs from the saved workflow record")
    parent_ids = {item.hypothesis_id for item in parents}
    reflections = tuple(item for item in state.reflections if item.hypothesis_id in parent_ids)
    if {item.hypothesis_id for item in reflections} != parent_ids:
        raise ValueError("parent reflections are missing")
    original_reflections = _validate_role_response(calls["0002_reflection"][1]["text"],
        role="reflection", schema=role_schema("reflection"))["reflections"]
    for review in reflections:
        row = next(item for item in original_reflections if item["hypothesis_id"] == review.hypothesis_id)
        for key, value in asdict(_reflection_draft(row)).items():
            if getattr(review, key) != value:
                raise ValueError("saved parent review differs from the actual model response")
    previous_meta = next(item for item in state.meta_reviews if item.round_index == 0)
    original_meta = _validate_role_response(calls["0004_meta_review"][1]["text"],
        role="meta_review", schema=role_schema("meta_review"))
    if previous_meta.next_round_guidance != tuple(str(value).strip() for value in original_meta["next_round_guidance"]):
        raise ValueError("saved guidance differs from the actual model response")
    evolution = EvolutionRequest(round_index=1, operator=actual["operator"], parents=parents,
        parent_reflections=reflections, previous_meta_review_id=previous_meta.meta_review_id,
        next_round_guidance=previous_meta.next_round_guidance)
    roles: tuple[RoleName, ...] = ("evolution", "reflection")
    configs = {role: role_config(get_agent_config("idea"), scenario, role) for role in roles}
    for role, config in configs.items():
        if public_model(config) != initial["roles"][role] or config.max_retries != 0:
            raise ValueError("replay model settings must match the parent and have zero retries")
    if original_request["model"] != public_model(configs["evolution"]):
        raise ValueError("actual parent model request differs from scenario model settings")
    context = build_discovery_context(request=RunRequest(project=scenario.project,
        user_request=scenario.question, extra={"run_id": replay_run_id}),
        evidence_refs=scenario.evidence_refs, constraints=scenario.constraints)
    return ReplayInputs(context, evolution, configs, hashes, parent_run_id)


def child_record(draft: HypothesisDraft, request: EvolutionRequest, context: DiscoveryContext) -> HypothesisRecord:
    """Use the existing evolution record/ID construction without host-authored text."""
    parent_ids = tuple(item.hypothesis_id for item in request.parents)
    identifier = stable_id("hyp", context.run_id, request.round_index, 0,
                           request.operator, parent_ids, draft.statement)
    return HypothesisRecord(hypothesis_id=identifier, run_id=context.run_id, round_index=request.round_index,
        parent_ids=parent_ids, mechanism=draft.mechanism, statement=draft.statement,
        testable_predictions=draft.testable_predictions, evidence_refs=draft.evidence_refs,
        constraints=draft.constraints, uncertainty=draft.uncertainty, operator=request.operator,
        created_at=stable_time(context.run_id, identifier))


async def run(args: argparse.Namespace) -> int:
    if not math.isfinite(args.max_seconds) or args.max_seconds <= 0:
        raise ValueError("max-seconds must be positive and finite")
    run_id = "idea_feedback_replay_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:6]
    root = (args.runs_root / run_id).resolve()
    root.mkdir(parents=True, exist_ok=False)
    parent = args.parent_run.resolve()
    summary: dict[str, Any] = {"run_id": run_id, "run_root": str(root), "parent_run": str(parent),
        "status": "preparing", "targeted_feedback_replay": True, "prior_model_material": True,
        "component_only": True, "fresh_end_to_end": False, "downstream_delivered": False,
        "scientific_validated": False, "simulation_executed": False, "research_tools_executed": 0,
        "no_new_research": True, "automatic_selection": False, "credential_persisted": False,
        "external_human_review_supplied": False, "feedback_quality_validated": False, "started_at": utc_now()}
    atomic_json(root / "summary.json", summary)
    logger.info("FEEDBACK_REPLAY_ROOT={}", root)
    inputs: ReplayInputs | None = None
    calls: LiveRoleCalls | None = None
    started = time.monotonic()
    code = 1
    try:
        source = source_snapshot(repo_root())
        summary.update(source)
        require_clean_source(source)
        inputs = load_parent(parent, replay_run_id=run_id)
        atomic_json(root / "input" / "request.json", {"parent_run_id": inputs.parent_run_id,
            "parent_hashes": inputs.parent_hashes, "context": asdict(inputs.context),
            "evolution": {**asdict(inputs.evolution), "parents": [p.model_dump(mode="json") for p in inputs.evolution.parents],
                "parent_reflections": [r.model_dump(mode="json") for r in inputs.evolution.parent_reflections]},
            "models": {role: public_model(config) for role, config in inputs.configs.items()}, **source})
        calls = LiveRoleCalls(root, inputs.configs)

        async def complete(role: str, prompt: str) -> str:
            assert calls is not None
            if calls.calls >= 2 or role != ("evolution", "reflection")[calls.calls]:
                raise RuntimeError("targeted replay permits only one evolution and one reflection call")
            return await calls.complete(role, prompt)

        async def replay() -> None:
            assert inputs is not None
            backend = LLMRoleBackend(complete)
            drafts = await backend.evolve(inputs.context, (inputs.evolution,))
            atomic_json(root / "child_drafts.json", [asdict(draft) for draft in drafts])
            children = tuple(child_record(draft, inputs.evolution, inputs.context) for draft in drafts)
            atomic_json(root / "child_records.json", [item.model_dump(mode="json") for item in children])
            reviewed, reviews = await reflect_hypotheses(backend=backend, context=inputs.context, hypotheses=children)
            atomic_json(root / "reviewed_children.json", [item.model_dump(mode="json") for item in reviewed])
            atomic_json(root / "reviews.json", [item.model_dump(mode="json") for item in reviews])
            summary.update(status="completed_targeted_replay", model_review_passed=all(not item.blocked for item in reviewed),
                           child_ids=[item.hypothesis_id for item in children])

        await asyncio.wait_for(replay(), timeout=args.max_seconds)
        code = 0
    except (asyncio.CancelledError, KeyboardInterrupt) as exc:
        summary.update(status="interrupted", exception=exception_record(exc))
    except Exception as exc:
        summary.update(status="failed", exception=exception_record(exc))
        code = 2 if calls is None or calls.requests == 0 else 1
    finally:
        if inputs is not None:
            try:
                summary["parent_unchanged"] = all(file_sha(parent / name) == value for name, value in inputs.parent_hashes.items())
            except OSError:
                summary["parent_unchanged"] = False
            summary["parent_manifest_sha256"] = digest(inputs.parent_hashes)
            if not summary["parent_unchanged"]:
                summary["status"] = "failed_parent_changed"
                code = 1
            if calls is not None and calls.requests:
                sent = request_payload(json.loads((root / "role_calls/0001_evolution/request.json").read_text()))["requests"][0]
                summary["feedback_fields_in_model_request"] = (
                    sent["parent_reflections"] == [item.model_dump(mode="json") for item in inputs.evolution.parent_reflections]
                    and sent["next_round_guidance"] == list(inputs.evolution.next_round_guidance)
                    and sent["previous_meta_review_id"] == inputs.evolution.previous_meta_review_id)
        summary.update(finished_at=utc_now(), duration_seconds=time.monotonic() - started,
            model_requests=calls.requests if calls else 0, model_responses=calls.responses if calls else 0,
            usage=calls.usage if calls else {}, usage_complete=calls.usage_complete if calls else True)
        atomic_json(root / "summary.json", summary)
        logger.info("FEEDBACK_REPLAY_RESULT status={} summary={}", summary["status"], root / "summary.json")
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-run", type=Path, required=True)
    parser.add_argument("--runs-root", type=Path, default=Path("runs/real_idea_roles"))
    parser.add_argument("--max-seconds", type=float, default=600.0)
    try:
        return asyncio.run(run(parser.parse_args()))
    except (ValueError, RuntimeError) as exc:
        logger.error("feedback replay preflight failed: {}", type(exc).__name__)
        return 2


if __name__ == "__main__":
    sys.exit(main())
