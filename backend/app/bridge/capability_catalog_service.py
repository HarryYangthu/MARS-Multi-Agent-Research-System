"""Passive owner capability inventory. Listing never grants execution authority."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import inspect
import json
from pathlib import Path
import re
from typing import Any, Literal

import yaml

from app.harness.llm.model_registry import AgentConfig, list_agent_configs
from app.harness.runtime.research_contract import ContractModel
from app.harness.skills.registry import load_selected_skills
from app.harness.tools.config import ToolConfig, _host_config_path, load_tool_configs
from app.harness.tools.registry import ToolFn, ToolRegistry, ToolSpec, get_registry
from app.settings import repo_root


class CapabilityCatalogUnavailable(ValueError):
    """Configuration cannot be safely represented by one passive snapshot."""


class PassiveEvidence(ContractModel):
    dependency_status: Literal["unknown"] = "unknown"
    dependency_reason: Literal["not_probed"] = "not_probed"
    certification_status: Literal["not_run"] = "not_run"
    certification_reason: Literal["receipt_chain_validator_not_installed"] = "receipt_chain_validator_not_installed"


class RolePermission(ContractModel):
    role: str
    configured_enabled: bool | None
    effective_enabled: bool
    configured_tool_granted: bool | None
    effective_tool_granted: bool
    effective_policy_allows_role: bool
    policy_intersection: bool
    # A static intersection does not check arguments, gates or invocation scopes.
    execution_authorized: Literal[False] = False


class ToolCapability(PassiveEvidence):
    name: str
    kind: Literal["tool", "mcp_binding"]
    origin: Literal["registered", "bridge_only", "runtime_bound", "unbound"]
    declared: bool
    configured_enabled: bool | None
    dispatch_enabled: bool
    registered: bool
    effective_spec_present: bool
    effective_transport: Literal["local_python", "mcp_stdio", "unbound"]
    effective_binding_sha256: str | None
    configured_sha256: str | None
    effective_spec_sha256: str | None
    handler_sha256: str | None
    drift_status: Literal["detected", "not_detected", "unknown"]
    drift_fields: tuple[str, ...]
    input_schema_sha256: str | None
    output_schema_sha256: str | None
    mutation_level: Literal["read", "write", "unknown"]
    requires_approval: bool | None
    network: bool | None
    require_isolation: bool | None
    roles: tuple[RolePermission, ...]
    contract_adapter: Literal["requires_host_scope", "unsupported"]
    execution_authorized: Literal[False] = False


class SkillCapability(PassiveEvidence):
    name: str
    kind: Literal["skill"] = "skill"
    declared: Literal[True] = True
    # Skills have no global enabled flag; selection belongs to an invocation.
    enabled: None = None
    selection_required: Literal[True] = True
    definition_valid: bool
    definition_reason: Literal["validated", "invalid_definition"]
    version: str | None
    definition_sha256: str
    content_sha256: str | None
    required_tools: tuple[str, ...]
    contract_adapter: Literal["requires_host_scope", "unsupported", "unknown"]
    execution_authorized: Literal[False] = False


class CapabilityCatalog(ContractModel):
    schema_id: Literal["capability_catalog.v1"] = "capability_catalog.v1"
    context_sha256: str
    registry_scope: Literal["owner_registry_not_invocation_fork"] = "owner_registry_not_invocation_fork"
    configured_tools_sha256: str
    configured_agents_sha256: str
    effective_agents_sha256: str
    agent_configuration_drift: bool
    configured_skills_sha256: str | None
    implementation_sha256: str
    hash_scope: Literal["inventory_sources_and_declarations_not_execution_certification"] = "inventory_sources_and_declarations_not_execution_certification"
    tools: tuple[ToolCapability, ...]
    skills: tuple[SkillCapability, ...]
    research_started: Literal[False] = False
    probes_started: Literal[False] = False
    certification_validation_available: Literal[False] = False


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _identifier(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", value):
        raise CapabilityCatalogUnavailable("Invalid capability identifier")
    return value


def _mapping(path: Path) -> tuple[dict[str, Any], bytes]:
    content = path.read_bytes()
    raw = yaml.safe_load(content)
    if not isinstance(raw, dict):
        raise CapabilityCatalogUnavailable("Configuration must be a mapping")
    return raw, content


def _handler_digest(handler: ToolFn) -> str | None:
    source = inspect.getsourcefile(handler)
    if source is None:
        return None
    try:
        source_digest = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    except OSError:
        return None
    return _digest([handler.__module__, handler.__qualname__, source_digest])


def _mcp_binding(handler: ToolFn | None) -> ToolConfig | None:
    # The existing real MCP factory closes over the configuration captured at
    # registration. Reading that closure must not be confused with tools/list.
    if (handler is None or handler.__module__ != "app.harness.tools.mcp_adapters"
            or handler.__qualname__ != "configured_mcp_handler.<locals>.invoke"):
        return None
    captured = inspect.getclosurevars(handler).nonlocals.get("cfg")
    return captured if isinstance(captured, ToolConfig) else None


def _contract_handlers() -> dict[str, ToolFn]:
    # This is a conservative catalogue projection of prepare_tool's five
    # identity-checked adapters, never an authorization list used by dispatch.
    from app.harness.tools import code
    return {"code.repo_reader": code.repo_reader_tool, "code.write_file": code.write_file_tool,
            "code.apply_patch": code.apply_patch_tool, "code.delete_file": code.delete_file_tool,
            "code.rollback_patch": code.rollback_patch_tool}


def _drift(cfg: ToolConfig | None, spec: ToolSpec | None) -> tuple[str, ...]:
    if cfg is None or spec is None:
        return ()
    # Compare the fields that a fresh registration would change. Empty/or
    # fallback values can retain an older spec, so equality is only
    # "not_detected", never proof that all configuration has been reloaded.
    fields: list[str] = []
    policy = asdict(spec.policy)
    configured = asdict(cfg)
    for key, actual in policy.items():
        desired = configured[key]
        if key != "process_backend":
            desired = desired or actual
        if desired != actual:
            fields.append("policy." + key)
    for key in ("input_schema", "output_schema", "description", "bridge_only"):
        actual = getattr(spec, key)
        if (configured[key] or actual) != actual:
            fields.append(key)
    return tuple(fields)


def _roles(name: str, enabled: bool, spec: ToolSpec | None, agents: list[AgentConfig],
           configured_agents: dict[str, Any]) -> tuple[RolePermission, ...]:
    rows: list[RolePermission] = []
    for agent in sorted(agents, key=lambda item: item.name):
        role = _identifier(agent.name)
        current = configured_agents.get(role)
        body = current if isinstance(current, dict) else None
        policy_allows = spec is not None and (not spec.policy.allowed_agents or role in spec.policy.allowed_agents)
        granted = name in agent.tools
        rows.append(RolePermission(role=role,
            configured_enabled=bool(body.get("enabled", True)) if body is not None else None,
            effective_enabled=agent.enabled,
            configured_tool_granted=name in body.get("tools", []) if body is not None and isinstance(body.get("tools", []), list) else None,
            effective_tool_granted=granted, effective_policy_allows_role=policy_allows,
            policy_intersection=enabled and agent.enabled and granted and policy_allows))
    return tuple(rows)


def _tool(name: str, registry: ToolRegistry, configs: dict[str, ToolConfig], agents: list[AgentConfig],
          configured_agents: dict[str, Any]) -> ToolCapability:
    _identifier(name)
    cfg = configs.get(name)
    # Only registered specs are effective. registry.spec() also synthesizes
    # bridge/runtime declarations and must not be used as execution evidence.
    spec = registry._specs.get(name)
    handler = registry._tools.get(name)
    binding = _mcp_binding(handler)
    enabled = cfg.enabled if cfg is not None else True  # Matches tool_config fallback.
    drift = _drift(cfg, spec)
    if binding is not None and (cfg is None or binding != cfg):
        drift += ("mcp_binding",)
    origin: Literal["registered", "bridge_only", "runtime_bound", "unbound"] = "unbound"
    if cfg is not None and cfg.bridge_only:
        origin = "bridge_only"
    elif cfg is not None and cfg.runtime_bound:
        origin = "runtime_bound"
    elif handler is not None:
        origin = "registered"
    adapter = _contract_handlers().get(name)
    return ToolCapability(name=name, kind="mcp_binding" if binding or cfg and (cfg.mcp_kind or cfg.mcp_tool) else "tool",
        origin=origin, declared=cfg is not None, configured_enabled=cfg.enabled if cfg else None,
        dispatch_enabled=enabled, registered=handler is not None, effective_spec_present=spec is not None,
        effective_transport="mcp_stdio" if binding is not None else "local_python" if handler else "unbound",
        effective_binding_sha256=_digest(asdict(binding)) if binding else None,
        configured_sha256=_digest(asdict(cfg)) if cfg else None,
        effective_spec_sha256=_digest(asdict(spec)) if spec else None,
        handler_sha256=_handler_digest(handler) if handler else None,
        drift_status="detected" if drift else "not_detected" if cfg and spec else "unknown", drift_fields=drift,
        input_schema_sha256=_digest(spec.input_schema) if spec else None,
        output_schema_sha256=_digest(spec.output_schema) if spec else None,
        mutation_level="read" if spec and spec.policy.mutation_level == "read" else "write" if spec and spec.policy.mutation_level == "write" else "unknown",
        requires_approval=spec.policy.requires_approval if spec else None,
        network=spec.policy.network if spec else None, require_isolation=spec.policy.require_isolation if spec else None,
        roles=_roles(name, enabled, spec, agents, configured_agents),
        contract_adapter="requires_host_scope" if adapter is not None and handler is adapter else "unsupported")


def _skills(path: Path, raw: dict[str, Any], tools: tuple[ToolCapability, ...]) -> tuple[SkillCapability, ...]:
    definitions = raw.get("skills")
    if raw.get("version") != 1 or not isinstance(definitions, dict):
        raise CapabilityCatalogUnavailable("Invalid skills registry")
    supported = {item.name for item in tools if item.contract_adapter == "requires_host_scope"}
    out: list[SkillCapability] = []
    for name, definition in sorted(definitions.items()):
        _identifier(name)
        try:
            if not isinstance(definition, dict):
                raise ValueError("Invalid definition")
            required = definition.get("required_tools", [])
            projects = definition.get("projects", [])
            if not isinstance(required, list) or not isinstance(projects, list):
                raise ValueError("Invalid permissions")
            selection = load_selected_skills([name], granted_tools=required,
                project=projects[0] if projects else "catalog_inspection", registry_path=path)
            entry = selection.manifest["skills"][0]
            version = entry["version"]
            if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9_.+-]{1,64}", version):
                raise ValueError("Invalid version")
            required_names = tuple(_identifier(item) for item in entry["required_tools"])
            out.append(SkillCapability(name=name, definition_valid=True, definition_reason="validated",
                version=version, definition_sha256=entry["definition_sha256"], content_sha256=entry["content_sha256"],
                required_tools=required_names,
                contract_adapter="requires_host_scope" if set(required_names) <= supported else "unsupported"))
        except (OSError, ValueError, TypeError, KeyError, IndexError):
            out.append(SkillCapability(name=name, definition_valid=False, definition_reason="invalid_definition",
                version=None, definition_sha256=_digest(definition), content_sha256=None,
                required_tools=(), contract_adapter="unknown"))
    return tuple(out)


def capability_catalog() -> CapabilityCatalog:
    """Read the actual owner's registrations and host files; never invoke them."""
    try:
        tool_path = _host_config_path("MARS_TOOLS_CONFIG_PATH", "tools.yaml")
        agent_path, skill_path = (repo_root() / "configs" / name for name in ("agents.yaml", "skills.yaml"))
        tools_raw, tool_bytes = _mapping(tool_path)
        configured_agents, agent_bytes = _mapping(agent_path)
        skills_raw, skill_bytes = _mapping(skill_path) if skill_path.exists() else ({"version": 1, "skills": {}}, None)
        configs = load_tool_configs()
        registry = get_registry()
        agents = list_agent_configs()
        tools = tuple(_tool(name, registry, configs, agents, configured_agents)
                      for name in sorted(set(configs) | set(registry.names())))
        skills = _skills(skill_path, skills_raw, tools)
        # Hash effective cached declarations separately; do not reload or reset
        # running agents to make their state appear consistent with disk.
        effective_agents = {agent.name: dict(agent.raw) for agent in agents}
        if (tool_path.read_bytes() != tool_bytes or agent_path.read_bytes() != agent_bytes
                or (skill_path.read_bytes() if skill_path.exists() else None) != skill_bytes):
            raise CapabilityCatalogUnavailable("Host configuration changed during inspection")
        from app.harness.tools import config, registry as tool_registry, research_accounting
        from app.harness.skills import registry as skill_registry
        from app.harness.llm import model_registry
        implementation = _digest([hashlib.sha256(Path(source).read_bytes()).hexdigest() for source in
            (__file__, config.__file__, tool_registry.__file__, research_accounting.__file__,
             skill_registry.__file__, model_registry.__file__)])
        result = CapabilityCatalog(context_sha256="", configured_tools_sha256=_digest(tools_raw),
            configured_agents_sha256=_digest(configured_agents), effective_agents_sha256=_digest(effective_agents),
            agent_configuration_drift=configured_agents != effective_agents,
            configured_skills_sha256=_digest(skills_raw) if skill_bytes is not None else None,
            implementation_sha256=implementation, tools=tools, skills=skills)
        return result.model_copy(update={"context_sha256": _digest(result.model_dump(exclude={"context_sha256"}))})
    except CapabilityCatalogUnavailable:
        raise
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, yaml.YAMLError) as exc:
        raise CapabilityCatalogUnavailable("Capability inventory is unavailable") from exc
