"""Real refused connections and timeouts, configuration-only and pure error tests."""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
import socket

import httpx
import pytest
import yaml

from app.bridge.model_connection_test import ConnectionTestRequest, failure_message, test_model_connection as run_probe
from app.harness.llm.model_registry import reset_cache_for_tests


@pytest.fixture
def configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    # Real on-disk configuration, never a provider or transport substitution.
    source = tmp_path / "configs"
    source.mkdir()
    (source / "models.yaml").write_text(yaml.safe_dump({
        "providers": {"local_vllm": {}, "custom": {"api_key_env": "MARS_TEST_UNSET_PROBE_KEY"}},
        "connection_test": {"timeout_seconds": 0.2, "close_timeout_seconds": 0.1,
                            "max_tokens": 32, "max_request_bytes": 4096, "prompt": "Reply OK"}}))
    monkeypatch.setattr("app.settings.REPO_ROOT", tmp_path)
    monkeypatch.setattr("app.settings.LOCAL_ENV_FILES", (tmp_path / ".env", tmp_path / ".env.local"))
    monkeypatch.delenv("MARS_TEST_UNSET_PROBE_KEY", raising=False)
    reset_cache_for_tests()
    yield source
    reset_cache_for_tests()


def request(url: str, provider: str = "local_vllm") -> ConnectionTestRequest:
    return ConnectionTestRequest(provider=provider, model="connectivity-check", base_url=url)


@pytest.mark.asyncio
async def test_real_closed_port_fails_without_persisting_configuration(configuration: Path) -> None:
    before = (configuration / "models.yaml").read_bytes()
    with socket.socket() as port:
        port.bind(("127.0.0.1", 0))  # Reserved but not listening: real refusal.
        result = await run_probe(request(f"http://127.0.0.1:{port.getsockname()[1]}/v1"))
    assert not result.ok and result.code in {"connection_failed", "timeout"}
    assert not result.configuration_saved
    assert (configuration / "models.yaml").read_bytes() == before
    assert not (configuration.parent / ".env.local").exists()


@pytest.mark.asyncio
async def test_real_nonresponding_socket_times_out_and_parallel_test_is_refused(configuration: Path) -> None:
    entered = asyncio.Event()
    received: list[bytes] = []
    async def hold(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            received.append(await reader.read(65536))
            entered.set()
            await reader.read()  # No HTTP/model response; wait for real client close.
        finally:
            writer.close()
            await writer.wait_closed()
    server = await asyncio.start_server(hold, "127.0.0.1", 0)
    try:
        async with server:
            port = server.sockets[0].getsockname()[1]
            task = asyncio.create_task(run_probe(request(f"http://127.0.0.1:{port}/v1")))
            await asyncio.wait_for(entered.wait(), timeout=3)
            other = await run_probe(request(f"http://127.0.0.1:{port}/v1"))
            assert not other.ok and other.code == "test_busy"
            result = await asyncio.wait_for(task, timeout=3)
            assert not result.ok and result.code == "timeout"
            assert len(received) == 1  # No automatic retry.
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("url,provider,code", [
    ("http://127.0.0.1/v1", "custom", "missing_key"),
    ("file:///tmp/key", "local_vllm", "invalid_url"),
    ("https://user:password@example.com/v1", "local_vllm", "invalid_url"),
    ("https://example.com/v1?secret=value", "local_vllm", "invalid_url"),
    ("https://example.com/v1", "mock", "unsupported_provider"),
])
async def test_unusable_configuration_is_rejected_before_request(configuration: Path, url: str, provider: str, code: str) -> None:
    result = await run_probe(request(url, provider))
    assert not result.ok and result.code == code
    assert "password" not in result.model_dump_json() and "secret=value" not in result.model_dump_json()


@pytest.mark.parametrize("status,code", [(401, "authentication_failed"), (403, "authentication_failed"),
    (429, "rate_or_quota_limit"), (404, "request_rejected"), (503, "provider_unavailable")])
def test_error_classifier_never_echoes_provider_body(status: int, code: str) -> None:
    # Pure mapping test, not a simulated provider execution.
    response = httpx.Response(status, request=httpx.Request("POST", "https://example.com"))
    value = failure_message(httpx.HTTPStatusError("DO_NOT_ECHO_SECRET", request=response.request, response=response))
    assert value[0] == code and "DO_NOT_ECHO_SECRET" not in value[1]


def test_malformed_api_input_does_not_echo_submitted_secret(configuration: Path) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.config import router
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.post("/api/config/test-connection", json={"api_key": "DO_NOT_ECHO_SECRET"})
        assert response.status_code == 422 and "DO_NOT_ECHO_SECRET" not in response.text
        oversized = client.post("/api/config/test-connection", content=b"x" * 4097)
        assert oversized.status_code == 413
