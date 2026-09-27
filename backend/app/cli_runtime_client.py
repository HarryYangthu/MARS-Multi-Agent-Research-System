"""Bounded HTTP access to the existing backend owner; never a second driver.

This adapter deliberately has no create-and-start research operation. Contract
preparation remains preparation, and HTTP success is not research success.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
import ipaddress
import json
import math
import os
import re
from types import TracebackType
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import httpx


class RuntimeClientError(RuntimeError):
    """Safe transport/protocol failure without request objects or credentials."""

    def __init__(self, code: str, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class RuntimeResponse:
    status_code: int
    payload: Any

    @property
    def ok(self) -> bool:
        """HTTP success only; callers must preserve the backend's outcome."""
        return 200 <= self.status_code < 300


def _loopback_origin(value: str) -> str:
    # Reject authority ambiguities before either URL parser can normalize them.
    if (not value or value != value.strip() or "\\" in value
            or any(ord(char) < 33 or ord(char) == 127 for char in value)):
        raise ValueError("Backend URL must be a loopback HTTP(S) origin")
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
        if (parsed.scheme not in {"http", "https"} or not host or "%" in host
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
                or "?" in value or "#" in value or port == 0):
            raise ValueError
        # Pin localhost to an IP instead of trusting DNS or proxy settings.
        address = ipaddress.ip_address("127.0.0.1" if host == "localhost" else host)
        if not address.is_loopback or (isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None):
            raise ValueError
    except ValueError:
        raise ValueError("Backend URL must be a loopback HTTP(S) origin without credentials, path or query") from None
    authority = f"[{address}]" if address.version == 6 else str(address)
    if port is not None:
        authority += f":{port}"
    return urlunsplit((parsed.scheme, authority, "", "", ""))


def _run_path(run_id: str, action: str = "") -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", run_id) or run_id in {".", ".."}:
        raise ValueError("run_id must be one literal run identifier")
    return f"/api/runs/{run_id}" + (f"/{action}" if action else "")


def _safe_payload(value: Any, token: str) -> Any:
    """Keep backend facts while removing credentials, including echoed token."""
    if isinstance(value, float) and not math.isfinite(value):
        # json.loads also overflows syntactically valid numbers such as 1e309;
        # parse_constant alone only catches the nonstandard NaN/Infinity forms.
        raise ValueError("nonfinite JSON number")
    if isinstance(value, str):
        return value.replace(token, "[REDACTED]") if token else value
    if isinstance(value, list):
        return [_safe_payload(item, token) for item in value]
    if isinstance(value, dict):
        protected = {"authorization", "proxy-authorization", "x-mars-desktop-token",
                     "mars_desktop_session_token", "api_key", "apikey", "password", "secret"}
        return {str(_safe_payload(key, token)): "[REDACTED]" if key.lower() in protected
                else _safe_payload(item, token) for key, item in value.items()}
    return value


def _reject_nonfinite(value: str) -> None:
    raise ValueError("nonfinite JSON number")


class RuntimeClient:
    """Use with ``async with``; limits are supplied by the CLI configuration.

    Only the process environment supplies MARS_DESKTOP_SESSION_TOKEN. Importing
    Settings here would also read local credential files, so it is intentional
    that this module depends on neither Settings nor the server composition.
    No redirects, proxy environment, retries, startup, or execution fallback.
    The deadline bounds network activity; JSON decoding is size bounded, not
    a preemptible CPU operation.
    """

    def __init__(self, base_url: str, *, timeout_seconds: float, max_response_bytes: int) -> None:
        self.base_url = _loopback_origin(base_url)
        if isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive and finite")
        if isinstance(max_response_bytes, bool) or not isinstance(max_response_bytes, int) or max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be a positive integer")
        self._timeout = timeout_seconds
        self._max_bytes = max_response_bytes
        self._client: httpx.AsyncClient | None = None
        self._token = ""

    async def __aenter__(self) -> RuntimeClient:
        if self._client is not None:
            raise RuntimeClientError("already_open", "Runtime client is already open")
        token = os.environ.get("MARS_DESKTOP_SESSION_TOKEN", "")
        if token and (len(token) < 32 or not token.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in token)):
            raise RuntimeClientError("invalid_session", "MARS_DESKTOP_SESSION_TOKEN is not a valid session credential")
        headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
        if token:
            headers["X-MARS-Desktop-Token"] = token
        self._token = token
        self._client = httpx.AsyncClient(base_url=self.base_url, headers=headers,
                                         timeout=self._timeout, follow_redirects=False, trust_env=False)
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                        traceback: TracebackType | None) -> None:
        client, self._client = self._client, None
        self._token = ""
        if client is not None:
            await client.aclose()

    async def list_runs(self, project: str = "") -> RuntimeResponse:
        return await self._request("GET", "/api/runs", params={"project": project} if project else None)

    async def detail(self, run_id: str) -> RuntimeResponse:
        return await self._request("GET", _run_path(run_id))

    async def start(self, run_id: str) -> RuntimeResponse:
        return await self._request("POST", _run_path(run_id, "start"))

    async def stop(self, run_id: str) -> RuntimeResponse:
        return await self._request("POST", _run_path(run_id, "stop"))

    async def resume(self, run_id: str) -> RuntimeResponse:
        return await self._request("POST", _run_path(run_id, "resume"))

    async def defaults(self) -> RuntimeResponse:
        return await self._request("GET", "/api/research-contracts/defaults")

    async def preflight(self, project: Mapping[str, Any]) -> RuntimeResponse:
        return await self._request("POST", "/api/research-contracts/preflight", payload=dict(project))

    async def prepare(self, *, project: Mapping[str, Any], goal: str,
                      mode: Literal["manual", "bounded_auto"], budget: Mapping[str, Any]) -> RuntimeResponse:
        return await self._request("POST", "/api/research-contracts/prepare",
                                   payload={"project": dict(project), "goal": goal, "mode": mode, "budget": dict(budget)})

    async def _request(self, method: str, path: str, *, params: Mapping[str, str] | None = None,
                       payload: dict[str, Any] | None = None) -> RuntimeResponse:
        client = self._client
        if client is None:
            raise RuntimeClientError("not_open", "Use RuntimeClient as an async context manager")
        if (not path.startswith("/") or path.startswith("//") or "\\" in path
                or any(ord(char) < 33 or ord(char) == 127 for char in path)):
            raise RuntimeClientError("invalid_request", "Request route must remain on the configured backend")
        try:
            content = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8") if payload is not None else None
        except (TypeError, ValueError, OverflowError):
            raise RuntimeClientError("invalid_request", "Request must contain finite JSON values") from None
        status: int | None = None
        try:
            # httpx's per-operation timeout alone does not bound a slow stream.
            async with asyncio.timeout(self._timeout):
                async with client.stream(method, path, params=params, content=content,
                                         headers={"Content-Type": "application/json"} if content is not None else None) as response:
                    status = response.status_code
                    if 300 <= status < 400:
                        raise RuntimeClientError("redirect_refused", "Backend redirect refused; use its explicit loopback origin", status_code=status)
                    if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
                        raise RuntimeClientError("invalid_response", "Backend must return an uncompressed bounded response", status_code=status)
                    length = response.headers.get("content-length")
                    if length is not None:
                        try:
                            declared = int(length)
                        except ValueError:
                            raise RuntimeClientError("invalid_response", "Backend sent an invalid Content-Length", status_code=status) from None
                        if declared < 0:
                            raise RuntimeClientError("invalid_response", "Backend sent an invalid Content-Length", status_code=status)
                        if declared > self._max_bytes:
                            raise RuntimeClientError("response_too_large", "Backend response exceeds the configured byte limit", status_code=status)
                    kind = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if kind != "application/json" and not (kind.startswith("application/") and kind.endswith("+json")):
                        raise RuntimeClientError("invalid_response", "Backend did not return JSON", status_code=status)
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > self._max_bytes:
                            raise RuntimeClientError("response_too_large", "Backend response exceeds the configured byte limit", status_code=status)
                        body.extend(chunk)
            parsed = json.loads(body, parse_constant=_reject_nonfinite)
            return RuntimeResponse(status, _safe_payload(parsed, self._token))
        except (TimeoutError, httpx.TimeoutException):
            raise RuntimeClientError("timeout", "Backend request timed out; mutation outcome may be unknown; inspect status before retrying", status_code=status) from None
        except httpx.HTTPError:
            raise RuntimeClientError("connection_failed", "Cannot complete the backend request; ensure the selected backend is running; inspect status before retrying a mutation", status_code=status) from None
        except (UnicodeError, ValueError, RecursionError):
            raise RuntimeClientError("invalid_response", "Backend returned invalid JSON", status_code=status) from None
