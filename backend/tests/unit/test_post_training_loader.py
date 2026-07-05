from __future__ import annotations

import pytest

from app.harness.llm.post_training_loader import load_handle


def test_endpoint_mode_requires_custom_endpoint() -> None:
    with pytest.raises(ValueError, match="requires custom_endpoint"):
        load_handle(
            {
                "enabled": True,
                "mode": "endpoint",
                "model": "mars-coding-posttrain",
            }
        )


def test_endpoint_mode_requires_model() -> None:
    with pytest.raises(ValueError, match="requires model"):
        load_handle(
            {
                "enabled": True,
                "mode": "endpoint",
                "custom_endpoint": "http://127.0.0.1:8001/v1",
            }
        )


def test_endpoint_mode_loads_sanitized_handle() -> None:
    handle = load_handle(
        {
            "enabled": "true",
            "mode": "endpoint",
            "endpoint_provider": "local_vllm",
            "custom_endpoint": "http://127.0.0.1:8001/v1",
            "model": "mars-coding-posttrain",
            "api_key_env": "LOCAL_VLLM_API_KEY",
        }
    )
    assert handle.enabled is True
    assert handle.mode == "endpoint"
    assert handle.endpoint_provider == "local_vllm"
    assert handle.custom_endpoint == "http://127.0.0.1:8001/v1"
    assert handle.model == "mars-coding-posttrain"
    assert handle.api_key_env == "LOCAL_VLLM_API_KEY"
    assert handle.source == "config"
