"""Evaluate the existing Idea role workflow with real models, without enabling deep mode.

This component evaluation performs no search, does not supply missing role schemas,
and never synthesizes, selects, approves or exports a downstream proposal.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.agents.base import RunRequest
from app.agents.idea.discovery import (
    CoScientistWorkflow, DeepDiscoveryConfig, LLMRoleBackend,
    RunLocalDiscoveryStore, build_discovery_context,
)
from app.harness.agent_loop.trace import LoopTrace, atomic_json, digest
from app.harness.llm.model_registry import AgentConfig, get_agent_config, select_provider
from app.harness.llm.openai_provider import public_error_details
from app.harness.llm.provider_base import LLMCompletionError, LLMProvider, Message, llm_call_deadline_seconds
from app.settings import repo_root


RoleName = Literal["generation", "reflection", "pairwise_judge", "evolution", "meta_review"]
ROLE_NAMES: tuple[RoleName, ...] = ("generation", "reflection", "pairwise_judge", "evolution", "meta_review")
DISCOVERY_DEFAULTS = {"initial_hypotheses": 3, "evolution_rounds": 1,
                      "children_per_round": 1, "max_pairwise_matches": 4, "top_k": 1}


class RoleModel(BaseModel):
    """Only non-secret model settings may be supplied or persisted."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    provider: Literal["anthropic", "openai", "qwen", "gemini", "deepseek", "zhipu", "local_vllm", "custom"] | None = None
    name: str | None = Field(default=None, min_length=1)
    max_tokens: int | None = Field(default=None, ge=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    thinking: bool | None = None
    reasoning_effort: Literal["low", "medium", "high", "max"] | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)
    max_retries: int | None = Field(default=None, ge=0, le=3)

    @model_validator(mode="after")
    def explicit_null_is_only_a_reasoning_reset(self) -> RoleModel:
        for name in self.model_fields_set:
            if name != "reasoning_effort" and getattr(self, name) is None:
                raise ValueError(f"{name} must be omitted to inherit; only reasoning_effort permits explicit null")
        return self


class RoleScenario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_id: Literal["idea_roles_scenario.v1"] = Field(default="idea_roles_scenario.v1", alias="schema")
    project: str = Field(min_length=1)
    question: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    model: RoleModel = Field(default_factory=RoleModel)
    roles: dict[RoleName, RoleModel] = Field(default_factory=dict)
    discovery: DeepDiscoveryConfig = Field(default_factory=lambda: DeepDiscoveryConfig.model_validate(DISCOVERY_DEFAULTS))

    @field_validator("discovery", mode="before")
    @classmethod
    def merge_discovery_defaults(cls, value: Any) -> Any:
        return {**DISCOVERY_DEFAULTS, **value} if isinstance(value, dict) else value


def role_config(original: AgentConfig, scenario: RoleScenario, role: RoleName) -> AgentConfig:
    settings = {**scenario.model.model_dump(exclude_unset=True),
                **scenario.roles.get(role, RoleModel()).model_dump(exclude_unset=True)}
    aliases = {"provider": "model_provider", "name": "model_name", "thinking": "thinking_enabled",
               "timeout_seconds": "request_timeout_seconds"}
    changes = {aliases.get(key, key): value for key, value in settings.items()}
    if changes.get("model_provider", original.model_provider) != original.model_provider:
        # Registry defaults and the existing environment resolve the new provider.
        changes.update(api_key_env="", base_url="", base_url_env="")
    return replace(original, name=f"idea.role.{role}", output_schema="", tools=(),
                   debate_enabled=False, **changes)


def public_model(config: AgentConfig) -> dict[str, Any]:
    return {"provider": config.model_provider, "name": config.model_name,
            "max_tokens": config.max_tokens, "temperature": config.temperature,
            "top_p": config.top_p, "thinking": config.thinking_enabled,
            "reasoning_effort": config.reasoning_effort,
            "timeout_seconds": config.request_timeout_seconds, "max_retries": config.max_retries,
            "retry_base_delay_seconds": config.retry_base_delay_seconds}


def role_messages(role: RoleName, prompt: str) -> list[Message]:
    """No shared history or compensating schema/instructions are added here."""
    return [Message("system", role), Message("user", prompt)]


def source_snapshot(directory: Path) -> dict[str, Any]:
    def git(*args: str) -> str:
        return subprocess.check_output(["git", "-C", str(directory), *args], text=True).strip()
    changes = git("status", "--porcelain", "--untracked-files=all")
    return {"source_commit": git("rev-parse", "HEAD"), "source_tree": git("rev-parse", "HEAD^{tree}"),
            "source_dirty": bool(changes), "source_changes": changes.splitlines()}


def require_clean_source(source: dict[str, Any]) -> None:
    if source["source_dirty"]:
        raise RuntimeError("commit source changes before a live component evaluation; no model request made")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def exception_record(exc: BaseException) -> dict[str, Any]:
    record: dict[str, Any] = {"error_type": type(exc).__name__}
    if isinstance(exc, ValidationError):
        # Pydantic's default text includes rejected values, which may be secrets.
        record["errors"] = [{"loc": list(item["loc"]), "type": item["type"]}
                            for item in exc.errors(include_input=False, include_url=False)]
    elif isinstance(exc, yaml.YAMLError):
        record["message"] = "invalid scenario YAML; rejected input values are not retained"
    elif type(exc).__module__.startswith(("openai", "httpx", "httpcore", "anthropic", "google")):
        record["details"] = public_error_details(exc) if isinstance(exc, Exception) else {}
        record["message"] = "provider error; credential-bearing transport details are not retained"
    else:
        record["message"] = str(exc)[:2000]
    if isinstance(exc, LLMCompletionError):
        record.update(reason=exc.reason, usage=exc.usage)
    return record


class LiveRoleCalls:
    """New real provider and two-message context for every role invocation."""

    def __init__(self, root: Path, configs: dict[RoleName, AgentConfig]) -> None:
        self.root = root
        self.configs = configs
        self.trace = LoopTrace(root / "role_trace", "full")
        self.calls = 0
        self.requests = 0
        self.responses = 0
        self.sdk_attempts = 0
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.usage_complete = True

    async def complete(self, role: str, prompt: str) -> str:
        if role not in self.configs:
            raise ValueError("unknown discovery role")
        selected = next(item for item in ROLE_NAMES if item == role)
        self.calls += 1
        index = self.calls
        directory = self.root / "role_calls" / f"{index:04d}_{role}"
        config = self.configs[selected]
        messages = role_messages(selected, prompt)
        request = {"role": role, "call": index, "created_at": utc_now(),
                   "model": public_model(config), "messages": [asdict(message) for message in messages],
                   "context_isolation": "fresh_two_messages", "tools": [], "prompt_unmodified": True}
        atomic_json(directory / "request.json", request)
        record: dict[str, Any] = {"role": role, "call": index, "status": "prepared",
                                  "started_at": request["created_at"], "request_sha256": digest(request),
                                  "prompt_sha256": digest(prompt), "model_dispatched": False}
        atomic_json(directory / "record.json", record)
        self.trace.emit("role_started", {"call": index, "role": role, "request_sha256": digest(request)})
        provider: LLMProvider | None = None
        started = time.monotonic()

        def observe(kind: str, data: dict[str, Any]) -> None:
            if kind == "sdk_attempt_started":
                self.sdk_attempts += 1
            if kind == "sdk_attempt_failed":
                self.usage_complete = False
            self.trace.emit(kind, {"call": index, "role": role, **data})

        try:
            provider, llm = select_provider(config)
            llm.response_schema = None
            llm.json_mode = False
            llm.tools = ()
            llm.attempt_observer = observe
            self.requests += 1
            record.update(status="model_requested", model_dispatched=True, dispatched_at=utc_now())
            atomic_json(directory / "record.json", record)
            self.trace.emit("model_request", {"call": index, "role": role}, visible=request)
            completion = await asyncio.wait_for(provider.complete(messages, llm),
                                                timeout=llm_call_deadline_seconds(llm))
            self.responses += 1
            response = asdict(completion)
            atomic_json(directory / "response.json", response)
            usage = completion.raw.get("usage")
            self._add_usage(usage)
            record.update(status="response_received", response_sha256=digest(response),
                          text_sha256=digest(completion.text), received_at=utc_now(), usage=usage)
            self.trace.emit("model_response", {"call": index, "role": role}, visible=response)
            if completion.is_mock:
                raise RuntimeError("non-real completion rejected")
            if completion.tool_calls:
                raise RuntimeError("the existing role workflow has no tool execution loop")
            return completion.text
        except BaseException as exc:
            if record["model_dispatched"] and record["status"] != "response_received":
                self.usage_complete = False
            if isinstance(exc, LLMCompletionError):
                self._add_usage(exc.usage)
            error = exception_record(exc)
            record.update(status="failed", exception=error)
            atomic_json(directory / "exception.json", error)
            self.trace.emit("role_exception", {"call": index, "role": role, **error})
            raise
        finally:
            if provider is not None:
                try:
                    await provider.close()
                except Exception as exc:
                    record["close_exception"] = exception_record(exc)
            record.update(finished_at=utc_now(), duration_seconds=time.monotonic() - started)
            atomic_json(directory / "record.json", record)

    def _add_usage(self, usage: Any) -> None:
        for key in self.usage:
            value = usage.get(key) if isinstance(usage, dict) else None
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                self.usage[key] += value
            else:
                self.usage_complete = False


async def run(args: argparse.Namespace) -> int:
    if not math.isfinite(args.max_seconds) or args.max_seconds <= 0:
        raise ValueError("max-seconds must be positive and finite")
    run_id = "idea_roles_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:6]
    root = (args.runs_root / run_id).resolve()
    root.mkdir(parents=True, exist_ok=False)
    summary: dict[str, Any] = {"schema_id": "idea_roles_evaluation.v1", "run_id": run_id,
        "run_root": str(root), "status": "preparing", "started_at": utc_now(),
        "component_only": True, "no_new_research": True, "research_tools_executed": 0,
        "scientific_validated": False, "end_to_end_passed": False, "downstream_delivered": False,
        "simulation_executed": False, "credential_persisted": False, "automatic_selection": False,
        "evidence_content_loaded": False, "input_mode": "existing_role_prompt_with_reference_labels_only",
        "response_capture_scope": "visible provider Completion and metadata; no raw HTTP or private reasoning"}
    atomic_json(root / "summary.json", summary)
    logger.info("LIVE_ROLES_RUN_ROOT={}", root)
    calls: LiveRoleCalls | None = None
    started = time.monotonic()
    code = 1
    try:
        source = source_snapshot(repo_root())
        summary.update(source)
        raw_scenario = args.scenario.read_bytes()
        scenario = RoleScenario.model_validate(yaml.safe_load(raw_scenario))
        original = get_agent_config("idea")
        configs = {role: role_config(original, scenario, role) for role in ROLE_NAMES}
        request = RunRequest(project=scenario.project, user_request=scenario.question,
                             extra={"run_id": run_id})
        context = build_discovery_context(request=request, evidence_refs=scenario.evidence_refs,
                                          constraints=scenario.constraints)
        atomic_json(root / "input" / "request.json", {"scenario": scenario.model_dump(mode="json", by_alias=True),
            "scenario_sha256": hashlib.sha256(raw_scenario).hexdigest(), "context": asdict(context),
            "roles": {role: public_model(config) for role, config in configs.items()}, **source})
        (root / "input" / "scenario.yaml").write_bytes(raw_scenario)
        if args.prepare_only:
            summary["status"] = "prepared"
            code = 0
        else:
            require_clean_source(source)
            calls = LiveRoleCalls(root, configs)
            workflow = CoScientistWorkflow(backend=LLMRoleBackend(calls.complete), config=scenario.discovery,
                                           store=RunLocalDiscoveryStore(root / "idea" / "discovery"))
            state = await asyncio.wait_for(workflow.run(context), timeout=args.max_seconds)
            summary.update(status="completed_component", workflow_status=state.status,
                           top_hypothesis_ids=list(state.top_hypothesis_ids))
            code = 0
    except (asyncio.CancelledError, KeyboardInterrupt) as exc:
        summary.update(status="interrupted", exception=exception_record(exc))
    except Exception as exc:
        summary.update(status="failed", exception=exception_record(exc))
        code = 2 if calls is None or calls.requests == 0 else 1
    finally:
        summary.update(finished_at=utc_now(), duration_seconds=time.monotonic() - started,
            model_requests=calls.requests if calls else 0, model_responses=calls.responses if calls else 0,
            sdk_attempts=calls.sdk_attempts if calls else 0,
            usage=calls.usage if calls else {}, usage_complete=calls.usage_complete if calls else True)
        checkpoint = root / "idea" / "discovery" / "checkpoint.v1.json"
        if checkpoint.is_file():
            summary["workflow_checkpoint"] = json.loads(checkpoint.read_text(encoding="utf-8"))
        atomic_json(root / "summary.json", summary)
        logger.info("LIVE_ROLES_RESULT status={} requests={} responses={} summary={}", summary["status"],
                    summary["model_requests"], summary["model_responses"], root / "summary.json")
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--runs-root", type=Path, default=Path("runs/real_idea_roles"))
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--max-seconds", type=float, default=1200.0)
    try:
        return asyncio.run(run(parser.parse_args()))
    except (ValueError, RuntimeError) as exc:
        logger.error("component preflight failed: {}", type(exc).__name__)
        return 2


if __name__ == "__main__":
    sys.exit(main())
