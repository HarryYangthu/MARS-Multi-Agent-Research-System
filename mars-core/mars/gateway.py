"""Loopback LiteLLM proxy for the coding agent: the agent only receives a short-lived local token.

The upstream API key stays in this process's environment (referenced as os.environ/NAME in the proxy config);
it is never written into the coding agent's configuration.
"""
from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import yaml

from mars.llm import LLM, ROOT, thinking_params


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class LocalGateway:
    def __init__(self, llm: LLM, role: str, root: Path) -> None:
        self.llm, self.role, self.root = llm, role, root
        self.token = "sk-mars-" + secrets.token_urlsafe(24)
        self.port = _free_port()
        self.process: subprocess.Popen[bytes] | None = None

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    @property
    def model(self) -> str:
        return str(self.llm.roles[self.role]["model"])

    def start(self, wait_seconds: float = 90) -> None:
        spec = self.llm.roles[self.role]
        provider = self.llm.providers[spec["provider"]]
        self.llm.deployment(self.role)  # fails early if the credential variable is missing
        self.root.mkdir(parents=True, exist_ok=True)
        upstream, settings = "openai/" + self.model, {"num_retries": self.llm.retries, "drop_params": True}
        if spec.get("stream") is False:  # upstream answers only non-streaming requests; CodingAgent streams (mars/nostream.py)
            upstream = "mars-nostream/" + self.model
            settings["custom_provider_map"] = [{"provider": "mars-nostream", "custom_handler": "mars.nostream.handler"}]
        config = {"model_list": [{"model_name": self.model, "litellm_params": {
                      "model": upstream, "api_base": provider["api_base"],
                      "api_key": "os.environ/" + provider["api_key_env"], "timeout": spec["timeout"],
                      **thinking_params(spec)}}],
                  "litellm_settings": settings}
        path = self.root / "gateway.yaml"
        path.write_text(yaml.safe_dump(config), encoding="utf-8")
        env = {**os.environ, "LITELLM_MASTER_KEY": self.token, "LITELLM_LOCAL_MODEL_COST_MAP": "True",
               "PYTHONPATH": os.pathsep.join(filter(None, [str(ROOT), os.environ.get("PYTHONPATH")]))}
        log = (self.root / "gateway.log").open("ab")
        # python -m instead of the venv's console script: works on Windows (Scripts/litellm.exe) and macOS alike
        self.process = subprocess.Popen([sys.executable, "-m", "litellm.proxy.proxy_cli", "--config", str(path),
                                         "--host", "127.0.0.1", "--port", str(self.port)], env=env, stdout=log, stderr=log)
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"LiteLLM 网关启动失败，见 {self.root / 'gateway.log'}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health/liveliness", timeout=2):
                    return
            except OSError:
                time.sleep(1)
        self.stop()
        raise RuntimeError("LiteLLM 网关在限定时间内未就绪")

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(10)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def __enter__(self) -> "LocalGateway":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()
