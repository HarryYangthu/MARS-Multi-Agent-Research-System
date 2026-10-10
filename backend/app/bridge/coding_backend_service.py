"""Product-level coding engine selection; the executor remains in harness."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from app.harness.agent_loop.zcode.config import ZCodeConfig
from app.settings import get_settings, set_runtime_env


def coding_backend_status() -> dict[str, Any]:
    installed = False
    reason = ""
    try:
        ZCodeConfig.load().resolve_command()
        installed = True
    except (ValueError, OSError) as exc:
        reason = str(exc)
    return {"selected": get_settings().mars_coding_backend, "zcode_available": installed,
            "reason": reason, "applies_to": "new_invocations", "model_source": "coding_agent"}


def select_coding_backend(backend: Literal["zcode", "native_llm"], *, persist: Callable[[], None]) -> dict[str, Any]:
    if backend == "zcode":
        view = coding_backend_status()
        if not view["zcode_available"]:
            raise ValueError(view["reason"] or "ZCode is unavailable")
    persist()
    set_runtime_env({"MARS_CODING_BACKEND": backend})
    return coding_backend_status()
