"""Self-check after installation (run through run.py so the intranet environment is set): mars-check [options]

Offline, always:
  imports      key packages import in the shared environment; which mars-core patches are present
  locks        the run lock across two real processes: the reader opens run.lock read-only (as api.status does)
  config       local/models.yaml loads; which role credential variables are set (never their values)
  paths        project-path checks of mars.project.Scope on this machine: Chinese and spaces, forward slashes,
               upper/lower case and drive-letter case (Windows), links and junctions pointing out of a project
  core-tests   mars-core tests/ (all), app-tests: mars-app tests/ — both with pytest's temporary directory under a
               path containing Chinese characters and spaces
  codingagent        CodingAgent protocol handshake inside the sandbox (no model request)
  sandbox      sandbox/test_sandbox.py: writes confined, credential files unreadable, network limited (host verdict)
  app          mars-app serves /api/health (core found), /api/runs and the built web page
--live       real model requests: checks/check_models.py, check_gateway.py, check_codingagent.py --live
--remote     checks/check_remote.py (GPU server, read-only)
--sources    checks/check_sources.py (literature sources and IEEE full text)
--evaluate   one real short baseline evaluation through mars.api.evaluate (the default project's first dataset, budget 76,
             GPU server or local CPU); exercises the project binding, its evaluation command, the job lock and result fetching
A JSON report is written to local/checks/selfcheck-<time>.json. Exit code 0 only if every selected step passed.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1]
CODE = DEPLOY.parent
CORE, APP = Path(os.environ.get("MARS_CORE_ROOT", CODE / "mars-core")), CODE / "mars-app"
PY = sys.executable

IMPORTS = """
import importlib, json, os, sys
mods = {m: getattr(importlib.import_module(m), "__version__", "ok") for m in
        ("litellm", "langgraph", "fastapi", "uvicorn", "pydantic", "yaml", "fitz", "trafilatura", "ddgs", "mcp", "typer", "pytest")}
if os.environ.get("MARS_TRAINING_CPU") == "1":
    mods.update({m: getattr(importlib.import_module(m), "__version__", "ok") for m in
                 ("torch", "numpy", "scipy", "matplotlib", "seaborn", "pandas", "tensorboard")})
from mars import codingagent, llm, research
import mars.api
patch = {"windows_and_locks": os.path.exists(os.path.join(os.environ["MARS_CORE_ROOT"], "mars", "osutil.py")),
         "non_stream": os.path.exists(os.path.join(os.environ["MARS_CORE_ROOT"], "mars", "nostream.py")),
         "sandbox_hook": "MARS_SANDBOX_COMMAND" in open(codingagent.__file__, encoding="utf-8").read(),
         "source_registry": hasattr(research, "load_sources")}
print(json.dumps({"python": sys.version.split()[0], "modules": {k: str(v) for k, v in mods.items()}, "core_patch": patch}))
"""
LOCKS = """
import json, os, subprocess, sys, tempfile, time
from mars import osutil
path = os.path.join(tempfile.mkdtemp(), "run.lock")
holder = subprocess.Popen([sys.executable, "-c", "import sys, time; from mars import osutil; h = open(sys.argv[1], 'a+'); "
                           "osutil.lock(h); h.truncate(0); h.write('4242'); h.flush(); print('held', flush=True); time.sleep(4)", path],
                          stdout=subprocess.PIPE, text=True)
assert holder.stdout.readline().strip() == "held"
result = {}
with open(path) as reader:  # read-only, like api._process
    result["reader_sees_pid"] = reader.read().strip() == "4242"
    try:
        osutil.lock(reader, shared=True)
        result["held_lock_detected"] = False
        osutil.unlock(reader)
    except BlockingIOError:
        result["held_lock_detected"] = True
holder.wait()
with open(path) as reader:
    osutil.lock(reader, shared=True)  # must succeed once the holder exited, also on a read-only handle
    osutil.unlock(reader)
    result["free_lock_acquired_read_only"] = True
print(json.dumps(result))
sys.exit(0 if all(result.values()) else 1)
"""
PATHS = """
import json, os, subprocess, sys
from pathlib import Path
from mars.project import Scope, resolve_path
base = Path(sys.argv[1]).resolve()
root = base / "项目 Proj A"
(root / "runs").mkdir(parents=True, exist_ok=True)
reference, text = Scope(root), str(root)
variants = {"as_is": text, "forward_slashes": text.replace(os.sep, "/"), "trailing_separator": text + os.sep,
            "dot_segments": str(root / "runs" / "..")}
if os.name == "nt":
    variants.update(upper_case=text.upper(), lower_case=text.lower(), drive_letter_case=text[0].swapcase() + text[1:])
result = {}
for name, value in variants.items():
    try:
        scope = Scope(Path(value))
        result[name] = scope.root == reference.root and scope.run("demo").parent.resolve() == (root / "runs").resolve()
    except Exception as error:  # noqa: BLE001
        result[name] = f"{type(error).__name__}: {error}"[:200]
result["relative_path"] = resolve_path("runs", root) == (root / "runs").resolve()
outside = base / "outside 外部"
outside.mkdir(exist_ok=True)
links = {"symlink": lambda p: p.symlink_to(outside, target_is_directory=True)}
if os.name == "nt":  # junctions need no privilege on Windows
    links["junction"] = lambda p: subprocess.run(["cmd", "/c", "mklink", "/J", str(p), str(outside)], check=True, capture_output=True)
for kind, make in links.items():
    project = base / f"项目 {kind}"
    project.mkdir(exist_ok=True)
    try:
        make(project / "runs")
    except (OSError, subprocess.CalledProcessError) as error:
        result[f"{kind}_out_of_project_refused"] = f"skipped: {error}"[:120]
        continue
    try:
        Scope(project)
        result[f"{kind}_out_of_project_refused"] = False
    except ValueError:
        result[f"{kind}_out_of_project_refused"] = True
print(json.dumps(result, ensure_ascii=False))
sys.exit(0 if all(v is True or str(v).startswith("skipped") for v in result.values()) else 1)
"""
EVALUATE = """
import json, os
from mars import api
project = os.environ.get("MARS_DEFAULT_PROJECT")
dataset = api.inspect_project(project)["binding"]["datasets"][0]["id"]
result = api.evaluate(api.baseline_dir(project_root=project), dataset, 76, 2026, baseline=True, project_root=project)
print(json.dumps({k: result.get(k) for k in ("ok", "executed_on", "metrics", "elapsed_seconds", "error")}))
raise SystemExit(0 if result.get("ok") else 1)
"""
CONFIG = """
import json, os
from mars.llm import LLM
llm = LLM()
print(json.dumps({role: {"model": spec["model"], "provider": spec["provider"], "stream": spec.get("stream", True),
                         "key_variable": llm.providers[spec["provider"]]["api_key_env"],
                         "key_set": bool(os.environ.get(llm.providers[spec["provider"]]["api_key_env"]))}
                  for role, spec in llm.roles.items()}, ensure_ascii=False))
"""


def step(name: str, argv: list[str], cwd: Path, timeout: int = 900) -> dict:
    print(f"\n=== {name}: {' '.join(Path(a).name if os.sep in a else a for a in argv[:4])}", flush=True)
    started = time.monotonic()
    try:
        done = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
        output, ok = (done.stdout + done.stderr).strip(), done.returncode == 0
    except subprocess.TimeoutExpired:
        output, ok = f"超过 {timeout} 秒未结束", False
    print(output[-3000:])
    result = {"ok": ok, "seconds": round(time.monotonic() - started, 1), "tail": output[-1500:]}
    for line in reversed(output.splitlines()):
        if line.startswith("{") and line.endswith("}"):
            try:
                result["data"] = json.loads(line)
            except ValueError:
                pass
            break
    return result


def serve_app() -> dict:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    started = time.monotonic()
    server = subprocess.Popen([PY, "-m", "uvicorn", "mars_app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                              cwd=APP, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    result: dict = {"port": port}
    try:
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as response:
                    result["health"] = json.load(response)
                break
            except OSError:
                time.sleep(1)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/runs", timeout=30) as response:
            result["runs"] = len(json.load(response))
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as response:
            result["web_page"] = "<div id=\"root\"" in response.read().decode("utf-8", "replace")
    except Exception as error:  # noqa: BLE001 - reported as the failure of this step
        result["error"] = f"{type(error).__name__}: {error}"[:300]
    finally:
        server.terminate()
        try:
            log = server.communicate(timeout=10)[0].decode("utf-8", "replace")
        except subprocess.TimeoutExpired:
            server.kill()
            log = ""
    result["seconds"] = round(time.monotonic() - started, 1)
    result["ok"] = bool((result.get("health") or {}).get("core_found")) and "runs" in result and result.get("web_page", False)
    if not result["ok"]:
        result["server_log"] = log[-1500:]
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--remote", action="store_true")
    parser.add_argument("--sources", action="store_true")
    parser.add_argument("--evaluate", action="store_true")
    args = parser.parse_args()
    checks = DEPLOY / "checks"
    report = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "platform": sys.platform, "steps": {}}
    steps = report["steps"]
    steps["imports"] = step("imports", [PY, "-c", IMPORTS], CORE, 300)
    steps["locks"] = step("locks", [PY, "-c", LOCKS], CORE, 120)
    steps["config"] = step("config", [PY, "-c", CONFIG], CORE, 120)
    scratch = DEPLOY / "local/checks" / f"路径 测试 {time.strftime('%Y%m%d-%H%M%S')}"  # Chinese + spaces on purpose
    scratch.mkdir(parents=True, exist_ok=True)
    steps["paths"] = step("paths", [PY, "-c", PATHS, str(scratch / "paths")], CORE, 120)
    pytest = [PY, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider"]
    steps["core-tests"] = step("core-tests", [*pytest, f"--basetemp={scratch / 'core'}"], CORE)
    steps["app-tests"] = step("app-tests", [*pytest, f"--basetemp={scratch / 'app'}"], APP)
    steps["codingagent"] = step("codingagent", [PY, str(checks / "check_codingagent.py")], DEPLOY, 300)
    steps["sandbox"] = step("sandbox", [PY, str(DEPLOY / "sandbox/test_sandbox.py")], DEPLOY, 600)
    print("\n=== app: mars-app 后端与前端", flush=True)
    steps["app"] = serve_app()
    if args.live:
        steps["models"] = step("models", [PY, str(checks / "check_models.py")], DEPLOY, 3600)
        steps["gateway"] = step("gateway", [PY, str(checks / "check_gateway.py")], DEPLOY, 3600)
        steps["codingagent-live"] = step("codingagent-live", [PY, str(checks / "check_codingagent.py"), "--live"], DEPLOY, 3600)
    if args.remote:
        steps["remote"] = step("remote", [PY, str(checks / "check_remote.py")], DEPLOY, 600)
    if args.sources:
        steps["sources"] = step("sources", [PY, str(checks / "check_sources.py")], DEPLOY, 900)
    if args.evaluate:
        steps["evaluate"] = step("evaluate", [PY, "-c", EVALUATE], CORE, 7200)
    patch = (steps["imports"].get("data") or {}).get("core_patch") or {}
    missing = [k for k, v in patch.items() if not v]
    out = DEPLOY / "local/checks"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"selfcheck-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n================ 自检结果 ================")
    for name, result in steps.items():
        print(f"  {'通过' if result.get('ok') else '失败'}  {name}  ({result.get('seconds', '-')}s)")
    if missing:
        print(f"  注意：内核还没有合入这些补丁：{missing}（见 code/mars-deploy/proposals/mars-core.md）")
    print(f"  报告：{path}")
    return 0 if all(r.get("ok") for r in steps.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
