"""Expose safe failures from OpenAI-compatible providers to API clients."""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from loguru import logger
from openai import APIConnectionError, APIStatusError, APITimeoutError


async def llm_error_response(_request: Request, exc: Exception) -> JSONResponse:
    """Keep provider failures inside ExceptionMiddleware so CORS still applies.

    Provider bodies, request headers and exception messages may contain secrets;
    only the exception type and HTTP status are safe to expose here.
    """
    status = 502
    detail = "模型服务请求失败，请稍后重试或检查模型服务配置。"
    if isinstance(exc, APIStatusError):
        upstream_status = exc.status_code
        if upstream_status == 402:
            status = 402
            detail = "模型服务账户余额不足（HTTP 402）。请充值当前 API Key 对应的账户，或更换有可用额度的 Key。"
        elif upstream_status in {401, 403}:
            detail = "模型服务鉴权或权限检查失败，请检查 API Key 和模型访问权限。"
        elif upstream_status == 429:
            status = 429
            detail = "模型服务限流或额度已用尽（HTTP 429），请检查账户额度后重试。"
        logger.warning("LLM API request failed: type={} upstream_status={}", type(exc).__name__, upstream_status)
    else:
        if isinstance(exc, APITimeoutError):
            status = 504
            detail = "模型服务请求超时，请稍后重试。"
        elif isinstance(exc, APIConnectionError):
            detail = "无法连接模型服务，请检查服务地址和网络连接。"
        logger.warning("LLM API request failed: type={}", type(exc).__name__)
    return JSONResponse(status_code=status, content={"detail": detail})
