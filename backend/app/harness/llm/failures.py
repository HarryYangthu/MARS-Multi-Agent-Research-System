"""Content-free model failure metadata and host-authored recovery guidance."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from openai import APIConnectionError, APITimeoutError

from app.harness.llm.openai_provider import public_error_details
from app.harness.llm.provider_base import LLMCompletionError, LLMConfig


def model_failure_reason(exc: Exception, config: LLMConfig) -> dict[str, Any]:
    """Preserve completion contracts; never copy SDK messages, bodies or headers."""
    if isinstance(exc, LLMCompletionError):
        return dict(exc.reason)
    details = public_error_details(exc)
    status = details.get("http_status")
    code = "model_request_failed"
    if status == 402:
        code = "provider_payment_required"
    elif status in {401, 403}:
        code = "provider_authentication_failed"
    elif status == 429:
        code = "provider_rate_limited"
    elif isinstance(exc, (APITimeoutError, TimeoutError)):
        code = "provider_timeout"
    elif isinstance(exc, APIConnectionError):
        code = "provider_connection_failed"
    return {"code": code, "provider": config.provider, "model": config.model, **details}


def model_failure_message(reason: Mapping[str, Any] | None) -> str:
    """Use only static text selected by bounded diagnostic codes."""
    messages = {
        "provider_payment_required": "模型服务拒绝请求（HTTP 402），请检查账户余额或更换有可用额度的 API Key 后重试。",
        "provider_authentication_failed": "模型服务鉴权或权限检查失败，请检查 API Key 和模型访问权限后重试。",
        "provider_rate_limited": "模型服务限流或额度已用尽（HTTP 429），请检查账户额度和限流设置后重试。",
        "provider_timeout": "模型服务请求超时，请检查服务状态和网络连接后重试。",
        "provider_connection_failed": "无法连接模型服务，请检查服务地址和网络连接后重试。",
    }
    return messages.get(str((reason or {}).get("code")), "模型请求未完成，请检查运行记录中的模型服务诊断后重试。")
