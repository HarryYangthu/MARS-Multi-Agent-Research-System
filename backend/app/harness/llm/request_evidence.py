"""Record the outbound model body, without transport credentials or private reasoning."""
from typing import Any

from app.harness.llm.provider_base import LLMConfig

BODY_FIELDS = frozenset({"model", "messages", "contents", "system", "systemInstruction", "tools",
                        "max_tokens", "temperature", "top_p", "reasoning_effort", "response_format",
                        "stream", "generationConfig", "parallel_tool_calls", "tool_choice", "thinking"})


def record_provider_request(config: LLMConfig, provider: str, body: dict[str, Any]) -> None:
    if config.attempt_observer is None:
        return
    payload = {key: value for key, value in body.items() if key in BODY_FIELDS}
    extra = body.get("extra_body")
    if isinstance(extra, dict):
        # The OpenAI SDK merges extra_body into the HTTP JSON body.
        payload.update({key: value for key, value in extra.items() if key in {"thinking", "reasoning_effort"}})
    config.attempt_observer("provider_request", {"provider": provider, "model": config.model, "wire_payload": payload})
