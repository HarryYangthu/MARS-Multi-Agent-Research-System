"""Small real completion checks. Never print credentials or raw provider errors."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import yaml

from app.harness.llm.model_registry import get_agent_config, select_provider
from app.harness.llm.provider_base import Message
from app.settings import repo_root


async def check(name: str) -> dict[str, object]:
    agent = get_agent_config(name)
    result: dict[str, object] = {"agent": name, "provider": agent.model_provider, "model": agent.model_name}
    provider = None
    try:
        provider, config = select_provider(agent)
        config = replace(config, max_tokens=128, thinking_enabled=False, reasoning_effort=None,
                         max_retries=0, request_timeout_seconds=30)
        response = await provider.complete([Message("user", "Reply with READY only.")], config)
        result.update(ok=bool(response.text.strip()), response=response.text.strip()[:80])
    except Exception as exc:
        result.update(ok=False, error_type=type(exc).__name__, status_code=getattr(exc, "status_code", None))
    finally:
        if provider is not None:
            await provider.close()
    return result


async def main() -> None:
    profile = yaml.safe_load((repo_root() / "configs/idea_focused.yaml").read_text())
    results = await asyncio.gather(check(profile.get("author_agent", "idea")), check(profile["review_agent"]))
    path = Path("runs/verification/idea_model_preflight.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    from loguru import logger
    logger.info("{}", results)


if __name__ == "__main__":
    asyncio.run(main())
