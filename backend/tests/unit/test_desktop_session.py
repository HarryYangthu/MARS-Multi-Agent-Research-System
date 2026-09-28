"""Exercise the real MARS ASGI application; no substituted service responses."""
from __future__ import annotations

import secrets
from pathlib import Path

import pytest
from pydantic import SecretStr
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.desktop_session import DesktopSessionMiddleware
from app.main import create_app
from app.settings import Settings, resolve_runtime_root


def test_desktop_session_protects_http_and_websocket() -> None:
    token = secrets.token_urlsafe(32)
    origin = "http://127.0.0.1:43123"
    app = create_app()
    app.add_middleware(DesktopSessionMiddleware, token=token, origins=(origin,))
    with TestClient(app) as client:
        for path in ("/health", "/api/projects", "/docs", "/openapi.json"):
            denied = client.get(path)
            assert denied.status_code == 401
            assert token not in denied.text
        auth = {"X-MARS-Desktop-Token": token}
        assert client.get("/health", headers=auth).json()["service"] == "mars-backend"
        assert client.get("/api/projects", headers={**auth, "Origin": origin}).status_code == 200
        assert client.get("/health", headers={**auth, "Origin": "https://untrusted.example"}).status_code == 403
        assert client.get("/health", headers=[("X-MARS-Desktop-Token", token)] * 2).status_code == 401
        assert client.get("/health", params={"token": token}).status_code == 401
        with pytest.raises(WebSocketDisconnect) as missing:
            with client.websocket_connect("/ws/runs/session-check", headers={"Origin": origin}):
                pytest.fail("unauthenticated socket accepted")
        assert missing.value.code == 1008
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/runs/session-check", headers=auth):
                pytest.fail("socket without application origin accepted")
        with client.websocket_connect("/ws/runs/session-check", headers={**auth, "Origin": origin}):
            pass


def test_desktop_auth_rejects_unsafe_configuration() -> None:
    app = create_app()
    with pytest.raises(ValueError, match="fresh ASCII token"):
        DesktopSessionMiddleware(app, token="short", origins=("http://127.0.0.1",))
    with pytest.raises(ValueError, match="explicit application origins"):
        DesktopSessionMiddleware(app, token=secrets.token_urlsafe(32), origins=("*",))
    token = secrets.token_urlsafe(32)
    settings = Settings(mars_desktop_session_token=SecretStr(token))
    assert token not in repr(settings)
    assert token not in settings.model_dump_json()


def test_runtime_root_requires_explicit_seeded_workspace(tmp_path: Path) -> None:
    source = tmp_path / "installed"
    assert resolve_runtime_root("", source) == source
    with pytest.raises(ValueError, match="absolute"):
        resolve_runtime_root("relative", source)
    with pytest.raises(ValueError, match="missing required"):
        resolve_runtime_root(str(tmp_path), source)
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/agents.yaml").write_text("{}\n")
    (tmp_path / "templates/artifacts").mkdir(parents=True)
    (tmp_path / "backend/app/harness/schema/schemas").mkdir(parents=True)
    assert resolve_runtime_root(str(tmp_path), source) == tmp_path.resolve()
