"""Verify that the coding-agent sandbox really holds on this machine (no model needed).

A probe process is started through the same wrapper mars-core uses for CodingAgent (macOS: the core's sandbox-exec
profile; Linux/Windows: mars_sandbox.py via MARS_SANDBOX_COMMAND). It tries to write inside and outside the
allowed directories (also from a child process), to reach the gateway port, and to reach a decoy port on
127.0.0.1. The verdict comes from the host side: which files exist afterwards and which listener saw a connection.
Directories contain spaces and Chinese characters on purpose.

    python sandbox/test_sandbox.py            exit 0 = every restriction the wrapper claims is in force
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORE = Path(os.environ.get("MARS_CORE_ROOT", HERE.parents[1] / "mars-core")).resolve()


def listener(record: list[bytes]) -> tuple[socket.socket, int]:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()

    def accept() -> None:
        while True:
            try:
                connection, _ = server.accept()
            except OSError:
                return
            with connection:
                connection.settimeout(5)
                try:
                    data = connection.recv(64)
                    record.append(data or b"<connected>")
                    connection.sendall(b"echo:" + data)
                except OSError:
                    record.append(b"<connected>")

    threading.Thread(target=accept, daemon=True).start()
    return server, server.getsockname()[1]


def probe(args: argparse.Namespace) -> int:
    """Runs inside the sandbox; reports what it observed (the host re-checks independently)."""
    seen: dict[str, str] = {}

    def attempt(name: str, action) -> None:
        try:
            seen[name] = "ok:" + str(action())
        except Exception as error:  # noqa: BLE001 - the error is the observation
            seen[name] = f"blocked:{type(error).__name__}"

    attempt("write_inside", lambda: Path(args.inside, "inside.txt").write_text("x"))
    attempt("write_outside", lambda: Path(args.outside, "outside.txt").write_text("x"))
    child = subprocess.run([sys.executable, "-c", f"open({str(Path(args.outside, 'child.txt'))!r}, 'w').write('x')"],
                           capture_output=True, text=True)
    seen["child_write_outside"] = "ok" if child.returncode == 0 else "blocked:" + (child.stderr.strip().splitlines() or ["?"])[-1][:80]

    def talk(port: int) -> str:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
            connection.sendall(b"ping")
            return connection.recv(64).decode()

    attempt("connect_gateway_port", lambda: talk(args.allowed))
    attempt("connect_other_local_port", lambda: talk(args.forbidden))
    if args.external:
        host, port = args.external.rsplit(":", 1)
        attempt("connect_external", lambda: socket.create_connection((host, int(port)), timeout=5).close())
    # read checks: the denied file must not come back with its marker; an ordinary read and a stdlib import must work
    attempt("read_secret", lambda: Path(args.secret).read_text())
    attempt("read_readable", lambda: Path(args.readable).read_text())
    attempt("read_stdlib", lambda: __import__("decimal").Decimal("1") + 1)
    print(json.dumps(seen))
    return 0


def wrapper_for(write: list[Path], deny: list[Path], port: int) -> tuple[list[str], bool]:
    """(argv prefix, whether mars_sandbox.py is in use). The macOS kernel profile (sandbox-exec) has no read-deny,
    so --deny-read only applies when the wrapper runs; the caller skips the read-deny verdict otherwise."""
    if sys.platform == "darwin" and not os.environ.get("MARS_SANDBOX_COMMAND"):
        sys.path.insert(0, str(CORE))
        from mars.codingagent import sandbox_prefix
        return sandbox_prefix(write, port), False
    base = json.loads(os.environ["MARS_SANDBOX_COMMAND"]) if os.environ.get("MARS_SANDBOX_COMMAND") \
        else [sys.executable, str(HERE / "mars_sandbox.py")]
    return [*base, *(f"--write={p}" for p in write), *(f"--deny-read={p}" for p in deny), f"--port={port}", "--"], True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--inside")
    parser.add_argument("--outside")
    parser.add_argument("--allowed", type=int)
    parser.add_argument("--forbidden", type=int)
    parser.add_argument("--external", default="", help="host:port reachable from this machine, e.g. the model API")
    parser.add_argument("--secret")
    parser.add_argument("--readable")
    parser.add_argument("--out", type=Path, default=HERE.parent / "local/checks")
    args = parser.parse_args()
    if args.probe:
        return probe(args)

    base = args.out / f"sandbox-{time.strftime('%Y%m%d-%H%M%S')} 沙箱"
    work, runtime, outside, creds = base / "work tree 工作区", base / "runtime", base / "outside 外部", base / "凭据 creds"
    for path in (work, runtime, outside, creds):
        path.mkdir(parents=True)
    secret, readable = creds / "models.env", outside / "readable.txt"
    secret.write_text("MARS_INTRANET_API_KEY=TOPSECRET_" + "d2marker")  # must never reach the agent
    readable.write_text("READABLE_OK_marker")                            # an ordinary file the agent may read
    gateway_hits: list[bytes] = []
    decoy_hits: list[bytes] = []
    gateway, gateway_port = listener(gateway_hits)
    decoy, decoy_port = listener(decoy_hits)
    prefix, using_wrapper = wrapper_for([work, runtime], [secret], gateway_port)
    command = [*prefix, sys.executable, str(Path(__file__).resolve()), "--probe",
               f"--inside={work}", f"--outside={outside}", f"--allowed={gateway_port}", f"--forbidden={decoy_port}",
               f"--secret={secret}", f"--readable={readable}",
               *([f"--external={args.external}"] if args.external else [])]
    env = dict(os.environ)
    if os.name == "nt":  # the file-system part can only be tested once the network limitation is acknowledged
        refused = subprocess.run(command, cwd=work, capture_output=True, text=True,
                                 env={k: v for k, v in env.items() if k != "MARS_SANDBOX_WINDOWS_NETWORK"})
        env["MARS_SANDBOX_WINDOWS_NETWORK"] = "unrestricted"
    run = subprocess.run(command, cwd=work, capture_output=True, text=True, env=env, timeout=300)
    gateway.close()
    decoy.close()
    lines = [line for line in run.stdout.splitlines() if line.startswith("{")]
    seen = json.loads(lines[-1]) if lines else {}
    leaked = sorted(p.name for p in outside.iterdir() if p.name != "readable.txt")
    claims = [json.loads(line) for line in run.stderr.splitlines() if line.startswith('{"mars_sandbox"')]
    secret_read = seen.get("read_secret", "")
    read_deny_enforced = bool(seen) and "d2marker" not in secret_read  # shadowed-empty (bwrap) or blocked (landlock/win)
    reads_still_work = "READABLE_OK_marker" in seen.get("read_readable", "") and seen.get("read_stdlib", "").startswith("ok")
    report = {
        "platform": sys.platform, "wrapper": command[:2], "exit_code": run.returncode, "probe": seen,
        "wrapper_claims": claims, "files_written_outside": leaked, "gateway_port_connections": len(gateway_hits),
        "other_port_connections": len(decoy_hits), "read_deny_tested": using_wrapper,
        "filesystem_enforced": bool(seen) and seen.get("write_inside", "").startswith("ok") and not leaked,
        "network_enforced": bool(seen) and seen.get("connect_gateway_port", "").startswith("ok:echo:ping")
                            and not decoy_hits and not seen.get("connect_external", "blocked").startswith("ok"),
        "read_deny_enforced": read_deny_enforced, "reads_still_work": reads_still_work,
        "stderr_tail": run.stderr.strip()[-600:] if not seen else "",
    }
    read_ok = (read_deny_enforced and reads_still_work) if using_wrapper else True  # macOS kernel profile has no read-deny
    if os.name == "nt":
        report["refuses_without_acknowledgement"] = refused.returncode == 97
        ok = report["filesystem_enforced"] and report["refuses_without_acknowledgement"] and read_ok
        report["verdict"] = ("PASS: writes confined and credentials unreadable; network NOT isolated on Windows "
                             "without administrator rights" if ok else "FAIL")
    else:
        ok = report["filesystem_enforced"] and report["network_enforced"] and read_ok
        report["verdict"] = ("PASS: writes confined, credentials unreadable, only the gateway port reachable"
                             if ok else "FAIL")
    (base / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
