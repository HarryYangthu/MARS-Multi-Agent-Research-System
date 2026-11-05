"""Role-based LLM access via LiteLLM: retries, fallback, JSON output with schema repair, audit log."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
import litellm  # noqa: E402

litellm.suppress_debug_info = True

ROOT = Path(__file__).resolve().parents[1]
Schema = TypeVar("Schema", bound=BaseModel)


def load_env_files(paths: list[Path]) -> None:
    for path in paths:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def strip_thinking(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    return text.split("</think>")[-1].strip()


def token_usage(usage: Any) -> dict[str, int | None]:
    get = (lambda o, k: o.get(k) if isinstance(o, dict) else getattr(o, k, None))
    details = get(usage, "prompt_tokens_details") if usage else None
    return {"input_tokens": get(usage, "prompt_tokens") if usage else None,
            "output_tokens": get(usage, "completion_tokens") if usage else None,
            "cached_tokens": get(details, "cached_tokens") if details else None}


def thinking_params(spec: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if isinstance(spec.get("thinking"), bool):
        params["extra_body"] = {"thinking": {"type": "enabled" if spec["thinking"] else "disabled"}}
    if spec.get("extra_body"):
        params["extra_body"] = {**params.get("extra_body", {}), **spec["extra_body"]}
    if spec.get("reasoning_effort"):
        params["reasoning_effort"] = spec["reasoning_effort"]
    return params


def extract_json(text: str) -> Any:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object found in model output")
    return json.loads(text[start:end + 1])


class LLM:
    def __init__(self, config_path: Path | None = None, log_path: Path | None = None) -> None:
        config_path = config_path or Path(os.environ.get("MARS_MODELS_CONFIG") or ROOT / "configs/models.yaml")
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        load_env_files([(config_path.parent / p).resolve() for p in cfg.get("env_files", [])])
        self.providers: dict[str, dict[str, Any]] = cfg["providers"]
        self.roles: dict[str, dict[str, Any]] = cfg["roles"]
        self.fallbacks: dict[str, str] = cfg.get("fallbacks", {})
        self.retries = int(cfg.get("retries", 2))
        self._slots = {role: threading.Semaphore(int(spec.get("concurrency", 2))) for role, spec in self.roles.items()}
        self.log_path = log_path
        self._log_lock = threading.Lock()

    def deployment(self, role: str) -> dict[str, Any]:
        spec = self.roles[role]
        provider = self.providers[spec["provider"]]
        key = os.environ.get(provider["api_key_env"], "")
        if not key:
            raise RuntimeError(f"缺少模型凭据环境变量 {provider['api_key_env']}（角色 {role}）")
        return {**spec, "api_base": provider["api_base"], "api_key": key}

    def _log(self, record: dict[str, Any]) -> None:
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_lock, self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def text(self, role: str, messages: list[dict[str, str]], *, json_mode: bool = False,
             max_tokens: int | None = None, tag: str = "") -> str:
        chain = [role] + ([self.fallbacks[role]] if role in self.fallbacks else [])
        original = messages
        errors = []
        for current in chain:
            spec = self.deployment(current)
            started = time.monotonic()
            messages = ([{"role": "user", "content": "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in original)}]
                        if spec.get("single_user") and len(original) > 1 else original)  # gateways taking one user turn
            try:
                with self._slots[current]:
                    content, reasoning, first, usage = self._stream(spec, messages, max_tokens, json_mode)
                content = strip_thinking(content)
                self._log({"ts": time.time(), "tag": tag, "role": current, "model": spec["model"],
                           "seconds": round(time.monotonic() - started, 1), "first_token_s": first,
                           "ok": bool(content), "reasoning_chars": len(reasoning), **usage, "prompt": messages, "output": content})
                if not content:
                    raise RuntimeError("empty model output")
                return content
            except Exception as error:  # every provider failure is recorded, then the fallback is tried
                errors.append(f"{current}: {type(error).__name__}: {error}")
                self._log({"ts": time.time(), "tag": tag, "role": current, "model": spec["model"],
                           "seconds": round(time.monotonic() - started, 1), "ok": False, "error": errors[-1]})
        raise RuntimeError("模型调用失败：" + " | ".join(errors))

    def _stream(self, spec: dict[str, Any], messages: list[dict[str, str]], max_tokens: int | None,
                json_mode: bool) -> tuple[str, str, float | None, dict[str, int | None]]:
        from mars.nostream import complete, read_stream
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            started, first = time.monotonic(), None
            try:
                request = dict(model="openai/" + spec["model"], api_base=spec["api_base"], api_key=spec["api_key"],
                               messages=messages, max_tokens=max_tokens or spec["max_tokens"], **thinking_params(spec),
                               response_format={"type": "json_object"} if json_mode and spec.get("json_mode", True) else None)
                if spec.get("stream") is False:  # raw request: JSON or an SSE body (mars/nostream.py)
                    body = {k: v for k, v in request.items() if v is not None and k not in ("api_base", "api_key")}
                    data = complete(spec["api_base"], spec["api_key"], {**body, "model": spec["model"]}, float(spec["timeout"]))
                else:
                    stream = litellm.completion(**request, stream=True, stream_options={"include_usage": True},
                                               timeout=float(spec.get("idle_timeout", 180)))
                    data, first, _ = read_stream(stream, float(spec["timeout"]), started)
                choice = data["choices"][0]
                if choice.get("finish_reason") in ("length", "content_filter"):
                    raise RuntimeError(f"incomplete model reply: finish_reason={choice['finish_reason']}")
                message = choice["message"]
                return message.get("content") or "", message.get("reasoning_content") or "", first, token_usage(data.get("usage"))
            except Exception as error:  # stalled or failed attempt: back off and retry
                last_error = error
                if attempt < self.retries:
                    time.sleep(min(60, 5 * 2 ** attempt))
        raise RuntimeError(f"{type(last_error).__name__}: {last_error}")

    def structured(self, role: str, messages: list[dict[str, str]], schema: type[Schema], *,
                   tag: str = "", repairs: int = 2) -> Schema:
        instruction = ("只输出一个 JSON 对象，不要输出其他文字。JSON 必须符合下面的 JSON Schema：\n"
                       + json.dumps(schema.model_json_schema(), ensure_ascii=False))
        messages = [*messages, {"role": "user", "content": instruction}]
        content = self.text(role, messages, json_mode=True, tag=tag)
        for attempt in range(repairs + 1):
            try:
                return schema.model_validate(extract_json(content))
            except (ValueError, ValidationError) as error:
                if attempt == repairs:
                    raise RuntimeError(f"{tag}: 模型输出在 {repairs} 次修复后仍不符合格式：{error}") from error
                messages = [*messages, {"role": "assistant", "content": content},
                            {"role": "user", "content": f"上面的输出不符合要求：{str(error)[:1500]}\n"
                             "思考请简洁（输出长度有上限，过长的思考会截断正文），只输出修正后的完整 JSON。"}]
                content = self.text(role, messages, json_mode=True, tag=tag + ":repair")
        raise AssertionError("unreachable")
