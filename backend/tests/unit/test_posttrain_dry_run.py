"""The removed checkpoint generator must not masquerade as training."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.harness.llm.post_training_loader import load_handle


def test_no_fabricated_checkpoint_generator_is_importable() -> None:
    assert importlib.util.find_spec("mars_posttrain.dry_run") is None


@pytest.mark.parametrize("mode", ["dry_run", "cpu_mock"])
def test_posttraining_rejects_removed_training_modes(mode: str) -> None:
    with pytest.raises(ValueError, match="unknown post_training mode"):
        load_handle({"enabled": True, "mode": mode})


def test_load_only_does_not_write_a_checkpoint(tmp_path: Path) -> None:
    handle = load_handle({"enabled": False, "mode": "load_only", "live_checkpoint_path": str(tmp_path / "checkpoint")})
    assert not handle.enabled
    assert handle.live_checkpoint_path is None
    assert list(tmp_path.iterdir()) == []
