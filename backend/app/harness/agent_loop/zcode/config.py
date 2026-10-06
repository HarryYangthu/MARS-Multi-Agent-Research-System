"""Host-owned configuration and public runtime discovery, without model credentials."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any

import yaml

from app.settings import repo_root
from app.harness.agent_loop.trace import atomic_json


@dataclass(frozen=True)
class ZCodeConfig:
    command: tuple[str, ...]
    rpc_timeout_seconds: int
    active_timeout_seconds: int
    max_model_calls: int
    max_tool_calls: int
    max_validation_repairs: int
    max_request_bytes: int
    max_protocol_line_bytes: int
    max_observation_chars: int

    @classmethod
    def load(cls, path: Path | None = None) -> ZCodeConfig:
        raw = yaml.safe_load((path or repo_root() / "configs/zcode.yaml").read_text())
        if not isinstance(raw, dict) or set(raw) != set(cls.__dataclass_fields__):
            raise ValueError("Invalid ZCode configuration fields")
        command = raw.pop("command")
        if not isinstance(command, list) or any(not isinstance(s, str) or not s for s in command):
            raise ValueError("ZCode command must be an argv list")
        if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in raw.values()):
            raise ValueError("ZCode limits must be positive integers")
        return cls(command=tuple(command), **raw)

    def resolve_command(self) -> tuple[str, ...]:
        if self.command:
            binary = shutil.which(self.command[0])
            if binary is None:
                raise ValueError("Configured ZCode executable is unavailable")
            return (binary, *self.command[1:])
        binary = shutil.which("zcode")
        if binary:
            return (binary,)
        # Local desktop fallback. Other teams may use PATH or configure an argv above.
        bundle = Path("/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs")
        node = shutil.which("node")
        if bundle.is_file() and node:
            return (node, str(bundle))
        raise ValueError("ZCode 未安装：请安装官方 CLI 或在 configs/zcode.yaml 设置 command；没有调用模型。")


def check_user_extensions() -> None:
    """The official CLI discovers user config; fail closed on executable extensions.

    Session allowlists control tools, but cannot control startup hooks/plugins.
    Until upstream exposes a no-user-config flag, reject those settings rather
    than silently running user hooks in a MARS background task.
    """
    path = Path.home() / ".zcode/cli/config.json"
    if not path.is_file():
        return
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("ZCode user configuration is invalid")
    for key in ("hooks", "plugins", "mcp"):
        section = data.get(key)
        if section:
            raise ValueError("ZCode 的用户配置含启动扩展。请使用没有 hooks、plugins、MCP 的专用运行环境；MARS 未启动编码。")


def model_config(model: str, max_tokens: int, endpoint: str, token: str, *, context_window: int) -> dict[str, Any]:
    """Only a short-lived loopback token is written, never the upstream API key."""
    return {"schemaVersion": 1, "config": {
        "providerConfigRules": {"providerRules": [{
            "providerId": "mars", "providerName": "MARS", "enabled": True,
            "config": {"group": "standard-personal", "access": {"type": "api-key", "apiKey": token},
                       "api": {"type": "openai-chat-completions", "baseUrl": endpoint + "/v1"},
                       "personalModelIds": [model], "visibility": "visible"}}]},
        "modelConfigRules": {"manualProviderModelRules": [], "providerModelRules": [{
            "providerId": "mars", "modelId": model, "config": {"enabled": True,
                "properties": {"requiresMfjsToolSchema": False, "contextWindow": context_window,
                    "inputFormat": {"supportsText": True, "supportsImage": False, "supportsAudio": False,
                                    "supportsVideo": False, "supportsPdf": False},
                    "outputFormat": {"supportsText": True}, "supportsToolCall": True,
                    "supportsJsonSchemaOutput": False, "supportsNativeWebSearch": False,
                    "supportsMidConversationSystem": True},
                "optionSpecs": {"reasoningLevel": {"values": ["low"], "map": "{}"},
                                "maxOutputTokens": {"max": max_tokens, "map": '{"max_tokens": maxOutputTokens}'}}}}]},
        "defaultModelSelection": {"providerId": "mars", "modelId": model, "options": {"reasoningLevel": "low"}}}}


def runtime_environment(root: Path, *, provider: dict[str, Any]) -> dict[str, str]:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    builtin = root / "builtin.json"
    personal = root / "provider.json"
    atomic_json(builtin, {"schemaVersion": 1, "revision": 1, "config": {
        "providerConfigRules": {"templateRules": [], "providerRules": []},
        "modelConfigRules": {key: [] for key in ("modelRules", "modelApiRules", "providerSiteRules",
                                                "templateModelRules", "builtinProviderModelRules")}}})
    atomic_json(personal, provider)
    personal.chmod(0o600)
    return {"ZCODE_HOME": str(root), "ZCODE_DATA_BASE_DIR": str(root),
            "ZCODE_STORAGE_DIR": str(root / "storage"), "ZCODE_LOG_DIR": str(root / "logs"),
            "ZCODE_BUILTIN_PROVIDER_CONFIG_FILE": str(builtin),
            "ZCODE_BUILTIN_PROVIDER_BUNDLED_CONFIG_FILE": str(builtin),
            "ZCODE_PERSONAL_PROVIDER_CONFIG_FILE": str(personal), "ZCODE_MODEL_TELEMETRY_ENABLED": "false",
            "PATH": os.environ.get("PATH", "")}


def runtime_identity(command: tuple[str, ...]) -> dict[str, str]:
    """A resumed session cannot silently switch to an updated runtime binary."""
    identities = {}
    for value in command:
        path = Path(value)
        if path.is_file():
            identities[str(path.resolve())] = hashlib.sha256(path.read_bytes()).hexdigest()
    return identities
