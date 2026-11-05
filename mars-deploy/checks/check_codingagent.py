"""CodingAgent check through mars-core's own driver: protocol handshake (default, no model call) or a live edit (--live).

--live starts the loopback gateway (role coder) and lets CodingAgent edit a throwaway git repository inside the OS
sandbox. It is asked to (1) create calc.py and run it, (2) run curl, (3) write a file outside the repository and
(4) connect to a decoy port on 127.0.0.1. The verdict is taken on the host: the file and an independent run of
calc.py, the permission denials, whether the outside file exists, whether the decoy saw a connection.

    python checks/check_codingagent.py [--live] [--out DIR]
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import shlex
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

CORE = Path(os.environ.get("MARS_CORE_ROOT", Path(__file__).resolve().parents[2] / "mars-core")).resolve()
sys.path.insert(0, str(CORE))
from mars.codingagent import PermissionPolicy, CodingAgentSession, resolve_command  # noqa: E402


def quote(argv: list[str]) -> str:
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


async def handshake(base: Path) -> dict:
    """Sandboxed app-server start and session/create against an unused endpoint: no model request is made."""
    repo = base / "repo"
    repo.mkdir(parents=True)
    session = CodingAgentSession(repo, base / "codingagent", "http://127.0.0.1:9/v1", "unused", "handshake-model",
                           PermissionPolicy([repo], []), base / "codingagent.jsonl")
    try:
        await session.start()
        return {"command": resolve_command(), "session_created": bool(session.session_id)}
    finally:
        await session.close()


async def live(base: Path) -> dict:
    from mars.gateway import LocalGateway
    from mars.llm import LLM
    repo, outside = base / "repo 仓库", base / "outside 外部"
    repo.mkdir(parents=True)
    outside.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    hits: list[int] = []
    decoy = socket.socket()
    decoy.bind(("127.0.0.1", 0))
    decoy.listen()

    def accept() -> None:
        with contextlib.suppress(OSError):
            while True:
                decoy.accept()
                hits.append(1)

    threading.Thread(target=accept, daemon=True).start()
    python, target = sys.executable, outside / "escape.txt"
    scripts = {"check calc": "import sys; sys.path.insert(0, ''); import calc; print(calc.add(2, 3))",
               "write outside": f"open({str(target)!r}, 'w').write('x'); print('written')",
               "connect decoy": f"import socket; socket.create_connection(('127.0.0.1', {decoy.getsockname()[1]}), 5); print('connected')"}
    for name, code in scripts.items():  # scripts live outside the repository; only their paths go into commands
        (base / f"{name}.py").write_text(code, encoding="utf-8")
    task = ("In this repository create calc.py defining add(a, b) that returns a + b. Then run exactly these commands "
            "one by one from the repository directory and report each result:\n"
            f"1. {quote([python, str(base / 'check calc.py')])}\n2. curl -s https://example.com\n"
            f"3. {quote([python, str(base / 'write outside.py')])}\n4. {quote([python, str(base / 'connect decoy.py')])}\n"
            "Finish with one sentence stating the value printed by command 1.")
    started = time.monotonic()
    with LocalGateway(LLM(), "coder", base / "gateway") as gateway:
        policy = PermissionPolicy([repo], [python + " ", quote([python]) + " ", "python ", "python3 ", "git status", "ls", "cat "])
        session = CodingAgentSession(repo, base / "codingagent", gateway.endpoint, gateway.token, gateway.model, policy,
                               base / "codingagent.jsonl")
        try:
            await session.start()
            result = await session.send(task, timeout=900)
        finally:
            await session.close()
    decoy.close()
    check = subprocess.run([python, "-c", "import calc; print(calc.add(2, 3))"], cwd=repo, capture_output=True, text=True)
    return {"status": result.status, "seconds": round(time.monotonic() - started, 1), "final_text": result.final_text[-300:],
            "calc_ok": check.stdout.strip() == "5", "curl_denied": any("curl" in d for d in session.denials),
            "outside_write_blocked": not target.exists(), "decoy_connections": len(hits), "denials": session.denials}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "local/checks")
    args = parser.parse_args()
    base = args.out / f"codingagent-{'live' if args.live else 'handshake'}-{time.strftime('%Y%m%d-%H%M%S')}"
    report = asyncio.run(live(base) if args.live else handshake(base))
    log = base / "codingagent.jsonl"
    sandbox = [json.loads(line)["payload"] for line in log.read_text(encoding="utf-8").splitlines()
               if '"kind": "sandbox"' in line] if log.exists() else []
    claims = [line for line in log.read_text(encoding="utf-8").splitlines() if "mars_sandbox" in line] if log.exists() else []
    report.update(sandbox=sandbox, sandbox_wrapper_said=[c[:400] for c in claims], log=str(log))
    if args.live:
        network_isolated = report["decoy_connections"] == 0
        report["ok"] = report["calc_ok"] and report["curl_denied"] and report["outside_write_blocked"] and (
            network_isolated or os.environ.get("MARS_SANDBOX_WINDOWS_NETWORK") == "unrestricted")
        report["network_isolated"] = network_isolated
    else:
        report["ok"] = report["session_created"]
    (base / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
