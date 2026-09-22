"""Local, interactive Windows configuration. No credentials in argv or output."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import getpass
import json
from pathlib import Path
import sys
from typing import Any
from urllib.parse import urlsplit

from loguru import logger
import yaml

ROOT = Path(__file__).resolve().parents[2]


def validate_endpoint(value: str) -> str:
    value = value.strip().rstrip("/")
    url = urlsplit(value)
    if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
            or url.query or url.fragment or any(ch.isspace() for ch in value)):
        raise ValueError("API 地址须是 http(s) URL，不能包含账号、密钥、查询参数或空白。")
    return value


def route_agents(source: dict[str, Any], *, provider: str, model_name: str,
                 endpoint: str, key_env: str) -> dict[str, Any]:
    """Apply explicit routing to every role, including focused author/reviewer."""
    result = deepcopy(source)
    for body in result.values():
        if not isinstance(body, dict) or not isinstance(body.get("model"), dict):
            continue
        model = body["model"]
        model.update(provider=provider, api_key_env=key_env, base_url=endpoint,
                     base_url_env="MARS_WINDOWS_API_BASE_URL")
        if model_name:
            model["model"] = model_name
        if provider != "deepseek":
            model["thinking"] = {"enabled": False}
            model.pop("reasoning_effort", None)
        for participant in body.get("debate", {}).get("participants", []):
            if isinstance(participant, dict):
                participant["provider"] = provider
                if model_name:
                    participant["model"] = model_name
    return result


def update_env(path: Path, values: dict[str, str]) -> None:
    """Preserve unrelated local values and replace every duplicate target key."""
    if any(any(ch in value for ch in '\r\n\x00"\\') for value in values.values()):
        raise ValueError("配置值不能含换行、双引号或反斜杠；路径请使用 /。")
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        key = line.strip().removeprefix("export ").split("=", 1)[0].strip()
        if key in values:
            if key not in seen:
                output.append(key + "=" + json.dumps(values[key], ensure_ascii=False))
                seen.add(key)
        else:
            output.append(line)
    output.extend(key + "=" + json.dumps(value, ensure_ascii=False) for key, value in values.items() if key not in seen)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(output) + "\n", encoding="utf-8")
    path.chmod(0o600)


def save_yaml(root: Path, relative: str, value: dict[str, Any]) -> None:
    path = root / relative
    backup = root / "local/windows/config-backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") / relative
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_bytes(path.read_bytes())
    path.write_text(yaml.safe_dump(value, sort_keys=False, allow_unicode=True), encoding="utf-8")


def configure_pimc(root: Path, code: Path, data: Path | None = None) -> None:
    code = code.expanduser().resolve()
    if not code.is_dir() or not (code / "train_static.py").is_file():
        raise ValueError("PIMC 源码目录必须存在且包含 train_static.py。")
    if data is not None:
        data = data.expanduser().resolve()
        if not data.is_file():
            raise ValueError("指定的数据文件不存在。")
    link = yaml.safe_load((root / "projects/pimc/repo_link.yaml").read_text(encoding="utf-8"))
    link["repo_path"] = code.as_posix()
    save_yaml(root, "projects/pimc/repo_link.yaml", link)
    execution = yaml.safe_load((root / "configs/execution.yaml").read_text(encoding="utf-8"))
    settings = execution["execution"]["paper_static"]
    settings.update(repo_path=code.as_posix(), python=Path(sys.executable).as_posix())
    if data is not None:
        settings["data_path"] = data.as_posix()
    save_yaml(root, "configs/execution.yaml", execution)


def main(root: Path = ROOT) -> None:
    logger.info("API 配置：1 DeepSeek 官方；2 OpenAI 兼容云端/网关；3 本机兼容服务；0 稍后在前端配置。")
    choice = input("选择 [1]：").strip() or "1"
    if choice == "0":
        logger.info("跳过 API 配置。界面可以启动；真实 Agent 需要随后配置有效模型。")
        return
    if choice not in {"1", "2", "3"}:
        raise ValueError("请选择 0、1、2 或 3。")
    provider, key_env, default_url = {
        "1": ("deepseek", "DEEPSEEK_API_KEY", "https://api.deepseek.com/v1"),
        "2": ("custom", "CUSTOM_ENDPOINT_API_KEY", ""),
        "3": ("local_vllm", "LOCAL_VLLM_API_KEY", "http://127.0.0.1:1234/v1"),
    }[choice]
    endpoint = validate_endpoint(input(f"API Base URL [{default_url}]：").strip() or default_url)
    model_name = input("模型名称（DeepSeek 可留空保留各 Agent 原模型）：").strip()
    if choice != "1" and not model_name:
        raise ValueError("兼容服务需要填写其实际加载的模型名称。")
    from app.settings import env_or_local
    key = getpass.getpass("API Key（隐藏输入；留空保留现有 Key，本机无鉴权服务可留空）：").strip()
    key = key or env_or_local(key_env) or ("EMPTY" if choice == "3" else "")
    if not key:
        raise ValueError("云端 API Key 未配置。")
    agents = yaml.safe_load((root / "configs/agents.yaml").read_text(encoding="utf-8"))
    updated = route_agents(agents, provider=provider, model_name=model_name, endpoint=endpoint, key_env=key_env)
    endpoint_env = {"deepseek": "DEEPSEEK_BASE_URL", "custom": "CUSTOM_ENDPOINT_URL",
                    "local_vllm": "LOCAL_VLLM_BASE_URL"}[provider]
    update_env(root / ".env.local", {key_env: key, "MARS_WINDOWS_API_BASE_URL": endpoint, endpoint_env: endpoint})
    save_yaml(root, "configs/agents.yaml", updated)
    # Enable only the project's real, allowlisted literature acquisition tools.
    if input("允许真实论文检索与下载？[Y/n]：").strip().lower() != "n":
        update_env(root / ".env.local", {
            "MARS_ENABLE_NETWORK_TOOLS": "true",
            "MARS_WEB_SEARCH_ALLOWLIST": "arxiv.org,export.arxiv.org,openaccess.thecvf.com,papers.nips.cc,papers.neurips.cc,proceedings.neurips.cc,api.openalex.org,openreview.net,proceedings.mlr.press",
        })
    code = input("PIMC 源码目录（可留空，之后在项目页接入）：").strip().strip('"')
    if code:
        data = input("PIMC 数据文件绝对路径（可留空，之后在前端选择）：").strip().strip('"')
        configure_pimc(root, Path(code), Path(data) if data else None)
    receipt = root / "local/windows/api-configured.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps({"provider": provider, "endpoint": endpoint,
                                  "configured_at": datetime.now(timezone.utc).isoformat()}, indent=2), encoding="utf-8")
    logger.info("配置已保存。Key 仅保存在本机 .env.local；启动后可在 /config/agents 调整各 Agent。")
    logger.info("本机模式只连接现有模型服务，不负责下载模型权重或启动 LM Studio/Ollama/vLLM。")
    logger.info("配置保存不代表模型调用成功；余额、模型名称和工具调用能力仍需真实运行验证。")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        logger.error("配置未完成：{}", exc)
        raise SystemExit(1) from None
