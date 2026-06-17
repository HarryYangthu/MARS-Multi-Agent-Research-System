"""Optional LangSmith trace mirror.

The file-backed trace manifest is the durable source of truth. This sink only
mirrors spans to LangSmith when explicitly enabled and configured.
"""
from __future__ import annotations

import uuid
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import yaml
from loguru import logger

from app.settings import get_settings, repo_root


class TraceSpanLike(Protocol):
    span_id: str
    parent_span_id: str | None
    name: str
    kind: str
    started_at: str
    ended_at: str | None
    status: str
    attributes: dict[str, Any]


_DENY_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
}


@dataclass(frozen=True)
class LangSmithSinkConfig:
    enabled: bool
    api_key: str
    endpoint: str
    project: str
    timeout_ms: int


class LangSmithSink:
    def __init__(self, config: LangSmithSinkConfig) -> None:
        self.config = config
        self._client: Any | None = None
        self._warned_unavailable = False

    @property
    def enabled(self) -> bool:
        return self.config.enabled and bool(self.config.api_key)

    def new_run_id(self) -> str:
        return str(uuid.uuid4())

    def span_started(
        self,
        *,
        run_id: str,
        trace_id: str,
        span: TraceSpanLike,
        parent_remote_run_id: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        client = self._client_or_none()
        if client is None:
            return
        remote_run_id = span.attributes.get("langsmith_run_id")
        if not isinstance(remote_run_id, str) or not remote_run_id:
            return
        try:
            kwargs: dict[str, Any] = {
                "id": uuid.UUID(remote_run_id),
                "name": span.name,
                "inputs": {
                    "run_id": run_id,
                    "attributes": _redact(span.attributes),
                },
                "run_type": _run_type(span.kind),
                "project_name": self.config.project,
                "start_time": _parse_datetime(span.started_at),
                "extra": {
                    "metadata": {
                        "mars_run_id": run_id,
                        "mars_trace_id": trace_id,
                        "mars_span_id": span.span_id,
                        "mars_parent_span_id": span.parent_span_id,
                        "mars_kind": span.kind,
                    }
                },
                "tags": ["mars", f"mars-run:{run_id}", f"kind:{span.kind}"],
            }
            if parent_remote_run_id:
                kwargs["parent_run_id"] = uuid.UUID(parent_remote_run_id)
            client.create_run(**kwargs)
        except Exception as exc:  # pragma: no cover - defensive external sink
            logger.warning("LangSmith span start mirror failed: {}", exc)

    def span_finished(
        self,
        *,
        run_id: str,
        trace_id: str,
        span: dict[str, Any],
    ) -> None:
        if not self.enabled:
            return
        client = self._client_or_none()
        if client is None:
            return
        attrs = span.get("attributes", {})
        if not isinstance(attrs, dict):
            attrs = {}
        remote_run_id = attrs.get("langsmith_run_id")
        if not isinstance(remote_run_id, str) or not remote_run_id:
            return
        status = str(span.get("status", "ok"))
        error = None
        if status == "error":
            raw_error = attrs.get("error", "span failed")
            error = str(raw_error)
        try:
            client.update_run(
                uuid.UUID(remote_run_id),
                end_time=_parse_datetime(str(span.get("ended_at"))),
                error=error,
                outputs={
                    "status": status,
                    "run_id": run_id,
                    "trace_id": trace_id,
                    "attributes": _redact(attrs),
                },
            )
        except Exception as exc:  # pragma: no cover - defensive external sink
            logger.warning("LangSmith span finish mirror failed: {}", exc)

    def _client_or_none(self) -> Any | None:
        if self._client is not None:
            return self._client
        try:
            from langsmith import Client
        except ImportError:
            if not self._warned_unavailable:
                logger.warning("LangSmith enabled but the langsmith package is unavailable")
                self._warned_unavailable = True
            return None
        try:
            self._client = Client(
                api_url=self.config.endpoint,
                api_key=self.config.api_key,
                timeout_ms=self.config.timeout_ms,
                hide_inputs=True,
                hide_outputs=True,
                hide_metadata=False,
            )
            return self._client
        except Exception as exc:  # pragma: no cover - defensive external sink
            logger.warning("LangSmith client initialization failed: {}", exc)
            return None


_sink: LangSmithSink | None = None


def get_langsmith_sink() -> LangSmithSink:
    global _sink
    if _sink is None:
        settings = get_settings()
        yaml_config = _read_langsmith_yaml_config()
        _sink = LangSmithSink(
            LangSmithSinkConfig(
                enabled=settings.mars_langsmith_enabled or _yaml_bool(yaml_config.get("enabled")),
                api_key=settings.langsmith_api_key,
                endpoint=_env_or_yaml_str(
                    "LANGSMITH_ENDPOINT",
                    yaml_config.get("endpoint"),
                    settings.langsmith_endpoint,
                ),
                project=_env_or_yaml_str(
                    "LANGSMITH_PROJECT",
                    yaml_config.get("project"),
                    settings.langsmith_project,
                ),
                timeout_ms=_env_or_yaml_int(
                    "MARS_LANGSMITH_TIMEOUT_MS",
                    yaml_config.get("timeout_ms"),
                    settings.mars_langsmith_timeout_ms,
                ),
            )
        )
    return _sink


def reset_langsmith_sink_for_tests() -> None:
    global _sink
    _sink = None


def _run_type(kind: str) -> str:
    normalized = kind.lower()
    if "llm" in normalized:
        return "llm"
    if "tool" in normalized or "gate" in normalized:
        return "tool"
    return "chain"


def _read_langsmith_yaml_config() -> dict[str, Any]:
    path = repo_root() / "configs" / "observability.yaml"
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("failed to read observability config: {}", exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    sinks = raw.get("sinks", {})
    if not isinstance(sinks, dict):
        return {}
    langsmith = sinks.get("langsmith", {})
    return langsmith if isinstance(langsmith, dict) else {}


def _yaml_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return False


def _yaml_str(value: Any, default: str) -> str:
    return value if isinstance(value, str) and value else default


def _env_or_yaml_str(env_name: str, value: Any, default: str) -> str:
    env_value = os.environ.get(env_name)
    if env_value:
        return env_value
    return _yaml_str(value, default)


def _yaml_int(value: Any, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _env_or_yaml_int(env_name: str, value: Any, default: int) -> int:
    env_value = os.environ.get(env_name)
    if env_value:
        try:
            return int(env_value)
        except ValueError:
            return default
    return _yaml_int(value, default)


def _parse_datetime(value: str) -> datetime | None:
    if not value or value == "None":
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in _DENY_KEYS:
                out[key_text] = "[redacted]"
            else:
                out[key_text] = _redact(item)
        return out
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    return value
