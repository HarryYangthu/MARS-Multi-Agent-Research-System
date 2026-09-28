"""Actual uvicorn/MARS HTTP calls and files; no substituted research results."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
from typing import Any

import httpx
import pytest

from app.cli_runtime_client import RuntimeClient, RuntimeClientError, _loopback_origin

ROOT = Path(__file__).resolve().parents[3]


@contextmanager
def environment(**values: str | None) -> Iterator[None]:
    """Use actual process environment without replacing runtime dependencies."""
    old = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@dataclass(frozen=True)
class LiveBackend:
    origin: str
    token: str
    runtime: Path

    def client(self, *, timeout_seconds: float = 5, max_response_bytes: int = 1_048_576) -> RuntimeClient:
        return RuntimeClient(self.origin, timeout_seconds=timeout_seconds, max_response_bytes=max_response_bytes)


@pytest.fixture(scope="module")
def backend(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveBackend]:
    root = tmp_path_factory.mktemp("real-cli-backend")
    runtime = root / "runtime"
    for line in (ROOT / "scripts/release/runtime_assets.txt").read_text().splitlines():
        if line and not line.startswith("#"):
            destination = runtime / line
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / line, destination)
    token = secrets.token_urlsafe(32)
    port_file, log_path = root / "port", root / "backend.log"
    # This is the unmodified production app and routes, with an owned socket
    # and fresh runtime. No inherited provider keys or local .env are present.
    startup = """
import os, socket, sys
from pathlib import Path
import uvicorn
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    os.environ['BACKEND_PORT'] = str(port)
    Path(sys.argv[1]).write_text(str(port))
    uvicorn.Server(uvicorn.Config('app.main:create_app', factory=True, host='127.0.0.1',
        port=port, access_log=False, log_level='warning')).run(sockets=[listener])
"""
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TEMP", "TMP") if key in os.environ}
    env.update({"PYTHONPATH": str(ROOT / "backend"), "PYTHONNOUSERSITE": "1", "MARS_RUNTIME_ROOT": str(runtime),
                "MARS_RUNTIME_MODE": "development", "MARS_DISTRIBUTION": "v30-core", "MARS_MOCK_MODE": "never",
                "MARS_CORS_ORIGINS": "http://127.0.0.1", "MARS_DESKTOP_SESSION_TOKEN": token,
                "MARS_ENABLE_NETWORK_TOOLS": "false", "MARS_LOG_LEVEL": "WARNING"})
    with log_path.open("wb") as log:
        process = subprocess.Popen([sys.executable, "-c", startup, str(port_file)], cwd=root, env=env, stdout=log, stderr=log)
    try:
        deadline = time.monotonic() + 30
        with httpx.Client(trust_env=False, timeout=0.3, headers={"X-MARS-Desktop-Token": token}) as client:
            while time.monotonic() < deadline:
                assert process.poll() is None, "Real backend failed to start: " + log_path.read_text().replace(token, "[REDACTED]")
                if port_file.exists() and port_file.read_text():
                    origin = "http://127.0.0.1:" + port_file.read_text()
                    try:
                        ready = client.get(origin + "/health")
                        if ready.status_code == 200 and ready.json().get("service") == "mars-backend":
                            yield LiveBackend(origin, token, runtime)
                            break
                    except httpx.HTTPError:
                        pass
                time.sleep(0.05)
            else:
                pytest.fail("Real backend readiness timed out")
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        assert token not in log_path.read_text()


def project_input(root: Path) -> dict[str, Any]:
    code = root / "code"
    code.mkdir()
    # A tripwire: preparation is not allowed to execute this declaration.
    (code / "evaluate.py").write_text("raise RuntimeError('preparation must never run commands')\n")
    (code / "baseline.py").write_text("# Human-authored baseline input; no measurement.\n")
    return {"project_id": "cli_regression", "display_name": "CLI contract admission",
        "paths": {"code": str(code), "knowledge": [], "data": [], "output": str(root / "results")},
        "commands": [{"name": purpose, "purpose": purpose, "executable": sys.executable,
                      "arguments": ["evaluate.py"], "entrypoint_files": ["evaluate.py"]}
                     for purpose in ("check", "train", "evaluate")],
        "metrics": [{"name": "MSE", "unit": "unitless", "direction": "minimize", "target": 0, "tolerance": 0}],
        "baseline_files": ["baseline.py"], "allowed_paths": ["candidate.py"], "protected_paths": [],
        "execution": {"kind": "local", "device": "cpu"}}


async def create_run(backend: LiveBackend, *, seed: bool = False) -> str:
    payload: dict[str, Any] = {"project": "synthetic_regression", "task": "client-human-input",
                               "entrypoint": "idea", "standalone": True, "auto_approve": False}
    if seed:
        payload["seed_artifact"] = """---
schema: proposal.v1
project: synthetic_regression
agent: idea
research_question: Can a human-authored draft enter the actual review lifecycle?
hypothesis: The runtime should await human approval without calling a model.
novelty: This is lifecycle input and does not report research or Agent success.
---
Manually authored test input. No experiment or model response is claimed.
"""
    async with httpx.AsyncClient(base_url=backend.origin, trust_env=False,
                                 headers={"X-MARS-Desktop-Token": backend.token}) as client:
        response = await client.post("/api/runs", json=payload)
        assert response.status_code == 200, response.text.replace(backend.token, "[REDACTED]")
        return str(response.json()["run_id"])


@pytest.mark.asyncio
async def test_real_defaults_preflight_prepare_share_backend_without_execution(backend: LiveBackend, tmp_path: Path) -> None:
    project = project_input(tmp_path)
    with environment(MARS_DESKTOP_SESSION_TOKEN=backend.token):
        async with backend.client() as client:
            before = await client.list_runs()
            defaults = await client.defaults()
            preflight = await client.preflight(project)
            prepared = await client.prepare(project=project, goal="Compare measured validation MSE", mode="manual", budget=defaults.payload)
            assert defaults.ok and preflight.ok and preflight.payload["ready"] is True
            assert prepared.ok and prepared.payload["task"]["goal"] == "Compare measured validation MSE"
            assert prepared.payload["task"]["budget"] == defaults.payload
            assert len(prepared.payload["task_sha256"]) == 64
            assert (await client.list_runs()).payload == before.payload
            async with httpx.AsyncClient(base_url=backend.origin, trust_env=False,
                                         headers={"X-MARS-Desktop-Token": backend.token}) as raw:
                response = await raw.post("/api/research-contracts/prepare", json={"project": project,
                    "goal": "Compare measured validation MSE", "mode": "manual", "budget": defaults.payload})
                assert response.status_code == prepared.status_code and response.json() == prepared.payload
            (Path(project["paths"]["code"]) / "evaluate.py").unlink()
            preflight = await client.preflight(project)
            rejected = await client.prepare(project=project, goal="Missing inputs must block", mode="manual", budget=defaults.payload)
            assert preflight.ok and preflight.payload["ready"] is False
            assert rejected.status_code == 422 and rejected.ok is False
            assert rejected.payload["detail"]["ready"] is False
    assert not (tmp_path / "results").exists()


@pytest.mark.asyncio
async def test_real_owner_start_review_stop_and_failed_resume(backend: LiveBackend) -> None:
    run_id = await create_run(backend, seed=True)
    with environment(MARS_DESKTOP_SESSION_TOKEN=backend.token):
        async with backend.client() as client:
            initial = await client.detail(run_id)
            assert initial.ok and initial.payload["states"]["idea"] == "pending"
            listing = await client.list_runs("synthetic_regression")
            assert any(item["run_id"] == run_id for item in listing.payload)
            assert (await client.list_runs("synthetic_regression&project=pimc")).payload == []
            denied = await client.resume(run_id)
            assert denied.status_code == 409 and denied.payload["detail"]["status"] == "no_interrupted_loop"
            started = await client.start(run_id)
            assert started.status_code == 202 and started.payload["status"] == "started"
            try:
                async with asyncio.timeout(5):
                    while (await client.detail(run_id)).payload["states"]["idea"] != "waiting_review":
                        await asyncio.sleep(0.02)
                async with backend.client() as second_client:
                    again = await second_client.start(run_id)
                assert again.status_code == 202 and again.payload["status"] == "already_running"
            finally:
                stopped = await client.stop(run_id)
            assert stopped.status_code == 202 and stopped.payload["status"] == "stopped"
            assert stopped.payload["termination"]["cleanup_complete"] is True
            refused = await client.start(run_id)
            assert refused.status_code == 409 and refused.payload["detail"]["status"] == "not_startable"
            missing = await client.detail("missing-run")
            assert missing.status_code == 404 and missing.payload == {"detail": "run not found"}
    run = backend.runtime / "runs" / run_id
    assert not (run / "agent_traces").exists()
    assert not (run / "resources/model_budget.v1.json").exists()
    assert (run / "run_state.authority.json").is_file()


@pytest.mark.asyncio
async def test_history_remains_read_only_through_every_client_control(backend: LiveBackend) -> None:
    from app.storage.run_store import RunStore

    run = RunStore(backend.runtime / "runs").create(task="human-archival-input", project="synthetic_regression", entrypoint="idea")
    before = {path.relative_to(run.root): path.read_bytes() for path in run.root.rglob("*") if path.is_file()}
    with environment(MARS_DESKTOP_SESSION_TOKEN=backend.token):
        async with backend.client() as client:
            detail = await client.detail(run.run_id)
            assert detail.ok and detail.payload["read_only"] is True
            assert detail.payload["read_only_reason"] == "missing_persisted_state"
            for method in (client.start, client.stop, client.resume):
                rejected = await method(run.run_id)
                assert rejected.status_code == 409 and rejected.ok is False
    assert {path.relative_to(run.root): path.read_bytes() for path in run.root.rglob("*") if path.is_file()} == before


@pytest.mark.asyncio
async def test_auth_is_only_from_process_environment_and_response_is_redacted(backend: LiveBackend, tmp_path: Path) -> None:
    # A real .env file must not be read by this standalone client.
    (tmp_path / ".env").write_text("MARS_DESKTOP_SESSION_TOKEN=" + backend.token + "\n")
    old = Path.cwd()
    try:
        os.chdir(tmp_path)
        with environment(MARS_DESKTOP_SESSION_TOKEN=None):
            async with backend.client() as client:
                denied = await client.list_runs()
                assert denied.status_code == 401 and denied.payload == {"detail": "Desktop session required"}
    finally:
        os.chdir(old)
    with environment(MARS_DESKTOP_SESSION_TOKEN=secrets.token_urlsafe(32)):
        async with backend.client() as client:
            assert (await client.defaults()).status_code == 401
    with environment(MARS_DESKTOP_SESSION_TOKEN=backend.token, HTTP_PROXY="http://127.0.0.1:1",
                     HTTPS_PROXY="http://127.0.0.1:1", ALL_PROXY="http://127.0.0.1:1", NO_PROXY=""):
        async with backend.client() as client:
            assert (await client.defaults()).ok
            # Actual FastAPI validation includes the submitted invalid value.
            reply = await client.preflight({"project_id": "invalid/" + backend.token})
            assert reply.status_code == 422 and "[REDACTED]" in json.dumps(reply.payload)
            assert backend.token not in repr(reply) and backend.token not in repr(client)


@pytest.mark.asyncio
async def test_real_backend_redirect_html_and_oversized_json_are_rejected(backend: LiveBackend) -> None:
    with environment(MARS_DESKTOP_SESSION_TOKEN=backend.token):
        async with backend.client() as client:
            with pytest.raises(RuntimeClientError) as redirect:
                await client._request("GET", "/api/runs/")
            assert redirect.value.code == "redirect_refused" and redirect.value.status_code == 307
            assert backend.token not in str(redirect.value)
            with pytest.raises(RuntimeClientError) as html:
                await client._request("GET", "/docs")
            assert html.value.code == "invalid_response" and html.value.status_code == 200
        async with backend.client(max_response_bytes=8) as client:
            with pytest.raises(RuntimeClientError) as too_large:
                await client.defaults()
            assert too_large.value.code == "response_too_large" and too_large.value.status_code == 200


@pytest.mark.asyncio
async def test_missing_backend_fails_without_creating_runtime_or_fallback(tmp_path: Path) -> None:
    before = list(tmp_path.iterdir())
    # Bound but non-listening OS socket: genuine unavailability. Some platforms
    # reject immediately; others let the connection deadline expire.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        with environment(MARS_DESKTOP_SESSION_TOKEN=None):
            async with RuntimeClient(f"http://127.0.0.1:{port}", timeout_seconds=0.5, max_response_bytes=1024) as client:
                with pytest.raises(RuntimeClientError) as failure:
                    await client.list_runs()
                assert failure.value.code in {"connection_failed", "timeout"} and failure.value.status_code is None
    assert list(tmp_path.iterdir()) == before


@pytest.mark.parametrize("url", ["http://example.org", "http://127.0.0.1.example.org", "http://192.168.1.1",
    "http://0.0.0.0", "http://[::]", "http://[::ffff:127.0.0.1]", "http://user:secret@127.0.0.1",
    "http://127.0.0.1/api", "http://127.0.0.1?token=secret", "http://127.0.0.1#secret",
    "http://127.0.0.1?", "http://127.0.0.1#", "http://127.0.0.1:0", "http://127.0.0.1:65536",
    "http://127.1", "http://2130706433", "http://127.0.0.1\\@example.org", "http://127.0.0.1\n",
    "http://[::1%25lo0]", "ftp://127.0.0.1", "http://localhost.", "http://local%68ost"])
def test_non_loopback_and_ambiguous_origins_rejected_without_echoing_input(url: str) -> None:
    with pytest.raises(ValueError) as failure:
        RuntimeClient(url, timeout_seconds=1, max_response_bytes=1024)
    assert url not in str(failure.value) and "secret" not in str(failure.value)


def test_loopback_canonicalization_never_resolves_dns() -> None:
    assert _loopback_origin("http://localhost:8000/") == "http://127.0.0.1:8000"
    assert _loopback_origin("https://127.0.0.1:8000") == "https://127.0.0.1:8000"
    assert _loopback_origin("http://[::1]:8000") == "http://[::1]:8000"


@pytest.mark.asyncio
@pytest.mark.parametrize("run_id", ["../runs", ".", "..", "a/b", "a\\b", "a%2fb", "a?x=y", "a#x", "a\n"])
async def test_run_identifiers_cannot_cross_routes(run_id: str) -> None:
    client = RuntimeClient("http://127.0.0.1:1", timeout_seconds=1, max_response_bytes=1024)
    for method in (client.detail, client.start, client.stop, client.resume):
        with pytest.raises(ValueError, match="literal run identifier"):
            await method(run_id)


@pytest.mark.parametrize("timeout", [True, 0, -1, float("nan"), float("inf")])
def test_request_timeout_must_be_finite_positive(timeout: float) -> None:
    with pytest.raises(ValueError):
        RuntimeClient("http://127.0.0.1", timeout_seconds=timeout, max_response_bytes=1024)


@pytest.mark.parametrize("limit", [True, 0, -1])
def test_response_limit_must_be_positive_integer(limit: int) -> None:
    with pytest.raises(ValueError):
        RuntimeClient("http://127.0.0.1", timeout_seconds=1, max_response_bytes=limit)


@pytest.mark.asyncio
async def test_invalid_session_credential_is_rejected_without_echo() -> None:
    for credential in ("short-secret", "x" * 40 + "\r\n", "x" * 40 + "密"):
        with environment(MARS_DESKTOP_SESSION_TOKEN=credential):
            with pytest.raises(RuntimeClientError) as failure:
                async with RuntimeClient("http://127.0.0.1:1", timeout_seconds=1, max_response_bytes=1024):
                    pytest.fail("invalid credential accepted")
            assert failure.value.code == "invalid_session" and credential not in str(failure.value)


@pytest.mark.asyncio
async def test_client_context_and_request_validation_prevent_unintended_requests(backend: LiveBackend) -> None:
    client = backend.client()
    with pytest.raises(RuntimeClientError) as closed:
        await client.defaults()
    assert closed.value.code == "not_open"
    with environment(MARS_DESKTOP_SESSION_TOKEN=backend.token):
        async with client:
            for route in ("https://example.org", "//example.org/api", "\\\\example.org", "/api/runs\n"):
                with pytest.raises(RuntimeClientError) as invalid_route:
                    await client._request("GET", route)
                assert invalid_route.value.code == "invalid_request"
            with pytest.raises(RuntimeClientError) as invalid_body:
                await client.preflight({"metric": float("nan")})
            assert invalid_body.value.code == "invalid_request"
            with pytest.raises(RuntimeClientError) as duplicate:
                await client.__aenter__()
            assert duplicate.value.code == "already_open"
    with pytest.raises(RuntimeClientError) as closed:
        await client.defaults()
    assert closed.value.code == "not_open"


@asynccontextmanager
async def failing_http_peer(chunks: tuple[bytes, ...], *, delay: float = 0) -> AsyncIterator[str]:
    """Transport-failure fixture, never a successful MARS service substitute.

    Actual TCP emits only HTTP 400 with chunked error bytes, allowing protocol
    limits to be checked without changing the real application's routes.
    """
    tasks: set[asyncio.Task[None]] = set()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        tasks.add(task)
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n")
            await writer.drain()
            for chunk in chunks:
                writer.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                await writer.drain()
                if delay:
                    await asyncio.sleep(delay)
            writer.write(b"0\r\n\r\n")
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            tasks.discard(task)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    finally:
        server.close()
        await server.wait_closed()
        remaining = tuple(tasks)
        for task in remaining:
            task.cancel()
        await asyncio.gather(*remaining, return_exceptions=True)


@pytest.mark.asyncio
async def test_chunked_error_body_is_bounded_without_content_length() -> None:
    async with failing_http_peer((b'{"detail":"', b"x" * 100, b'"}')) as origin:
        with environment(MARS_DESKTOP_SESSION_TOKEN=None):
            async with RuntimeClient(origin, timeout_seconds=1, max_response_bytes=32) as client:
                with pytest.raises(RuntimeClientError) as failure:
                    await client.defaults()
                assert failure.value.code == "response_too_large" and failure.value.status_code == 400


@pytest.mark.asyncio
async def test_slow_error_stream_cannot_reset_the_overall_deadline() -> None:
    async with failing_http_peer((b" ",) * 100, delay=0.02) as origin:
        with environment(MARS_DESKTOP_SESSION_TOKEN=None):
            async with RuntimeClient(origin, timeout_seconds=0.3, max_response_bytes=1024) as client:
                began = time.monotonic()
                with pytest.raises(RuntimeClientError) as failure:
                    await client.defaults()
                assert failure.value.code == "timeout" and failure.value.status_code == 400
                assert time.monotonic() - began < 1.5


@pytest.mark.asyncio
@pytest.mark.parametrize("error_body", [b'{"detail":1e309}', b'{"detail":[-1e309]}', b'{"detail":NaN}', b'{"detail":'])
async def test_invalid_or_nonfinite_json_error_responses_are_not_accepted(error_body: bytes) -> None:
    async with failing_http_peer((error_body,)) as origin:
        with environment(MARS_DESKTOP_SESSION_TOKEN=None):
            async with RuntimeClient(origin, timeout_seconds=1, max_response_bytes=1024) as client:
                with pytest.raises(RuntimeClientError) as failure:
                    await client.defaults()
                assert failure.value.code == "invalid_response" and failure.value.status_code == 400


def cli_process(backend: LiveBackend, directory: Path, *arguments: str, token: str | None) -> subprocess.CompletedProcess[str]:
    """Run the installed entrypoint in a clean OS process against the real owner."""
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TEMP", "TMP") if key in os.environ}
    env.update({"PYTHONPATH": str(ROOT / "backend"), "PYTHONNOUSERSITE": "1",
                "MARS_RUNTIME_ROOT": str(backend.runtime), "MARS_RUNTIME_MODE": "development",
                "MARS_DISTRIBUTION": "v30-core", "MARS_MOCK_MODE": "never", "MARS_ENABLE_NETWORK_TOOLS": "false"})
    if token is not None:
        env["MARS_DESKTOP_SESSION_TOKEN"] = token
    completed = subprocess.run([sys.executable, "-m", "app.cli", *arguments], cwd=directory, env=env,
                               text=True, capture_output=True, timeout=25, check=False)
    for secret in (backend.token, token):
        if secret and any(fragment in completed.stdout or fragment in completed.stderr for fragment in (secret, secret[:12])):
            pytest.fail("CLI stdout/stderr exposed a session credential")
    return completed


@pytest.mark.asyncio
async def test_cli_subprocess_controls_same_backend_owner_without_model_execution(backend: LiveBackend, tmp_path: Path) -> None:
    run_id = await create_run(backend, seed=True)

    def invoke(*arguments: str) -> subprocess.CompletedProcess[str]:
        return cli_process(backend, tmp_path, "run", "--server", backend.origin, *arguments, token=backend.token)

    listed = invoke("list", "--project", "synthetic_regression")
    assert listed.returncode == 0, listed.stderr
    assert any(item["run_id"] == run_id for item in json.loads(listed.stdout)["response"])
    initial = invoke("show", run_id)
    assert initial.returncode == 0 and json.loads(initial.stdout)["response"]["states"]["idea"] == "pending"
    denied = invoke("resume", run_id)
    assert denied.returncode == 2
    assert json.loads(denied.stdout)["http_status"] == 409
    assert json.loads(denied.stdout)["response"]["detail"]["status"] == "no_interrupted_loop"
    started = invoke("start", run_id)
    assert started.returncode == 0 and json.loads(started.stdout)["http_status"] == 202
    assert json.loads(started.stdout)["response"]["status"] == "started"
    try:
        with environment(MARS_DESKTOP_SESSION_TOKEN=backend.token):
            async with backend.client() as client:
                async with asyncio.timeout(5):
                    while (await client.detail(run_id)).payload["states"]["idea"] != "waiting_review":
                        await asyncio.sleep(0.02)
        # The first CLI process is gone; the backend must still own its driver.
        again = invoke("start", run_id)
        assert again.returncode == 0 and json.loads(again.stdout)["response"]["status"] == "already_running"
    finally:
        stopped = invoke("stop", run_id)
    assert stopped.returncode == 0 and json.loads(stopped.stdout)["http_status"] == 202
    assert json.loads(stopped.stdout)["response"]["termination"]["cleanup_complete"] is True
    after = invoke("show", run_id)
    assert after.returncode == 0
    detail = json.loads(after.stdout)["response"]
    assert detail["states"]["idea"] == "waiting_review" and detail["termination"]["type"] == "cancelled"
    for action in ("start", "resume"):
        blocked = invoke(action, run_id)
        assert blocked.returncode == 2 and json.loads(blocked.stdout)["http_status"] == 409
    missing = invoke("show", "missing-cli-subprocess-run")
    assert missing.returncode == 2 and json.loads(missing.stdout)["http_status"] == 404
    run_root = backend.runtime / "runs" / run_id
    assert (run_root / "run_state.authority.json").is_file()
    assert not (run_root / "agent_traces").exists() and not (run_root / "resources/model_budget.v1.json").exists()


@pytest.mark.parametrize("custom_budget", [False, True])
def test_cli_subprocess_remote_contract_hash_matches_direct_api_and_never_overwrites(
    backend: LiveBackend, tmp_path: Path, custom_budget: bool,
) -> None:
    project = project_input(tmp_path)
    config = tmp_path / "project.yaml"
    config.write_text(json.dumps(project), encoding="utf-8")  # JSON is also valid YAML.
    goal = "Compare the declared metric without starting research"
    output = tmp_path / "frozen-task.json"

    def invoke(*arguments: str) -> subprocess.CompletedProcess[str]:
        return cli_process(backend, tmp_path, "project", "--server", backend.origin, *arguments, token=backend.token)

    with httpx.Client(base_url=backend.origin, trust_env=False, timeout=5,
                      headers={"X-MARS-Desktop-Token": backend.token}) as direct:
        before = direct.get("/api/runs").json()
        budgets = direct.get("/api/research-contracts/defaults").json()
        defaults = invoke("defaults")
        assert defaults.returncode == 0 and json.loads(defaults.stdout) == budgets
        budget_arguments: list[str] = []
        if custom_budget:
            budgets["operation_retries"] = 0
            budget_path = tmp_path / "budget.yaml"
            budget_path.write_text(json.dumps(budgets), encoding="utf-8")
            budget_arguments = ["--budget", str(budget_path)]
        checked = invoke("preflight", "--config", str(config))
        assert checked.returncode == 0 and json.loads(checked.stdout)["ready"] is True
        expected = direct.post("/api/research-contracts/prepare", json={"project": project, "goal": goal,
            "mode": "manual", "budget": budgets})
        assert expected.status_code == 200
        frozen = invoke("freeze", "--config", str(config), "--goal", goal, "--mode", "manual", "--output", str(output), *budget_arguments)
        assert frozen.returncode == 0, frozen.stderr
        report = json.loads(frozen.stdout)
        assert report["status"] == "prepared" and report["research_started"] is False
        assert report["task_sha256"] == expected.json()["task_sha256"]
        assert json.loads(output.read_text()) == expected.json()
        original = output.read_bytes()
        duplicate = invoke("freeze", "--config", str(config), "--goal", goal, "--mode", "manual", "--output", str(output), *budget_arguments)
        assert duplicate.returncode != 0 and output.read_bytes() == original
        assert direct.get("/api/runs").json() == before
        (Path(project["paths"]["code"]) / "evaluate.py").unlink()
        rejected = invoke("preflight", "--config", str(config))
        assert rejected.returncode == 2 and json.loads(rejected.stdout)["ready"] is False
        missing_output = tmp_path / "must-not-exist.json"
        rejected_freeze = invoke("freeze", "--config", str(config), "--goal", goal, "--output", str(missing_output))
        assert rejected_freeze.returncode == 2 and json.loads(rejected_freeze.stdout)["http_status"] == 422
        assert not missing_output.exists() and not (tmp_path / "results").exists()
        assert direct.get("/api/runs").json() == before


@pytest.mark.parametrize("credential", ["missing", "wrong"])
def test_cli_subprocess_real_401_is_nonzero_and_never_prints_credentials(backend: LiveBackend, tmp_path: Path, credential: str) -> None:
    # A local credential file must not silently authorize the HTTP client.
    (tmp_path / ".env").write_text("MARS_DESKTOP_SESSION_TOKEN=" + backend.token + "\n", encoding="utf-8")
    supplied = None if credential == "missing" else secrets.token_urlsafe(32)
    response = cli_process(backend, tmp_path, "run", "--server", backend.origin, "list", token=supplied)
    assert response.returncode == 2
    payload = json.loads(response.stdout)
    assert payload["http_status"] == 401 and payload["http_ok"] is False
    assert payload["response"] == {"detail": "Desktop session required"}
    assert "Traceback" not in response.stderr


def test_cli_subprocess_local_validation_error_never_echoes_session_token(backend: LiveBackend, tmp_path: Path) -> None:
    project = project_input(tmp_path)
    # A secret accidentally pasted into a wrong field must not be echoed by
    # local Pydantic diagnostics before an HTTP request can redact the response.
    project["execution"]["device"] = backend.token
    path = tmp_path / "invalid-project.yaml"
    path.write_text(json.dumps(project), encoding="utf-8")
    response = cli_process(backend, tmp_path, "project", "--server", backend.origin,
                           "preflight", "--config", str(path), token=backend.token)
    assert response.returncode != 0
    assert "Traceback" not in response.stderr


@pytest.mark.parametrize("invalid_argument", ["choice", "unknown_option"])
def test_cli_subprocess_invalid_arguments_never_echo_session_token(
    backend: LiveBackend, tmp_path: Path, invalid_argument: str,
) -> None:
    if invalid_argument == "choice":
        arguments = ["project", "--server", backend.origin, "freeze", "--config", "unused.yaml",
                     "--goal", "No research execution", "--output", "unused.json", "--mode", backend.token]
    else:
        arguments = ["run", "--server", backend.origin, "list", "--unexpected", backend.token]
    response = cli_process(backend, tmp_path, *arguments, token=backend.token)
    assert response.returncode == 2
    assert "Traceback" not in response.stderr
    assert not (tmp_path / "unused.json").exists()


def test_cli_subprocess_creates_real_contract_run_under_shared_owner_without_starting(
    backend: LiveBackend, tmp_path: Path,
) -> None:
    project = project_input(tmp_path)
    config = tmp_path / "project.yaml"
    config.write_text(json.dumps(project), encoding="utf-8")
    contract = tmp_path / "frozen.json"

    def project_command(*arguments: str) -> subprocess.CompletedProcess[str]:
        return cli_process(backend, tmp_path, "project", "--server", backend.origin, *arguments, token=backend.token)

    frozen = project_command("freeze", "--config", str(config), "--goal", "Read the declared task before execution",
                             "--mode", "manual", "--output", str(contract))
    assert frozen.returncode == 0 and json.loads(frozen.stdout)["research_started"] is False
    saved = contract.read_bytes()
    with httpx.Client(base_url=backend.origin, trust_env=False, timeout=5,
                      headers={"X-MARS-Desktop-Token": backend.token}) as direct:
        before = direct.get("/api/runs").json()
        denied = cli_process(backend, tmp_path, "project", "--server", backend.origin, "create",
                             "--contract", str(contract), "--name", "Unauthorized", token=None)
        assert denied.returncode == 2 and json.loads(denied.stdout)["http_status"] == 401
        assert direct.get("/api/runs").json() == before
        created = project_command("create", "--contract", str(contract), "--name", "CLI contract admission")
        assert created.returncode == 0, created.stderr
        report = json.loads(created.stdout)
        assert report["http_status"] == 201 and report["http_ok"] is True
        receipt = report["response"]
        assert receipt["status"] == "created" and receipt["research_started"] is False
        assert receipt["task_sha256"] == json.loads(saved)["task_sha256"]
        run_id = receipt["run_id"]
        assert len(direct.get("/api/runs").json()) == len(before) + 1
        shown = cli_process(backend, tmp_path, "run", "--server", backend.origin, "show", run_id, token=backend.token)
        assert shown.returncode == 0
        assert json.loads(shown.stdout)["response"] == direct.get("/api/runs/" + run_id).json()
        assert json.loads(shown.stdout)["response"]["execution_admission"]["ready"] is False
        assert set(json.loads(shown.stdout)["response"]["states"].values()) == {"pending"}
        root = backend.runtime / "runs" / run_id
        assert json.loads((root / "input/research_task.v1.json").read_bytes()) == json.loads(saved)
        state_before = (root / "run_state.sqlite3").read_bytes()
        for action in ("start", "resume"):
            blocked = cli_process(backend, tmp_path, "run", "--server", backend.origin, action, run_id, token=backend.token)
            assert blocked.returncode == 2
            assert json.loads(blocked.stdout)["response"]["detail"]["status"] == "contract_execution_blocked"
        assert (root / "run_state.sqlite3").read_bytes() == state_before
        assert not (root / "resources").exists() and not (root / "agent_traces").exists()
        (Path(project["paths"]["code"]) / "baseline.py").write_text("# Changed after freeze\n")
        stale = project_command("create", "--contract", str(contract), "--name", "Stale")
        assert stale.returncode == 2 and json.loads(stale.stdout)["http_status"] == 409
        assert len(direct.get("/api/runs").json()) == len(before) + 1
        assert contract.read_bytes() == saved


def test_cli_subprocess_create_requires_explicit_backend_and_cannot_fall_back_locally(backend: LiveBackend, tmp_path: Path) -> None:
    result = cli_process(backend, tmp_path, "project", "create", "--contract", "not-read.json", "--name", "No local owner", token=backend.token)
    assert result.returncode == 2 and json.loads(result.stdout)["error_code"] == "server_required"
    assert list(tmp_path.iterdir()) == []


def _cli_frozen_file(directory: Path) -> tuple[Path, dict[str, Any]]:
    from tests.unit.test_research_run_admission import frozen_input
    payload = frozen_input(directory).model_dump(mode="json")
    path = directory / "cli-frozen.json"
    path.write_text(json.dumps(payload))
    return path, payload


def test_cli_explicit_id_replays_and_lookup_requires_no_source(backend: LiveBackend, tmp_path: Path) -> None:
    contract, payload = _cli_frozen_file(tmp_path)
    request_id = "cli-idempotent-save"
    arguments = ("project", "--server", backend.origin, "create", "--contract", str(contract),
                 "--name", "CLI idempotent task", "--request-id", request_id)
    created = cli_process(backend, tmp_path, *arguments, token=backend.token)
    assert created.returncode == 0, created.stderr
    report = json.loads(created.stdout)
    assert report["creation_confirmed"] is True and report["contract_match"] is True
    assert report["response"]["idempotent"] is True and report["response"]["request_id"] == request_id
    shutil.rmtree(tmp_path / "code")
    repeated = cli_process(backend, tmp_path, *arguments, token=backend.token)
    assert repeated.returncode == 0 and json.loads(repeated.stdout) == report
    contract.unlink()
    lookup_args = ("project", "--server", backend.origin, "request-status", request_id)
    lookup = cli_process(backend, tmp_path, *lookup_args, "--task-sha256", payload["task_sha256"], token=backend.token)
    assert lookup.returncode == 0
    verified = json.loads(lookup.stdout)
    assert verified["creation_confirmed"] and verified["contract_match"] is True
    assert verified["response"]["run"] == report["response"]
    without_hash = cli_process(backend, tmp_path, *lookup_args, token=backend.token)
    assert without_hash.returncode == 0 and json.loads(without_hash.stdout)["contract_match"] is None
    mismatch = cli_process(backend, tmp_path, *lookup_args, "--task-sha256", "0" * 64, token=backend.token)
    assert mismatch.returncode == 2 and json.loads(mismatch.stdout)["error_code"] == "creation_response_mismatch"
    assert "Traceback" not in mismatch.stderr


def test_cli_id_conflict_does_not_allocate_another_run(backend: LiveBackend, tmp_path: Path) -> None:
    contract, _ = _cli_frozen_file(tmp_path)
    base = ("project", "--server", backend.origin, "create", "--contract", str(contract), "--request-id", "cli-body-conflict")
    first = cli_process(backend, tmp_path, *base, "--name", "Original CLI task", token=backend.token)
    assert first.returncode == 0
    conflict = cli_process(backend, tmp_path, *base, "--name", "Different CLI task", token=backend.token)
    assert conflict.returncode == 2
    report = json.loads(conflict.stdout)
    assert report["http_status"] == 409 and report["creation_confirmed"] is False
    assert report["response"]["detail"]["code"] == "creation_request_conflict"


def test_cli_pending_and_unknown_are_nonzero_without_resubmission(backend: LiveBackend, tmp_path: Path) -> None:
    from tests.unit.test_research_creation import interrupted_creation
    contract, frozen = _cli_frozen_file(tmp_path)
    payload: dict[str, Any] = {"name": "CLI unknown outcome", "contract": frozen, "request_id": "cli-pending-to-unknown"}
    child = tmp_path / "child"
    child.mkdir()
    lookup_args = ("project", "--server", backend.origin, "request-status", payload["request_id"], "--task-sha256", frozen["task_sha256"])
    create_args = ("project", "--server", backend.origin, "create", "--contract", str(contract), "--name", payload["name"], "--request-id", payload["request_id"])
    with interrupted_creation(backend.runtime / "runs", child, payload, "intent") as process:
        for arguments in (lookup_args, create_args):
            pending = cli_process(backend, tmp_path, *arguments, token=backend.token)
            report = json.loads(pending.stdout)
            assert pending.returncode == 2 and report["creation_confirmed"] is False
            assert report["http_ok"] is True and report["response"]["status"] == "pending"
        process.kill()
        process.wait(timeout=5)
        lookup = cli_process(backend, tmp_path, *lookup_args, token=backend.token)
        assert lookup.returncode == 2 and json.loads(lookup.stdout)["response"]["status"] == "unknown"
        repeat = cli_process(backend, tmp_path, *create_args, token=backend.token)
        assert repeat.returncode == 2 and json.loads(repeat.stdout)["http_status"] == 409
        assert json.loads(repeat.stdout)["response"]["detail"]["run_id"] is None


def test_cli_rejected_lookup_and_explicit_new_request(backend: LiveBackend, tmp_path: Path) -> None:
    contract, frozen = _cli_frozen_file(tmp_path)
    baseline = tmp_path / "code/baseline.py"
    original = baseline.read_bytes()
    baseline.write_text("# source changed after freezing")
    base = ("project", "--server", backend.origin, "create", "--contract", str(contract), "--name", "CLI preflight rejection")
    rejected = cli_process(backend, tmp_path, *base, "--request-id", "cli-preflight-rejected", token=backend.token)
    assert rejected.returncode == 2
    receipt = json.loads(rejected.stdout)
    assert receipt["creation_confirmed"] is False and receipt["response"]["detail"]["admitted"] is False
    lookup = cli_process(backend, tmp_path, "project", "--server", backend.origin, "request-status", "cli-preflight-rejected",
                         "--task-sha256", frozen["task_sha256"], token=backend.token)
    assert lookup.returncode == 2 and json.loads(lookup.stdout)["response"]["status"] == "rejected"
    baseline.write_bytes(original)
    accepted = cli_process(backend, tmp_path, *base, "--request-id", "cli-preflight-new-request", token=backend.token)
    assert accepted.returncode == 0 and json.loads(accepted.stdout)["creation_confirmed"] is True


@pytest.mark.parametrize("invalid", ["token", "token_prefix", "path"])
def test_cli_request_id_cannot_leak_credentials_or_escape_route(backend: LiveBackend, tmp_path: Path, invalid: str) -> None:
    contract, _ = _cli_frozen_file(tmp_path)
    value = backend.token if invalid == "token" else backend.token[:12] if invalid == "token_prefix" else "../outside"
    for arguments in (
        ("request-status", "--", value),
        ("create", "--contract", str(contract), "--name", "Never submit credential", "--request-id=" + value),
    ):
        result = cli_process(backend, tmp_path, "project", "--server", backend.origin, *arguments, token=backend.token)
        assert result.returncode == 2 and json.loads(result.stdout)["error_code"] == "invalid_request"
        assert "Traceback" not in result.stderr


def test_cli_lookup_missing_unauthorized_and_no_server_are_nonzero(backend: LiveBackend, tmp_path: Path) -> None:
    arguments = ("project", "--server", backend.origin, "request-status", "cli-not-registered")
    missing = cli_process(backend, tmp_path, *arguments, token=backend.token)
    assert missing.returncode == 2 and json.loads(missing.stdout)["http_status"] == 404
    denied = cli_process(backend, tmp_path, *arguments, token=None)
    assert denied.returncode == 2 and json.loads(denied.stdout)["http_status"] == 401
    no_server = cli_process(backend, tmp_path, "project", "request-status", "cli-not-registered", token=backend.token)
    assert no_server.returncode == 2 and json.loads(no_server.stdout)["error_code"] == "server_required"
    assert list(tmp_path.iterdir()) == []


def test_cli_actual_timeout_is_recovered_by_readonly_request_lookup(backend: LiveBackend, tmp_path: Path) -> None:
    from app.storage.research_creation_store import ResearchCreationStore
    contract, frozen = _cli_frozen_file(tmp_path)
    # Give only this CLI process a short, real YAML-configured network deadline.
    cli_runtime = tmp_path / "client-runtime"
    for line in (ROOT / "scripts/release/runtime_assets.txt").read_text().splitlines():
        if line and not line.startswith("#"):
            destination = cli_runtime / line
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / line, destination)
    (cli_runtime / "configs/cli_runtime.yaml").write_text("schema_id: cli_runtime.v1\ntimeout_seconds: 0.05\nmax_response_bytes: 1048576\n")
    short_client = LiveBackend(backend.origin, backend.token, cli_runtime)
    catalog = ResearchCreationStore(backend.runtime / "runs")
    catalog.initialize()
    with catalog.connection(writable=True) as connection:
        connection.execute("BEGIN EXCLUSIVE")
        try:
            timed_out = cli_process(short_client, tmp_path, "project", "--server", backend.origin, "create", "--contract", str(contract),
                "--name", "CLI actual timeout", "--request-id", "cli-real-timeout", token=backend.token)
            assert timed_out.returncode == 2, timed_out.stderr
            assert json.loads(timed_out.stdout)["error_code"] == "timeout"
            assert "request-status" in timed_out.stderr
        finally:
            connection.rollback()
    # Explicit lookup calls are reads. Neither helper submits another creation.
    deadline = time.monotonic() + 10
    while True:
        lookup = cli_process(backend, tmp_path, "project", "--server", backend.origin, "request-status", "cli-real-timeout",
            "--task-sha256", frozen["task_sha256"], token=backend.token)
        if lookup.returncode == 0:
            break
        assert time.monotonic() < deadline, lookup.stdout + lookup.stderr
        time.sleep(0.01)
    report = json.loads(lookup.stdout)
    assert report["creation_confirmed"] is True and report["response"]["run"]["research_started"] is False



@pytest.mark.parametrize("damage", ["request_id", "hash", "started", "idempotent", "envelope_hash", "nested_run_id"])
def test_cli_creation_parser_rejects_authored_inconsistent_evidence(damage: str) -> None:
    # Pure parser inputs only; these are never a provider, tool or service result.
    from app.cli import _creation_response
    from app.cli_runtime_client import RuntimeResponse
    run: dict[str, Any] = {"status": "created", "research_started": False, "request_id": "parser-request-id",
        "idempotent": True, "entrypoint": "pipeline", "run_id": "authored_parser_input", "task": "parser input",
        "project": "parser", "created_at": "2026-09-28T00:00:00Z", "task_sha256": "1" * 64,
        "execution_admission": {"ready": False}}
    envelope: dict[str, Any] = {"request_id": "parser-request-id", "task_sha256": "1" * 64,
        "status": "created", "admitted": True, "research_started": False,
        "run_id": "authored_parser_input", "run": run, "reason": None}
    if damage == "envelope_hash":
        envelope["task_sha256"] = "2" * 64
    elif damage == "nested_run_id":
        run["run_id"] = "another_parser_input"
    else:
        field = {"request_id": "request_id", "hash": "task_sha256", "started": "research_started", "idempotent": "idempotent"}[damage]
        run[field] = {"request_id": "another-request-id", "hash": "2" * 64, "started": True, "idempotent": False}[damage]
    with pytest.raises(RuntimeClientError) as failure:
        _creation_response(RuntimeResponse(200, envelope), request_id="parser-request-id", expected_task_sha256="1" * 64, lookup=True)
    assert failure.value.code == "creation_response_mismatch"
