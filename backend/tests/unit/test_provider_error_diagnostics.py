"""Classify authored SDK exception values; no transport receives a substitute response."""
from datetime import datetime, timezone
from typing import Any, cast

import httpx
import pytest
from openai import APIStatusError

from app.harness.llm.openai_provider import _is_retryable_error, public_error_details, retry_after_seconds


@pytest.mark.parametrize("code,retryable", [("1113", False), ("1308", False), ("1310", False),
                                            ("1313", False), ("1315", False), ("1302", True), ("1305", True)])
def test_account_limits_are_distinct_from_transient_429(code: str, retryable: bool) -> None:
    response = httpx.Response(429, request=httpx.Request("POST", "https://example.invalid"),
                              headers={"retry-after": "30", "authorization": "authored-private-header"})
    error = APIStatusError("authored error-classifier input", response=cast(Any, response),
                           body={"error": {"code": code, "message": "authored-private-response-message"}})
    assert _is_retryable_error(error, provider_name="zhipu") is retryable
    assert public_error_details(error) == {"exception_type": "APIStatusError", "http_status": 429,
                                           "api_error_code": code, "retry_after_seconds": 30.0}


def test_unrecognized_body_values_do_not_escape_into_diagnostics() -> None:
    response = httpx.Response(429, request=httpx.Request("POST", "https://example.invalid"))
    error = APIStatusError("authored private message", response=cast(Any, response),
                           body={"error": {"code": "authored.secret-shaped-value", "message": "private"}})
    assert public_error_details(error) == {"exception_type": "APIStatusError", "http_status": 429}


def test_retry_after_supports_seconds_and_http_dates_and_rejects_nonfinite_values() -> None:
    assert retry_after_seconds("30") == 30
    now = datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc)
    assert retry_after_seconds("Mon, 07 Sep 2026 00:00:30 GMT", now=now) == 30
    for value in [None, "nan", "inf", "invalid date"]:
        assert retry_after_seconds(value) is None
