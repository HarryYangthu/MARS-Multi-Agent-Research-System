"""Post-training artifact loader (V0 = load-only).

V0 only knows how to *reference* a post-trained model so the Coding Agent
can route requests to it. V1 ships the actual GRPO training pipeline.
The four supported modes mirror PRODUCT.md §7.3:

* ``load_only`` — V0 default; nothing is loaded, the registered provider
  serves the configured public model.
* ``adapter`` — points at a LoRA adapter directory; vLLM serve loads it
  via ``--lora-modules``. V0 just records the path.
* ``endpoint`` — points at a custom OpenAI-compatible URL.
* ``fine_tuned_id`` — names a vendor-side fine-tuned model id
  (e.g. OpenAI ``ft:...`` or Anthropic ``ft-...``).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

PostTrainingMode = Literal["load_only", "adapter", "endpoint", "fine_tuned_id"]
EndpointProvider = Literal["local_vllm", "custom"]


@dataclass(frozen=True)
class PostTrainingHandle:
    enabled: bool
    mode: PostTrainingMode
    adapter_path: Path | None = None
    custom_endpoint: str | None = None
    fine_tuned_model_id: str | None = None
    live_checkpoint_path: Path | None = None
    model: str | None = None
    endpoint_provider: EndpointProvider = "local_vllm"
    api_key_env: str | None = None
    source: str = "config"


def _as_bool(value: object, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off", ""}:
        return False
    return default


def _as_optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _endpoint_provider(value: object) -> EndpointProvider:
    raw = _as_optional_str(value) or "local_vllm"
    if raw not in {"local_vllm", "custom"}:
        raise ValueError(
            "post_training endpoint_provider must be 'local_vllm' or 'custom'"
        )
    return raw  # type: ignore[return-value]


def load_handle(
    config: Mapping[str, object], *, source: str = "config"
) -> PostTrainingHandle:
    """Validate and produce a PostTrainingHandle from the YAML sub-tree.

    Raises ValueError if required fields for the chosen mode are missing.
    """
    enabled = _as_bool(config.get("enabled"), default=False)
    mode_raw = str(config.get("mode", "load_only"))
    if mode_raw not in {"load_only", "adapter", "endpoint", "fine_tuned_id"}:
        raise ValueError(f"unknown post_training mode '{mode_raw}'")
    mode: PostTrainingMode = mode_raw  # type: ignore[assignment]

    adapter = _as_optional_str(
        config.get("adapter_path") or config.get("lora_adapter_path")
    )
    endpoint = _as_optional_str(config.get("custom_endpoint"))
    ft_id = _as_optional_str(config.get("fine_tuned_model_id"))
    live = _as_optional_str(config.get("live_checkpoint_path"))
    model = _as_optional_str(config.get("model"))
    endpoint_provider = _endpoint_provider(config.get("endpoint_provider"))
    api_key_env = _as_optional_str(config.get("api_key_env")) or (
        "CUSTOM_ENDPOINT_API_KEY"
        if endpoint_provider == "custom"
        else "LOCAL_VLLM_API_KEY"
    )

    if not enabled:
        return PostTrainingHandle(enabled=False, mode="load_only", source=source)

    if mode == "adapter" and not adapter:
        raise ValueError("post_training mode=adapter requires adapter_path")
    if mode == "endpoint":
        if not endpoint:
            raise ValueError("post_training mode=endpoint requires custom_endpoint")
        if not model:
            raise ValueError("post_training mode=endpoint requires model")
    if mode == "fine_tuned_id" and not ft_id:
        raise ValueError(
            "post_training mode=fine_tuned_id requires fine_tuned_model_id"
        )

    return PostTrainingHandle(
        enabled=enabled,
        mode=mode,
        adapter_path=Path(adapter) if adapter else None,
        custom_endpoint=endpoint,
        fine_tuned_model_id=ft_id,
        live_checkpoint_path=Path(live) if live else None,
        model=model,
        endpoint_provider=endpoint_provider,
        api_key_env=api_key_env,
        source=source,
    )
