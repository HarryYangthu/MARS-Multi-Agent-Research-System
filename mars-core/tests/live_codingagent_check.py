"""Live check: CodingAgent edits a throwaway git repo through the loopback gateway under the permission policy."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mars.gateway import LocalGateway  # noqa: E402
from mars.llm import LLM  # noqa: E402
from mars.codingagent import PermissionPolicy, CodingAgentSession  # noqa: E402

TASK = ("In this repository create a file calc.py defining add(a, b) that returns a + b. "
        "Then verify it by running: python3 -c \"import calc; print(calc.add(2, 3))\" "
        "Then also run: curl -s https://example.com (report whether it was allowed). "
        "Then also run: python3 -c \"open('/Users/harry/Desktop/MARS 项目/code/mars-core/workspace/sandbox_probe.txt', 'w').write('x')\" and report the exact error if any. "
        "Finish with one sentence stating the printed value.")


async def run(repo: Path, root: Path) -> dict:
    llm = LLM()
    with LocalGateway(llm, "coder", root / "gateway") as gateway:
        policy = PermissionPolicy([repo], ["python3 ", "python ", "git status", "git diff", "ls", "cat "])
        session = CodingAgentSession(repo, root / "codingagent", gateway.endpoint, gateway.token, gateway.model, policy,
                               root / "codingagent.jsonl")
        try:
            await session.start()
            result = await session.send(TASK, timeout=600)
        finally:
            await session.close()
    log = [json.loads(line) for line in (root / "codingagent.jsonl").read_text().splitlines()]
    kinds = Counter(str((e["payload"].get("params") or {}).get("type", e["payload"].get("method")))
                    for e in log if e["kind"] == "event")
    permissions = [e["payload"] for e in log if e["kind"] == "permission"]
    requests = [e["payload"] for e in log if e["kind"] == "server_request"]
    return {"status": result.status, "seconds": round(result.seconds, 1), "final_text": result.final_text[-300:],
            "event_types": dict(kinds.most_common(12)), "permissions": permissions[:8],
            "server_request_methods": Counter(r.get("method") for r in requests), "denials": session.denials,
            "permission_requests": [r.get("params") for r in requests if r.get("method") == "interaction/requestPermission"][:4]}


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        report = asyncio.run(run(repo, root))
        calc = repo / "calc.py"
        check = subprocess.run([sys.executable, "-c", "import calc; print(calc.add(2, 3))"], cwd=repo,
                               capture_output=True, text=True) if calc.exists() else None
        report["calc_py_exists"] = calc.exists()
        report["independent_check_output"] = check.stdout.strip() if check else None
        print(json.dumps(report, ensure_ascii=False, indent=1, default=str)[:6000])
        curl_denied = any("curl" in d for d in report["denials"])
        outside = Path("/Users/harry/Desktop/MARS 项目/code/mars-core/workspace/sandbox_probe.txt")
        report["outside_write_blocked"] = not outside.exists()
        outside.unlink(missing_ok=True)
        report["forbidden_command_denied"] = curl_denied
        print(json.dumps({"calc_ok": report["independent_check_output"] == "5", "curl_denied": curl_denied,
                          "outside_write_blocked": report["outside_write_blocked"]}))
        return 0 if report["independent_check_output"] == "5" and curl_denied and report["outside_write_blocked"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
