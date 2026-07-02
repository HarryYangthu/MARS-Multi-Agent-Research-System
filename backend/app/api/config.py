"""Controlled config workbench API for V2."""
from __future__ import annotations

import difflib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.harness.llm.model_registry import reset_cache_for_tests
from app.settings import env_or_local, repo_root, set_runtime_env

router = APIRouter(prefix="/api/config", tags=["config"])

CONFIG_NAMES: tuple[str, ...] = (
    "agents",
    "models",
    "tools",
    "gates",
    "workflow",
    "reporting",
    "knowledge",
    "execution",
    "frontend",
)
HIGH_RISK_CONFIGS = {"agents", "tools", "gates", "execution", "workflow"}
AGENT_ORDER: tuple[str, ...] = (
    "commander",
    "idea",
    "experiment",
    "coding",
    "execution",
    "writing",
)
ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


class ConfigFile(BaseModel):
    name: str
    path: str
    text: str
    data: dict[str, Any]
    high_risk: bool = False


class ConfigSnapshot(BaseModel):
    files: list[ConfigFile]


class ConfigTextPayload(BaseModel):
    name: str = Field(..., min_length=1)
    text: str


class ConfigApplyPayload(ConfigTextPayload):
    actor: str = "user"
    confirm_high_risk: bool = False


class ConfigValidationResult(BaseModel):
    valid: bool
    errors: list[str] = []
    data: dict[str, Any] = {}


class ConfigDiffResult(BaseModel):
    name: str
    diff: str
    valid: bool
    errors: list[str] = []
    high_risk: bool = False


class ProviderDefaultView(BaseModel):
    api_key_env: str = ""
    base_url: str = ""
    base_url_env: str = ""
    configured: bool = False
    base_url_configured: bool = False


class AgentLlmConfigRow(BaseModel):
    agent: str
    enabled: bool = True
    provider: str
    model: str
    temperature: float = 0.7
    max_tokens: int = 4096
    api_key_env: str = ""
    api_key_configured: bool = False
    base_url: str = ""
    base_url_env: str = ""
    base_url_configured: bool = False


class AgentLlmConfigView(BaseModel):
    agents: list[AgentLlmConfigRow]
    providers: list[str]
    provider_defaults: dict[str, ProviderDefaultView]
    secrets_path: str
    note: str


class AgentLlmUpdateRow(BaseModel):
    agent: str = Field(..., min_length=1)
    enabled: bool = True
    provider: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=1, le=200000)
    api_key_env: str = ""
    api_key: str = ""
    base_url: str = ""
    base_url_env: str = ""


class AgentLlmUpdatePayload(BaseModel):
    agents: list[AgentLlmUpdateRow]
    actor: str = "frontend"


@router.get("", response_model=ConfigSnapshot)
async def get_config() -> ConfigSnapshot:
    return ConfigSnapshot(files=[_read_config(name) for name in CONFIG_NAMES])


@router.get("/agent-llm", response_model=AgentLlmConfigView)
async def get_agent_llm_config() -> AgentLlmConfigView:
    return _read_agent_llm_config()


@router.post("/agent-llm", response_model=AgentLlmConfigView)
async def update_agent_llm_config(payload: AgentLlmUpdatePayload) -> AgentLlmConfigView:
    if not payload.agents:
        raise HTTPException(status_code=422, detail="at least one agent row is required")
    agents_path = _config_path("agents")
    before = agents_path.read_text(encoding="utf-8") if agents_path.exists() else ""
    agents_raw = _read_yaml_mapping(agents_path)
    provider_map = _provider_map()
    secret_updates: dict[str, str] = {}

    for row in payload.agents:
        if row.agent not in agents_raw:
            raise HTTPException(status_code=404, detail=f"unknown agent '{row.agent}'")
        if row.provider != "mock" and row.provider not in provider_map:
            raise HTTPException(
                status_code=422,
                detail=f"unknown provider '{row.provider}' for agent '{row.agent}'",
            )
        api_key_env = row.api_key_env.strip()
        base_url_env = row.base_url_env.strip()
        for env_name, label in ((api_key_env, "api_key_env"), (base_url_env, "base_url_env")):
            if env_name and not ENV_NAME_RE.match(env_name):
                raise HTTPException(
                    status_code=422,
                    detail=f"{label} for agent '{row.agent}' must look like API_KEY_ENV",
                )
        body = _mutable_mapping(agents_raw[row.agent])
        model = _mutable_mapping(body.get("model"))
        model["provider"] = row.provider
        model["model"] = row.model
        model["temperature"] = row.temperature
        model["max_tokens"] = row.max_tokens
        _set_or_remove(model, "api_key_env", api_key_env)
        _set_or_remove(model, "base_url", row.base_url.strip())
        _set_or_remove(model, "base_url_env", base_url_env)
        body["enabled"] = row.enabled
        body["model"] = model
        agents_raw[row.agent] = body
        if row.api_key:
            if not api_key_env:
                raise HTTPException(
                    status_code=422,
                    detail=f"api_key_env is required when setting a key for '{row.agent}'",
                )
            secret_updates[api_key_env] = row.api_key

    after = yaml.safe_dump(agents_raw, allow_unicode=True, sort_keys=False)
    agents_path.write_text(after, encoding="utf-8")
    if secret_updates:
        _write_local_env_values(secret_updates)
        set_runtime_env(secret_updates)
    reset_cache_for_tests()
    _write_audit_event(
        {
            "event": "agent_llm_config.applied",
            "actor": payload.actor,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "agents": [row.agent for row in payload.agents],
            "secrets_updated": sorted(secret_updates),
            "diff": "".join(
                difflib.unified_diff(
                    before.splitlines(keepends=True),
                    after.splitlines(keepends=True),
                    fromfile="configs/agents.yaml",
                    tofile="configs/agents.yaml",
                )
            ),
        }
    )
    return _read_agent_llm_config()


@router.post("/validate", response_model=ConfigValidationResult)
async def validate_config(payload: ConfigTextPayload) -> ConfigValidationResult:
    _config_path(payload.name)
    valid, data, errors = _validate_yaml(payload.name, payload.text)
    return ConfigValidationResult(valid=valid, errors=errors, data=data)


@router.post("/preview-diff", response_model=ConfigDiffResult)
async def preview_config_diff(payload: ConfigTextPayload) -> ConfigDiffResult:
    path = _config_path(payload.name)
    valid, _, errors = _validate_yaml(payload.name, payload.text)
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    diff = "".join(
        difflib.unified_diff(
            current.splitlines(keepends=True),
            payload.text.splitlines(keepends=True),
            fromfile=f"configs/{payload.name}.yaml",
            tofile=f"configs/{payload.name}.yaml (proposed)",
        )
    )
    return ConfigDiffResult(
        name=payload.name,
        diff=diff,
        valid=valid,
        errors=errors,
        high_risk=payload.name in HIGH_RISK_CONFIGS,
    )


@router.post("/apply", response_model=ConfigFile)
async def apply_config(payload: ConfigApplyPayload) -> ConfigFile:
    path = _config_path(payload.name)
    valid, _, errors = _validate_yaml(payload.name, payload.text)
    if not valid:
        raise HTTPException(status_code=422, detail={"errors": errors})
    if payload.name in HIGH_RISK_CONFIGS and not payload.confirm_high_risk:
        raise HTTPException(
            status_code=409,
            detail=f"config '{payload.name}' is high risk and requires confirmation",
        )
    before = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(payload.text, encoding="utf-8")
    _write_audit_event(
        {
            "event": "config.applied",
            "name": payload.name,
            "actor": payload.actor,
            "high_risk": payload.name in HIGH_RISK_CONFIGS,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "diff": "".join(
                difflib.unified_diff(
                    before.splitlines(keepends=True),
                    payload.text.splitlines(keepends=True),
                    fromfile=f"configs/{payload.name}.yaml",
                    tofile=f"configs/{payload.name}.yaml",
                )
            ),
        }
    )
    return _read_config(payload.name)


def _read_config(name: str) -> ConfigFile:
    path = _config_path(name)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    valid, data, _ = _validate_yaml(name, text)
    return ConfigFile(
        name=name,
        path=f"configs/{name}.yaml",
        text=text,
        data=data if valid else {},
        high_risk=name in HIGH_RISK_CONFIGS,
    )


def _config_path(name: str) -> Path:
    if name not in CONFIG_NAMES:
        raise HTTPException(
            status_code=404,
            detail=f"unknown config '{name}'. supported: {', '.join(CONFIG_NAMES)}",
        )
    return repo_root() / "configs" / f"{name}.yaml"


def _validate_yaml(name: str, text: str) -> tuple[bool, dict[str, Any], list[str]]:
    try:
        parsed = yaml.safe_load(text) if text.strip() else {}
    except yaml.YAMLError as exc:
        return False, {}, [str(exc)]
    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        return False, {}, ["top-level YAML value must be a mapping"]
    if name in {"workflow", "reporting", "frontend"} and name not in parsed:
        return False, parsed, [f"top-level key '{name}' is required"]
    return True, parsed, []


def _read_agent_llm_config() -> AgentLlmConfigView:
    agents_raw = _read_yaml_mapping(_config_path("agents"))
    provider_map = _provider_map()
    provider_defaults = {
        provider: ProviderDefaultView(
            api_key_env=str(body.get("api_key_env") or ""),
            base_url=str(body.get("base_url") or ""),
            base_url_env=str(body.get("base_url_env") or ""),
            configured=bool(env_or_local(str(body.get("api_key_env") or ""))),
            base_url_configured=bool(
                env_or_local(str(body.get("base_url_env") or ""))
                or str(body.get("base_url") or "")
            ),
        )
        for provider, body in provider_map.items()
    }
    ordered_agents = [name for name in AGENT_ORDER if name in agents_raw]
    ordered_agents.extend(name for name in agents_raw if name not in ordered_agents)
    rows: list[AgentLlmConfigRow] = []
    for agent in ordered_agents:
        body = _mutable_mapping(agents_raw.get(agent))
        model = _mutable_mapping(body.get("model"))
        provider = str(model.get("provider") or "mock")
        defaults = provider_map.get(provider, {})
        api_key_env = str(model.get("api_key_env") or defaults.get("api_key_env") or "")
        base_url = str(model.get("base_url") or defaults.get("base_url") or "")
        base_url_env = str(model.get("base_url_env") or defaults.get("base_url_env") or "")
        rows.append(
            AgentLlmConfigRow(
                agent=agent,
                enabled=bool(body.get("enabled", True)),
                provider=provider,
                model=str(model.get("model") or "mock-1"),
                temperature=float(model.get("temperature", 0.7)),
                max_tokens=int(model.get("max_tokens", 4096)),
                api_key_env=api_key_env,
                api_key_configured=bool(env_or_local(api_key_env)),
                base_url=base_url,
                base_url_env=base_url_env,
                base_url_configured=bool(env_or_local(base_url_env) or base_url),
            )
        )
    return AgentLlmConfigView(
        agents=rows,
        providers=sorted(set(provider_map) | {"mock"}),
        provider_defaults=provider_defaults,
        secrets_path=".env.local",
        note="API keys are written only to ignored local env files; configs/agents.yaml stores model routing.",
    )


def _provider_map() -> dict[str, dict[str, Any]]:
    models_raw = _read_yaml_mapping(_config_path("models"))
    providers = models_raw.get("providers", {})
    if not isinstance(providers, dict):
        return {}
    return {
        str(name): _mutable_mapping(body)
        for name, body in providers.items()
        if isinstance(body, dict)
    }


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail=f"{path.name} must be a mapping")
    return data


def _mutable_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _set_or_remove(mapping: dict[str, Any], key: str, value: str) -> None:
    if value:
        mapping[key] = value
    else:
        mapping.pop(key, None)


def _write_local_env_values(updates: dict[str, str]) -> None:
    path = repo_root() / ".env.local"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    next_lines: list[str] = []
    seen: set[str] = set()
    for line in lines:
        key = _env_assignment_key(line)
        if key and key in updates:
            next_lines.append(f"{key}={_quote_env_value(updates[key])}")
            seen.add(key)
        else:
            next_lines.append(line)
    missing = [key for key in updates if key not in seen]
    if missing and next_lines and next_lines[-1].strip():
        next_lines.append("")
    for key in missing:
        next_lines.append(f"{key}={_quote_env_value(updates[key])}")
    path.write_text("\n".join(next_lines).rstrip() + "\n", encoding="utf-8")


def _env_assignment_key(line: str) -> str:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return ""
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].strip()
    key = stripped.split("=", 1)[0].strip()
    return key if ENV_NAME_RE.match(key) else ""


def _quote_env_value(value: str) -> str:
    if not value or any(ch.isspace() or ch in {'"', "'", "#"} for ch in value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _write_audit_event(payload: dict[str, Any]) -> None:
    audit_dir = repo_root() / "runs" / "config_audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    with (audit_dir / "config_events.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
