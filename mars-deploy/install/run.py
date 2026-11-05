"""Start MARS with the installed intranet environment (used by the mars / mars-app / mars-check / mars-python
entry points at the bundle root):

    run.py mars <args>      mars-core CLI (bin/mars equivalent: run / resume / refine / status / evaluate / kb ...)
    run.py app              mars-app on http://127.0.0.1:<MARS_APP_PORT, default 8020>, serving web/dist
    run.py check [--live] [--remote] [--sources]      self-check (install/selfcheck.py)
    run.py python <script> <args>                     any script, e.g. code/mars-deploy/checks/check_models.py

The environment it sets: PYTHONUTF8=1 (the code reads UTF-8 without naming it; Chinese Windows defaults to GBK),
PYTHONPATH, MARS_CORE_ROOT, MARS_MODELS_CONFIG / MARS_SOURCES_CONFIG (local/), MARS_APP_DATA, MARS_DEPLOY_ROOT
(mars-app's model page runs checks/check_models.py from there), MARS_CODINGAGENT_COMMAND
(bundled node + codingagent.cjs), MARS_SANDBOX_COMMAND (sandbox/mars_sandbox.py), core.autocrlf=false for every git call
(MinGit enables CRLF conversion, which would make checkouts differ from the GPU server), plus local/mars.env.
On Windows the machine is kept from idle sleep while the command runs (no administrator rights needed).
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1]
CODE = DEPLOY.parent
LOCAL = DEPLOY / "local"


def read_env(path: Path) -> dict[str, str]:
    values = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def environment() -> tuple[dict[str, str], dict]:
    paths_file = LOCAL / "paths.json"
    if not paths_file.is_file():
        raise SystemExit("还没有安装：先运行离线包根目录的 install.cmd（Windows）或 ./install.sh（Linux）")
    paths = json.loads(paths_file.read_text(encoding="utf-8"))
    env = dict(os.environ)
    for key, value in read_env(LOCAL / "mars.env").items():
        env.setdefault(key, value)
    core = CODE / "mars-core"
    env.update(PYTHONUTF8="1", PYTHONPATH=os.pathsep.join([str(core), str(CODE / "mars-app/server")]),
               MARS_CORE_ROOT=str(core), MARS_APP_DATA=str(LOCAL / "app-data"), MARS_DEPLOY_ROOT=str(DEPLOY),
               MARS_MODELS_CONFIG=str(LOCAL / "models.yaml"), MARS_SOURCES_CONFIG=str(LOCAL / "sources.yaml"),
               MARS_CODINGAGENT_COMMAND=json.dumps([paths["node"], paths["codingagent_cjs"]]),
               GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.autocrlf", GIT_CONFIG_VALUE_0="false")
    # Credential paths the coding-agent sandbox must not let the agent read (the kernel adds remote.key and the
    # model env files it knows). A listed path that does not exist is skipped by the wrapper.
    deny = [LOCAL / "models.env", *sorted(LOCAL.glob("models.env.bak-*"))]
    deny += [Path(home) / ".ssh" for home in dict.fromkeys(filter(None, [env.get("HOME"), env.get("USERPROFILE")]))]
    sandbox_cmd = [paths["venv_python"], str(DEPLOY / "sandbox/mars_sandbox.py"), *(f"--deny-read={p}" for p in deny)]
    env.setdefault("MARS_SANDBOX_COMMAND", json.dumps(sandbox_cmd))
    # The bundled demo project (packed at <root>/demo/<name>/): mars-app registers it read-only on first launch.
    demo_root = CODE.parent / "demo"
    demo = next((p for p in sorted(demo_root.iterdir()) if (p / ".mars/project.yaml").is_file()), None) if demo_root.is_dir() else None
    if demo is not None:
        env.setdefault("MARS_DEMO_PROJECT", str(demo))
    studies = CODE.parent / "projects"
    study = next((p for p in sorted(studies.iterdir()) if (p / ".mars/project.yaml").is_file()), None) if studies.is_dir() else None
    if study is not None:
        env.setdefault("MARS_DEFAULT_PROJECT", str(study))
    if paths.get("training_cpu"):
        env["MARS_TRAINING_CPU"] = "1"
    extra = [str(Path(paths["node"]).parent)] + ([paths["git_cmd_dir"]] if paths.get("git_cmd_dir") else [])
    env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
    if any(env.get(k) for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")):
        hosts = [h for h in env.get("NO_PROXY", env.get("no_proxy", "")).split(",") if h.strip()]
        env["NO_PROXY"] = env["no_proxy"] = ",".join(dict.fromkeys(hosts + ["127.0.0.1", "localhost"]))
    return env, paths


def keep_awake() -> None:
    if os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED


def _open_when_ready(port: str) -> None:
    """Poll the health endpoint in a background thread, then open the default browser (one-click launch)."""
    import threading
    import urllib.request
    import webbrowser

    def wait() -> None:
        for _ in range(120):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2).close()
                webbrowser.open(f"http://127.0.0.1:{port}/")
                return
            except OSError:
                time.sleep(1)

    threading.Thread(target=wait, daemon=True).start()


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("mars", "app", "check", "python"):
        print(__doc__)
        return 2
    env, paths = environment()
    python, target, args = paths["venv_python"], argv[0], argv[1:]
    if target == "mars":
        command, cwd = [python, "-m", "mars.cli", *args], Path.cwd()  # like bin/mars: relative paths are the caller's
    elif target == "app":
        port = env.get("MARS_APP_PORT", "8020")
        print(f"mars-app：http://127.0.0.1:{port}/  （Ctrl+C 停止；只监听本机）", flush=True)
        if "--open" in args:  # one-click launch: open the browser once the server answers
            args = [a for a in args if a != "--open"]
            _open_when_ready(port)
        command, cwd = [python, "-m", "uvicorn", "mars_app.main:app", "--host", "127.0.0.1", "--port", port, *args], CODE / "mars-app"
    elif target == "check":
        command, cwd = [python, str(DEPLOY / "install/selfcheck.py"), *args], DEPLOY
    else:
        command, cwd = [python, *args], Path.cwd()
    keep_awake()
    child = subprocess.Popen(command, cwd=cwd, env=env)
    signal.signal(signal.SIGINT, lambda *_: None)  # Ctrl+C reaches the child directly; wait for it to finish
    return child.wait()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
