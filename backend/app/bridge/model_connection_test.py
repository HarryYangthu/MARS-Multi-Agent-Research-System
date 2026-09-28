"""An explicit, bounded real model probe; never saves credentials or runs research."""
from __future__ import annotations

import asyncio
import time
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr
import yaml

from app.harness.llm.model_capabilities import setup_reasoning
from app.harness.llm.model_registry import _models_config
from app.harness.llm.provider_base import LLMConfig, LLMProvider, Message
from app.harness.llm.openai_provider import _OpenAICompatProvider, public_error_details
from app.settings import env_or_local, repo_root


class ConnectionTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=1, max_length=2048)
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""))


class ConnectionTestResult(BaseModel):
    ok: bool
    code: str
    message: str
    elapsed_ms: int
    requested_model: str
    configuration_saved: Literal[False] = False


class ProbePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timeout_seconds: float = Field(gt=0, le=40)
    max_tokens: int = Field(ge=16, le=1024)
    close_timeout_seconds: float = Field(gt=0, le=5)
    max_request_bytes: int = Field(ge=1024, le=65536)
    prompt: str = Field(min_length=1, max_length=200)


def probe_policy() -> ProbePolicy:
    raw = yaml.safe_load((repo_root() / "configs/models.yaml").read_text())
    return ProbePolicy.model_validate(raw["connection_test"])


def probe_provider(request: ConnectionTestRequest) -> LLMProvider:
    defaults = _models_config().get(request.provider)
    if defaults is None or request.provider == "mock":
        raise ValueError("unsupported_provider")
    parsed = urlsplit(request.base_url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment):
        raise ValueError("invalid_url")
    credential = request.api_key.get_secret_value().strip() or env_or_local(str(defaults.get("api_key_env") or ""))
    if not credential and request.provider != "local_vllm":
        raise ValueError("missing_key")
    if request.provider == "anthropic":
        from app.harness.llm.anthropic_provider import AnthropicProvider
        return AnthropicProvider(api_key=credential, base_url=request.base_url)
    if request.provider == "gemini":
        from app.harness.llm.gemini_provider import GeminiProvider
        return GeminiProvider(api_key=credential, base_url=request.base_url)
    from app.harness.llm.openai_provider import ZhipuProvider
    if request.provider == "zhipu":
        return ZhipuProvider(api_key=credential, base_url=request.base_url)
    if request.provider not in {"openai", "qwen", "deepseek", "custom", "local_vllm"}:
        raise ValueError("unsupported_provider")
    return _OpenAICompatProvider(api_key=credential or "EMPTY", base_url=request.base_url, provider_name=request.provider)


def failure_message(exc: Exception) -> tuple[str, str]:
    # Never return exception bodies/headers: providers may echo secrets or URLs.
    if isinstance(exc, ValueError) and str(exc) in {
        "unsupported_provider", "invalid_url", "missing_key",
    }:
        code = str(exc)
        return code, {"unsupported_provider": "此服务商不支持连接测试。",
                      "invalid_url": "请输入完整的 http/https API 地址，不含账号、查询参数或片段。",
                      "missing_key": "请填写 API Key，或先保存该服务商的密钥。"}[code]
    details = public_error_details(exc)
    status = details.get("http_status")
    if status in {401, 403}:
        return "authentication_failed", "服务商拒绝访问，请检查 API Key、模型权限和服务地址。"
    if status == 429:
        return "rate_or_quota_limit", "服务商限流或额度不足，请检查账户余额与请求限额后再试。"
    if status in {400, 404, 422}:
        return "request_rejected", "服务商拒绝了请求，请检查模型名称、API 地址和接口兼容性。"
    if isinstance(exc, TimeoutError) or "Timeout" in type(exc).__name__:
        return "timeout", "连接测试超时。请检查网络或服务状态；请求可能已到达服务商，系统不会自动重试。"
    if isinstance(status, int) and status >= 500:
        return "provider_unavailable", "模型服务暂时不可用，请稍后再试。"
    return "connection_failed", "未能完成模型请求，请检查网络、API 地址和服务商状态。"


_probe_lock = asyncio.Lock()


async def test_model_connection(request: ConnectionTestRequest) -> ConnectionTestResult:
    started = time.monotonic()
    def result(ok: bool, code: str, message: str) -> ConnectionTestResult:
        return ConnectionTestResult(ok=ok, code=code, message=message,
                                    elapsed_ms=round((time.monotonic() - started) * 1000), requested_model=request.model)
    if _probe_lock.locked():
        return result(False, "test_busy", "已有连接测试正在进行，请等待本次结果后再试。")
    async with _probe_lock:
        provider: LLMProvider | None = None
        try:
            policy = probe_policy()
            provider = probe_provider(request)
            thinking, effort = setup_reasoning(request.provider, request.model)
            config = LLMConfig(provider=request.provider, model=request.model, temperature=0,
                max_tokens=policy.max_tokens, thinking_enabled=thinking, reasoning_effort=effort,
                request_timeout_seconds=policy.timeout_seconds, max_retries=0)
            async with asyncio.timeout(policy.timeout_seconds):
                completion = await provider.complete([Message(role="user", content=policy.prompt)], config)
            if completion.is_mock or not completion.text.strip():
                return result(False, "empty_response", "服务未返回可用的真实模型响应。")
            return result(True, "connected", "连接成功，已收到真实模型响应。此次测试未保存配置，也未启动研究。")
        except Exception as exc:
            code, message = failure_message(exc)
            return result(False, code, message)
        finally:
            if provider is not None:
                try:
                    await asyncio.wait_for(provider.close(), timeout=policy.close_timeout_seconds)
                except Exception:
                    pass  # Never disclose provider cleanup exceptions or strand the request.
