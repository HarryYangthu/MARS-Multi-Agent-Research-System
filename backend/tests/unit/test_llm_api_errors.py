"""Classify exception values without substituting model or transport execution."""
import json
from typing import Any, cast

import httpx
import pytest
from openai import APIConnectionError, APIStatusError, APITimeoutError
from starlette.requests import Request

from app.api.llm_errors import llm_error_response


@pytest.mark.parametrize("upstream,status,text", [
    (402, 402, "余额不足"),
    (401, 502, "鉴权"),
    (403, 502, "权限"),
    (429, 429, "限流"),
    (500, 502, "模型服务请求失败"),
    (400, 502, "模型服务请求失败"),
])
async def test_status_errors_have_safe_actionable_details(upstream: int, status: int, text: str) -> None:
    private = "authored-private-value"
    request = httpx.Request("POST", "https://example.invalid", headers={"authorization": private})
    error = APIStatusError(private, response=cast(Any, httpx.Response(upstream, request=request)),
                           body={"error": private})
    response = await llm_error_response(Request({"type": "http"}), error)
    body = bytes(response.body).decode()
    assert response.status_code == status
    assert text in json.loads(body)["detail"]
    assert private not in body


@pytest.mark.parametrize("timeout,status,text", [(False, 502, "无法连接"), (True, 504, "超时")])
async def test_connection_errors_remain_failures(timeout: bool, status: int, text: str) -> None:
    request = httpx.Request("POST", "https://example.invalid")
    error = APITimeoutError(cast(Any, request)) if timeout else APIConnectionError(request=cast(Any, request))
    response = await llm_error_response(Request({"type": "http"}), error)
    assert response.status_code == status
    assert text in json.loads(bytes(response.body))["detail"]
