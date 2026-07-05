from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import reset_for_tests
from app.bridge.agent_registry import reset_registry_for_tests
from app.main import create_app


@pytest.fixture
def client() -> TestClient:
    reset_for_tests()
    reset_registry_for_tests()
    return TestClient(create_app())


def test_get_coding_post_training_status(client: TestClient) -> None:
    response = client.get("/api/agents/coding/post-training")

    assert response.status_code == 200
    body = response.json()
    assert body["agent"] == "coding"
    assert body["enabled"] is True
    assert body["mode"] == "endpoint"
    assert body["provider"] == "local_vllm"
    assert body["model"] == "mars-coding-posttrain"
    assert body["endpoint"] == "http://127.0.0.1:8001/v1"
    assert body["source"] == "config"


def test_load_coding_post_training_runtime_endpoint(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POSTTRAIN_SECRET", "super-secret-value")

    response = client.post(
        "/api/agents/coding/post-training/load",
        json={
            "enabled": True,
            "mode": "endpoint",
            "endpoint_provider": "custom",
            "custom_endpoint": "http://127.0.0.1:9000/v1",
            "model": "mars-coding-posttrain-v2",
            "api_key_env": "POSTTRAIN_SECRET",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "agent": "coding",
        "enabled": True,
        "mode": "endpoint",
        "provider": "custom",
        "model": "mars-coding-posttrain-v2",
        "endpoint": "http://127.0.0.1:9000/v1",
        "source": "runtime",
        "warnings": [],
    }
    assert "super-secret-value" not in json.dumps(body)

    followup = client.get("/api/agents/coding/post-training")
    assert followup.status_code == 200
    assert followup.json()["model"] == "mars-coding-posttrain-v2"
