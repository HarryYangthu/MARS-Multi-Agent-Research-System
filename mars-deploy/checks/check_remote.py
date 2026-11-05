"""Check the GPU server described by `remote` in mars-core/configs/machine.yaml, read-only.

Runs exactly the transports the kernel uses: ssh with the dedicated key in BatchMode, git present on the server for the
per-project bare repositories the kernel creates, and an ssh + tar stream unpacked with tarfile (the way results come
back). On the server it checks python3/torch/CUDA, the working root and, for the default project (MARS_DEFAULT_PROJECT),
its remote data files. Nothing is written remotely.

    python checks/check_remote.py
"""
from __future__ import annotations

import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

CORE = Path(os.environ.get("MARS_CORE_ROOT", Path(__file__).resolve().parents[2] / "mars-core")).resolve()
sys.path.insert(0, str(CORE))
from mars.project import ROOT, machine_config  # noqa: E402

HINTS = {"Host key verification failed": "第一次连接这台服务器：先在终端手动执行一次 ssh（不带 BatchMode），确认主机指纹",
         "Permission denied": "密钥被拒绝：检查 user、key 路径，以及服务器 ~/.ssh/authorized_keys",
         "UNPROTECTED PRIVATE KEY": "私钥权限过宽：Windows 上执行 icacls <key> /inheritance:r /grant:r \"%USERNAME%:R\"，Linux 上 chmod 600",
         "Could not resolve hostname": "主机名解析失败：检查 host，或改用 IP",
         "Connection timed out": "连不上端口：检查 host、port 和公司网络策略"}


def hint(text: str) -> str:
    return next((v for k, v in HINTS.items() if k in text), "")


def remote_data() -> list[str]:
    """Remote data paths of the default project (its remote.data_path), when one is set and fully declared."""
    project = os.environ.get("MARS_DEFAULT_PROJECT")
    if not project:
        return []
    try:
        from mars.project import inspect
        _, ctx = inspect(project)
    except Exception:  # noqa: BLE001 - the project check reports its own problems
        return []
    return list(((ctx.binding["config"].get("remote") or {}).get("data") or {}).values()) if ctx else []


def main() -> int:
    remote = machine_config().get("remote") or {}
    report: dict = {"ssh_binary": shutil.which("ssh"), "git_binary": shutil.which("git")}
    missing = [k for k in ("name", "host", "port", "user", "key", "root") if not remote.get(k)]
    if missing:
        print(json.dumps({**report, "error": f"configs/machine.yaml 的 remote 缺少 {missing}"}, ensure_ascii=False))
        return 1
    key_path = Path(remote["key"]).expanduser()
    key = (key_path if key_path.is_absolute() else ROOT / key_path).resolve()
    ssh = ["ssh", "-i", str(key), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=30",
           "-p", str(remote["port"]), f"{remote['user']}@{remote['host']}"]
    report["key"] = {"path": str(key), "exists": key.is_file()}
    root = remote["root"]

    def run(argv: list[str], **kwargs) -> dict:
        started = time.monotonic()
        try:
            done = subprocess.run(argv, capture_output=True, text=True, timeout=120, **kwargs)
        except (OSError, subprocess.TimeoutExpired) as error:
            return {"ok": False, "error": f"{type(error).__name__}: {error}"[:300]}
        out = {"ok": done.returncode == 0, "seconds": round(time.monotonic() - started, 1),
               "stdout": done.stdout.strip()[-600:], "stderr": done.stderr.strip()[-400:]}
        if not out["ok"]:
            out["hint"] = hint(done.stderr)
        return out

    report["ssh_login"] = run([*ssh, "true"])
    if report["ssh_login"]["ok"]:
        data = " ".join(shlex.quote(p) for p in remote_data())
        script = (f"mkdir -p {shlex.quote(root)} 2>/dev/null; test -w {shlex.quote(root)} && echo root-ok; git --version && echo git-ok; "
                  "python3 -c 'import torch, sys; print(\"python\", sys.version.split()[0], \"torch\", torch.__version__, "
                  "\"cuda\", torch.cuda.is_available(), torch.cuda.device_count())'; "
                  f"for f in {data}; do test -r \"$f\" && echo data-ok \"$f\" || echo data-missing \"$f\"; done")
        report["server"] = run([*ssh, "bash", "-lc", shlex.quote(script)])  # single quotes: no expansion by the login shell
        text = report["server"].get("stdout", "")
        report["server"].update(root_ok="root-ok" in text, git_ok="git-ok" in text, cuda="cuda True" in text,
                                data_missing=[l.split(" ", 1)[1] for l in text.splitlines() if l.startswith("data-missing")])
        stream = subprocess.run([*ssh, "tar", "-C", root, "--no-recursion", "-cf", "-", "."], capture_output=True, timeout=120)
        try:
            with tarfile.open(fileobj=io.BytesIO(stream.stdout), mode="r|") as archive:
                names = [member.name for member in archive]
            report["tar_stream"] = {"ok": "." in names or "./" in names, "members": names}
        except tarfile.TarError as error:
            report["tar_stream"] = {"ok": False, "error": f"{error} {stream.stderr.decode(errors='replace')[-300:]}"}
    server = report.get("server", {})
    report["ok"] = all(report.get(k, {}).get("ok") for k in ("ssh_login", "server", "tar_stream")) \
        and server.get("root_ok") and server.get("git_ok") and server.get("cuda") and not server.get("data_missing")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
