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

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

PostTrainingMode = Literal["load_only", "adapter", "endpoint", "fine_tuned_id"]


@dataclass(frozen=True)
class PostTrainingHandle:
    enabled: bool
    mode: PostTrainingMode
    adapter_path: Path | None = None
    custom_endpoint: str | None = None
    fine_tuned_model_id: str | None = None
    live_checkpoint_path: Path | None = None


def load_handle(config: dict[str, str | bool]) -> PostTrainingHandle:
    """Validate and produce a PostTrainingHandle from the YAML sub-tree.

    Raises ValueError if required fields for the chosen mode are missing.
    """
    enabled = bool(config.get("enabled", False))
    mode_raw = str(config.get("mode", "load_only"))
    if mode_raw not in {"load_only", "adapter", "endpoint", "fine_tuned_id"}:
        raise ValueError(f"unknown post_training mode '{mode_raw}'")
    mode: PostTrainingMode = mode_raw  # type: ignore[assignment]

    adapter = config.get("adapter_path") or config.get("lora_adapter_path")
    endpoint = config.get("custom_endpoint")
    ft_id = config.get("fine_tuned_model_id")
    live = config.get("live_checkpoint_path")

    if not enabled:
        return PostTrainingHandle(enabled=False, mode="load_only")

    if mode == "adapter" and not adapter:
        raise ValueError("post_training mode=adapter requires adapter_path")
    if mode == "endpoint" and not endpoint:
        raise ValueError("post_training mode=endpoint requires custom_endpoint")
    if mode == "fine_tuned_id" and not ft_id:
        raise ValueError(
            "post_training mode=fine_tuned_id requires fine_tuned_model_id"
        )

    return PostTrainingHandle(
        enabled=enabled,
        mode=mode,
        adapter_path=Path(str(adapter)) if adapter else None,
        custom_endpoint=str(endpoint) if endpoint else None,
        fine_tuned_model_id=str(ft_id) if ft_id else None,
        live_checkpoint_path=Path(str(live)) if live else None,
    )
