"""Live check of the loopback gateway the coding agent uses: plain, streamed and tool-calling requests, wrong token.

CodingAgent always streams and calls tools natively, so a gateway that only passes the plain request is not enough.
With `stream: false` on the role, the streamed requests exercise the core's non-stream replay (mars/nostream.py).
Every request is real; the gateway log is kept under the report directory.

    python checks/check_gateway.py [--role coder] [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CORE = Path(os.environ.get("MARS_CORE_ROOT", Path(__file__).resolve().parents[2] / "mars-core")).resolve()
sys.path.insert(0, str(CORE))
from mars.gateway import LocalGateway  # noqa: E402
from mars.llm import LLM  # noqa: E402

TOOL = {"type": "function", "function": {"name": "add", "description": "Add two integers.",
        "parameters": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                       "required": ["a", "b"]}}}


def post(gateway: LocalGateway, body: dict, token: str, timeout: float = 600, attempts: int = 4) -> tuple[int, bytes]:
    """POST /chat/completions; HTTP 429 (upstream rate limit) is retried with backoff and reported if it persists."""
    for attempt in range(attempts):
        request = urllib.request.Request(gateway.endpoint + "/chat/completions", data=json.dumps(body).encode(),
                                         method="POST", headers={"Authorization": "Bearer " + token,
                                                                 "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            if error.code != 429 or attempt == attempts - 1:
                return error.code, error.read()
            time.sleep(20 * (attempt + 1))
    raise AssertionError("unreachable")


def sse(raw: bytes) -> list[dict]:
    chunks = []
    for line in raw.decode("utf-8", "replace").splitlines():
        if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]"):
            chunks.append(json.loads(line[5:]))
    return chunks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", default="coder")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "local/checks")
    args = parser.parse_args()
    root = args.out / f"gateway-{time.strftime('%Y%m%d-%H%M%S')}"
    llm = LLM()
    spec = llm.roles[args.role]
    report: dict = {"role": args.role, "model": spec["model"], "stream_upstream": spec.get("stream", True),
                    "gateway_log": str(root / "gateway.log")}
    with LocalGateway(llm, args.role, root) as gateway:
        # Connectivity probes use a small budget (128 tokens), matching MARS-Product's connection_test_max_tokens;
        # the tool-call capability check below gets its own larger budget. A mandatory-thinking model that returns
        # empty at 128 tokens is itself a signal to raise the budget or turn thinking off for the check.
        ask = {"model": gateway.model, "max_tokens": 128,
               "messages": [{"role": "user", "content": "Reply with the single word: pong"}]}
        started = time.monotonic()
        status, raw = post(gateway, ask, gateway.token)
        text = json.loads(raw)["choices"][0]["message"]["content"] if status == 200 else raw.decode()[:300]
        report["plain"] = {"status": status, "ok": status == 200 and "pong" in text.lower(),
                           "seconds": round(time.monotonic() - started, 1), "reply": text[-80:]}

        started = time.monotonic()
        status, raw = post(gateway, {**ask, "stream": True, "stream_options": {"include_usage": True}}, gateway.token)
        chunks = sse(raw) if status == 200 else []
        text = "".join((c["choices"][0]["delta"].get("content") or "") for c in chunks if c.get("choices"))
        report["stream"] = {"status": status, "ok": status == 200 and "pong" in text.lower(), "chunks": len(chunks),
                            "seconds": round(time.monotonic() - started, 1), "reply": text[-80:] or raw.decode()[:300]}

        tools = {**ask, "max_tokens": 512, "stream": True, "tools": [TOOL], "tool_choice": "auto",
                 "messages": [{"role": "user", "content": "Use the add tool to compute 2 + 3. Call the tool; do not answer in text."}]}
        started = time.monotonic()
        status, raw = post(gateway, tools, gateway.token)
        calls: dict[int, dict] = {}
        for chunk in sse(raw) if status == 200 else []:
            for call in (chunk.get("choices") or [{}])[0].get("delta", {}).get("tool_calls") or []:
                entry = calls.setdefault(call.get("index", 0), {"name": "", "arguments": ""})
                entry["name"] += (call.get("function") or {}).get("name") or ""
                entry["arguments"] += (call.get("function") or {}).get("arguments") or ""
        parsed = [{**c, "arguments": json.loads(c["arguments"] or "{}")} for c in calls.values()] if calls else []
        report["stream_tools"] = {"status": status, "seconds": round(time.monotonic() - started, 1), "calls": parsed,
                                  "ok": any(c["name"] == "add" and c["arguments"] == {"a": 2, "b": 3} for c in parsed),
                                  **({} if status == 200 else {"error": raw.decode()[:300]})}

        status, _ = post(gateway, ask, "wrong-token", attempts=1)
        report["wrong_token_rejected"] = status >= 400  # without a database LiteLLM answers 400; any error = rejected
    report["ok"] = all(report[k]["ok"] for k in ("plain", "stream", "stream_tools")) and report["wrong_token_rejected"]
    root.mkdir(parents=True, exist_ok=True)
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
