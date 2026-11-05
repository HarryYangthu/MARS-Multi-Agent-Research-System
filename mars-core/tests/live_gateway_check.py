"""Live check: the loopback gateway forwards a real chat completion using only the local token."""
from __future__ import annotations

import json
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mars.gateway import LocalGateway  # noqa: E402
from mars.llm import LLM  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp, LocalGateway(LLM(), "coder", Path(tmp)) as gateway:
        body = json.dumps({"model": gateway.model, "max_tokens": 2000,
                           "messages": [{"role": "user", "content": "Reply with the single word: pong"}]}).encode()
        request = urllib.request.Request(gateway.endpoint + "/chat/completions", data=body, method="POST",
                                         headers={"Authorization": "Bearer " + gateway.token,
                                                  "Content-Type": "application/json"})
        started = time.monotonic()
        with urllib.request.urlopen(request, timeout=300) as response:
            data = json.load(response)
        text = data["choices"][0]["message"]["content"]
        print(json.dumps({"ok": "pong" in text.lower(), "seconds": round(time.monotonic() - started, 1),
                          "model": data.get("model"), "reply": text[-80:]}, ensure_ascii=False))
        bad = urllib.request.Request(gateway.endpoint + "/chat/completions", data=body, method="POST",
                                     headers={"Authorization": "Bearer wrong", "Content-Type": "application/json"})
        try:
            urllib.request.urlopen(bad, timeout=60)
            print(json.dumps({"wrong_token_rejected": False}))
            return 1
        except urllib.error.HTTPError as error:
            # Without a database LiteLLM answers an invalid key with 400 ("No connected db"); any error = rejected.
            print(json.dumps({"wrong_token_rejected": error.code >= 400, "status": error.code}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
