"""Small real author/reviewer completions; no credentials or provider bodies printed."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from loguru import logger
import yaml

from app.harness.llm.accounting import guarded_complete
from app.harness.llm.model_capabilities import setup_reasoning
from app.harness.llm.model_registry import get_agent_config, select_provider
from app.harness.llm.openai_provider import public_error_details
from app.harness.llm.provider_base import LLMConfig, Message
from app.settings import repo_root


def probe_config(config: LLMConfig) -> LLMConfig:
    defaults = yaml.safe_load((repo_root() / "configs/windows_native.yaml").read_text(encoding="utf-8"))
    thinking, effort = setup_reasoning(config.provider, config.model)
    return replace(config, max_tokens=int(defaults["api_probe_max_tokens"]), max_retries=0,
                   thinking_enabled=thinking, reasoning_effort=effort,
                   request_timeout_seconds=float(defaults["api_probe_timeout_seconds"]))


async def check(name: str, root: Path) -> dict[str, Any]:
    provider = None
    result: dict[str, Any] = {"agent": name, "ok": False}
    try:
        provider, config = select_provider(get_agent_config(name))
        result.update(provider=config.provider, model=config.model)
        config = probe_config(config)
        response = await guarded_complete(provider, [Message("user", "Reply READY only.")], config,
                                          run_root=root, correlation={"purpose": "windows_api_check", "agent": name})
        result.update(ok=bool(response.text.strip()), visible_response=bool(response.text.strip()))
    except Exception as exc:
        result.update(public_error_details(exc))
        if result.get("http_status") == 402:
            result["hint"] = "检查模型账号余额、额度或 Key 对应的账号。"
        elif result.get("http_status") == 401:
            result["hint"] = "检查 API Key。"
        else:
            result["hint"] = "检查服务地址、模型名称、网络和本机模型服务状态。"
    finally:
        if provider is not None:
            await provider.close()
    return result


async def main() -> int:
    profile = yaml.safe_load((repo_root() / "configs/idea_focused.yaml").read_text())
    root = repo_root() / "local/windows/api-checks" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    root.mkdir(parents=True)
    results = [await check(name, root) for name in (profile["author_agent"], profile["review_agent"])]
    (root / "result.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("{}", json.dumps(results, ensure_ascii=False))
    logger.info("这是实际 API 连通性检查，不代表工具调用、独立评审或研究任务已通过。")
    return 0 if all(result["ok"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
