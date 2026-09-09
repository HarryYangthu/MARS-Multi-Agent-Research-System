"""Explicit service configuration, never a second execution checkpoint.

Only a checked-in profile identifier is accepted. Resolution does not mutate the
global agent registry, environment, YAML, provider, or memory store. Its immutable
receipt records configuration only; the existing loop/checkpoint remains the
authority for execution history, budgets, unknown calls, and research reviews.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
import yaml

from app.harness.agent_loop import AgentLoopPolicy
from app.harness.agent_loop.trace import canonical, digest
from app.harness.llm.model_registry import AgentConfig, get_agent_config
from app.harness.tools.config import ToolConfig, load_tool_configs
from app.harness.tools.registry import get_registry
from app.settings import repo_root


ProfileName = Literal["baseline", "experimental_research_pro_per_insight_v1", "experimental_research_pro_per_insight_v2", "experimental_research_pro_per_insight_v3", "experimental_research_pro_per_insight_v4", "experimental_research_pro_per_insight_v5", "experimental_research_pro_per_insight_v6"]
EXPERIMENTAL_PROFILES = ("experimental_research_pro_per_insight_v1", "experimental_research_pro_per_insight_v2",
                         "experimental_research_pro_per_insight_v3", "experimental_research_pro_per_insight_v4",
                         "experimental_research_pro_per_insight_v5", "experimental_research_pro_per_insight_v6")
PROFILE_FILE = "configs/idea_runtime_profiles.yaml"
SNAPSHOT_FILE = "input/idea_runtime_profile.v1.json"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Author(_StrictModel):
    provider: Literal["deepseek"]
    name: Literal["deepseek-v4-pro"]
    max_tokens: int = Field(ge=1, le=32768)
    temperature: float = Field(ge=0, le=2)
    top_p: float = Field(gt=0, le=1)
    thinking: Literal[True]
    reasoning_effort: Literal["low", "medium", "high", "max"]
    timeout_seconds: float = Field(gt=0, le=360)
    max_retries: int = Field(ge=0, le=1)
    retry_base_delay_seconds: float = Field(ge=0, le=60)
    api_key_env: Literal["DEEPSEEK_API_KEY"]
    base_url: Literal["https://api.deepseek.com/v1"]
    base_url_env: Literal[""]


class _LeadResearch(_StrictModel):
    max_delegations: int = Field(ge=1, le=8)
    per_delegation_min_sources: int | None = Field(default=None, strict=True, ge=1, le=1)


class _ChildResearch(_StrictModel):
    review_mode: Literal["per_insight_then_whole", "per_insight_collect_then_whole"]


class _Lead(_StrictModel):
    model: _Author
    loop: dict[str, Any]
    research: _LeadResearch
    tools: list[str] | None = Field(default=None, min_length=1, max_length=32)


class _Child(_StrictModel):
    model: _Author
    loop: dict[str, Any]
    research: _ChildResearch
    tools: list[str] | None = Field(default=None, min_length=1, max_length=32)


class _Definition(_StrictModel):
    status: Literal["experimental"]
    validated: Literal[False]
    lead: _Lead
    child: _Child


def public_agent_configuration(config: AgentConfig) -> dict[str, Any]:
    """Allowlisted effective fields, not raw YAML or resolved credentials."""
    research = config.raw.get("research", {})
    # Research settings currently consumed by the product, copied explicitly so
    # unknown raw configuration cannot accidentally disclose arbitrary values.
    research_keys = ("max_delegations", "required_tools", "enable_network", "source_downloads",
                     "source_download_limit", "web_search", "excerpt_context_chars", "review_mode",
                     "per_delegation_min_sources")
    if not isinstance(research, dict):
        raise ValueError("research configuration must be a mapping")
    configured_tools = load_tool_configs()
    tool_contracts = {name: asdict(configured_tools.get(name, ToolConfig())) for name in config.tools}
    return {
        "name": config.name, "enabled": config.enabled, "output_schema": config.output_schema,
        "model": {"provider": config.model_provider, "name": config.model_name,
                  "temperature": config.temperature, "max_tokens": config.max_tokens, "top_p": config.top_p,
                  "thinking": config.thinking_enabled, "reasoning_effort": config.reasoning_effort,
                  "timeout_seconds": config.request_timeout_seconds, "max_retries": config.max_retries,
                  "retry_base_delay_seconds": config.retry_base_delay_seconds,
                  "api_key_env": config.api_key_env, "base_url": config.base_url,
                  "base_url_env": config.base_url_env},
        "debate_enabled": config.debate_enabled,
        "loop": asdict(AgentLoopPolicy.from_mapping(config.raw.get("loop", {}))),
        "tools": list(config.tools),
        "enabled_tools": [name for name in config.tools if tool_contracts[name]["enabled"]],
        # Actual schemas/observations still belong to the ordinary loop trace.
        # Binding tool settings also detects changed timeouts/permissions on resume.
        "tool_configuration_sha256": digest(tool_contracts),
        "research": {key: deepcopy(research[key]) for key in research_keys if key in research},
    }


def _profile_tools(original: AgentConfig, declared: list[str] | None) -> tuple[str, ...]:
    """Only an explicit local child profile can alter the registered read tools."""
    if declared is None:
        return original.tools
    if not declared or len(set(declared)) != len(declared):
        raise ValueError("profile tools must be a nonempty list without duplicates")
    registry, configurations = get_registry(), load_tool_configs()
    for name in declared:
        configuration, spec = configurations.get(name), registry.spec(name)
        if (configuration is None or spec is None or not registry.has(name)
                or not configuration.enabled or configuration.runtime_bound or configuration.bridge_only
                or configuration.mutation_level != "read" or spec.policy.mutation_level != "read"
                or original.name not in configuration.allowed_agents or original.name not in spec.policy.allowed_agents
                or configuration.requires_approval or spec.policy.requires_approval):
            raise ValueError("profile tool is not an enabled registered read tool permitted for " + original.name + ": " + name)
    if "search.fetch_sources" not in declared:
        raise ValueError("research profile tools must retain actual PDF fetching")
    return tuple(declared)


def _lead_profile_tools(original: AgentConfig, declared: list[str] | None) -> tuple[str, ...]:
    """Narrow an Idea lead's existing tools without granting new capabilities.

    The exact delegation tool is bound to its real ResearchSession later; its
    catalog/spec is checked here and the loop checks actual registration before
    any model call. Other tools must already have a registered read handler.
    """
    if declared is None:
        return original.tools
    delegate = "idea.research_delegate"
    if (original.name != "idea" or not original.enabled or not declared
            or len(set(declared)) != len(declared) or delegate not in declared
            or not set(declared) <= set(original.tools)):
        raise ValueError("lead profile tools must be a unique subset of the enabled Idea tools retaining delegation")
    registry, configurations = get_registry(), load_tool_configs()
    for name in declared:
        configuration, spec = configurations.get(name), registry.spec(name)
        if (configuration is None or spec is None or not configuration.enabled
                or configuration.bridge_only or spec.bridge_only
                or configuration.mutation_level != "read" or spec.policy.mutation_level != "read"
                or original.name not in configuration.allowed_agents or original.name not in spec.policy.allowed_agents
                or configuration.requires_approval or spec.policy.requires_approval):
            raise ValueError("lead profile tool is not an enabled permitted read tool: " + name)
        if name == delegate:
            if not configuration.runtime_bound:
                raise ValueError("lead delegation must use the existing runtime-bound ResearchSession")
        elif configuration.runtime_bound or not registry.has(name):
            raise ValueError("lead profile tool has no registered read handler: " + name)
    return tuple(declared)


def _overlay(original: AgentConfig, configured: _Lead | _Child) -> AgentConfig:
    model = configured.model
    policy = AgentLoopPolicy.from_mapping(configured.loop)
    declared = set(configured.loop)
    required = set(asdict(policy))
    # This opt-in feature did not exist in the original v1 catalog. Its omitted
    # false default preserves that behavior; all prior settings stay explicit.
    if not policy.author_empty_completion_repair_enabled and "author_empty_completion_repair_enabled" not in declared:
        required.discard("author_empty_completion_repair_enabled")
    if declared != required:
        raise ValueError("experimental profile must explicitly declare every loop setting")
    if policy.protocol != "json_actions" or policy.mode != "reflection" or policy.trace != "full":
        raise ValueError("experimental profile requires json_actions, reflection and full trace")
    if not policy.reflection_thinking_enabled or policy.reflection_reasoning_effort != "high":
        raise ValueError("experimental profile requires high-thinking review")
    raw = deepcopy(dict(original.raw))
    raw["loop"] = asdict(policy)
    raw["research"] = {**raw.get("research", {}), **configured.research.model_dump(exclude_none=True)}
    # Keep raw and typed settings consistent for existing configuration readers.
    raw["model"] = {"provider": model.provider, "model": model.name, "max_tokens": model.max_tokens,
                    "temperature": model.temperature, "top_p": model.top_p,
                    "thinking": {"enabled": model.thinking}, "reasoning_effort": model.reasoning_effort,
                    "request_timeout_seconds": model.timeout_seconds,
                    "retry": {"max_retries": model.max_retries, "base_delay_seconds": model.retry_base_delay_seconds},
                    "api_key_env": model.api_key_env, "base_url": model.base_url, "base_url_env": model.base_url_env}
    raw["debate"] = {**raw.get("debate", {}), "enabled": False}
    tools = (_profile_tools(original, configured.tools) if isinstance(configured, _Child)
             else _lead_profile_tools(original, configured.tools))
    if configured.tools is not None:
        raw["tools"] = list(tools)
    return replace(original, model_provider=model.provider, model_name=model.name, max_tokens=model.max_tokens,
                   temperature=model.temperature, top_p=model.top_p, thinking_enabled=model.thinking,
                   reasoning_effort=model.reasoning_effort, request_timeout_seconds=model.timeout_seconds,
                   max_retries=model.max_retries, retry_base_delay_seconds=model.retry_base_delay_seconds,
                   api_key_env=model.api_key_env, base_url=model.base_url, base_url_env=model.base_url_env,
                   debate_enabled=False, tools=tools, raw=raw)


@dataclass(frozen=True)
class ResolvedIdeaProfile:
    profile_id: str
    lead: AgentConfig
    child: AgentConfig
    source_sha256: str
    configuration_json: str

    def snapshot(self) -> dict[str, Any]:
        current = {"lead": public_agent_configuration(self.lead), "child": public_agent_configuration(self.child)}
        if canonical(current) != self.configuration_json:
            raise ValueError("startup Idea profile configuration was mutated")
        configuration = json.loads(self.configuration_json)
        return {"schema": "idea.runtime_profile.v1", "profile_id": self.profile_id,
                "status": "experimental", "validated": False, "configuration": configuration,
                "configuration_sha256": digest(configuration),
                "source": {"path": PROFILE_FILE, "sha256": self.source_sha256}}


def resolve_idea_profile(selector: str) -> ResolvedIdeaProfile | None:
    """Read one known local profile. Baseline does not acquire any override."""
    if selector == "baseline":
        return None
    if selector not in EXPERIMENTAL_PROFILES:
        raise ValueError("unknown Idea runtime profile; arbitrary paths are not accepted")
    path = repo_root() / PROFILE_FILE
    content = path.read_bytes()
    data = yaml.safe_load(content)
    if (not isinstance(data, dict) or set(data) != {"schema", "profiles"}
            or data["schema"] != "idea.runtime_profiles.v1" or not isinstance(data["profiles"], dict)
            or set(data["profiles"]) != set(EXPERIMENTAL_PROFILES)):
        raise ValueError("invalid local Idea runtime profile catalog")
    definition = _Definition.model_validate(data["profiles"][selector])
    if definition.child.tools is not None and selector not in {"experimental_research_pro_per_insight_v4", "experimental_research_pro_per_insight_v5", "experimental_research_pro_per_insight_v6"}:
        raise ValueError("explicit research tools require a separate v4, v5 or v6 profile")
    if definition.lead.tools is not None and selector not in {"experimental_research_pro_per_insight_v5", "experimental_research_pro_per_insight_v6"}:
        raise ValueError("explicit lead tools require a separate v5 or v6 profile")
    if definition.lead.research.per_delegation_min_sources is not None and selector != "experimental_research_pro_per_insight_v6":
        raise ValueError("single-publication research units require the separate v6 profile")
    original_lead, original_child = get_agent_config("idea"), get_agent_config("idea_research")
    if (not original_lead.enabled or not original_child.enabled
            or original_lead.output_schema != "proposal.v1" or original_child.output_schema != "research_report.v1"
            or "idea.research_delegate" not in original_lead.tools):
        raise ValueError("experimental profile requires the enabled Idea/research contracts and delegation tool")
    lead, child = _overlay(original_lead, definition.lead), _overlay(original_child, definition.child)
    configuration = {"lead": public_agent_configuration(lead), "child": public_agent_configuration(child)}
    return ResolvedIdeaProfile(selector, lead, child, hashlib.sha256(content).hexdigest(), canonical(configuration))


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key in Idea runtime profile receipt")
        result[key] = value
    return result


def bind_profile_snapshot(run_root: Path, profile: ResolvedIdeaProfile | None, *, resume: bool = False) -> dict[str, Any] | None:
    """Bind once before model selection; never rewrite a prior receipt or trace.

    A receipt only checks configuration identity. A matching receipt does not
    make a failed/unknown/terminal checkpoint resumable; the loop still decides.
    Legacy default runs without this opt-in receipt keep their existing behavior.
    """
    path = run_root / SNAPSHOT_FILE
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("Idea runtime profile receipt must be a local regular file")
    if path.exists():
        if profile is None:
            raise ValueError("run used an experimental Idea runtime profile; baseline cannot resume or revise it")
        previous = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
        expected = profile.snapshot()
        if not isinstance(previous, dict) or previous != expected:
            raise ValueError("Idea runtime profile configuration differs from this run; use a new run")
        return previous
    if profile is None:
        return None
    # Never attach a new identity to an old API/CLI execution, including a
    # historical run whose profile receipt was lost. Empty RunStore dirs are OK.
    prior_files = any(item.is_file() for directory in (run_root / "agent_traces", run_root / "idea")
                      if directory.exists() for item in directory.rglob("*"))
    if resume or prior_files or (run_root / "input/request.json").exists() or (run_root / "input/continuation.json").exists():
        raise ValueError("cannot adopt experimental profile for an existing run without its original profile receipt")
    snapshot = profile.snapshot()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation forbids silently overwriting a competing binder. A
    # partial file after a hard crash is intentionally rejected on the next read.
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(canonical(snapshot) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except FileExistsError:
        return bind_profile_snapshot(run_root, profile, resume=resume)
    return snapshot
