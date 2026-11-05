"""Offline installer for the MARS intranet bundle. Standard library only; started by install.cmd / install.sh with
the bundled Python, never touches the network.

1. checks every file of the bundle against MANIFEST.json (first install: everything; later: the dependencies);
2. unpacks Node, the CodingAgent command runtime and (Windows) MinGit into runtime/;
3. creates the shared environment code/mars-core/.venv (mars-core and mars-app use the same one) from the bundled
   wheels with hash checking: pip by default, uv when it is on PATH (pass --pip to force pip);
4. writes local configs from the templates, never overwriting: local/models.yaml, models.env, sources.yaml,
   mars.env and code/mars-core/configs/machine.yaml (this machine's python and remote GPU);
5. runs the offline self-check (unit tests, CodingAgent handshake, sandbox, mars-app).

    install.cmd | ./install.sh  [--pip] [--reinstall] [--verify-all] [--no-check]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]          # <bundle>/code/mars-deploy/install/install.py
CODE, BUNDLE, RUNTIME = ROOT / "code", ROOT / "bundle", ROOT / "runtime"
DEPLOY = CODE / "mars-deploy"
LOCAL = DEPLOY / "local"
WINDOWS = os.name == "nt"


def say(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def run(argv: list, **kwargs) -> None:
    say(" ".join(str(a) for a in argv[:6]) + (" ..." if len(argv) > 6 else ""))
    subprocess.run([str(a) for a in argv], check=True, **kwargs)


def verify(everything: bool) -> dict:
    manifest = json.loads((ROOT / "MANIFEST.json").read_text(encoding="utf-8"))
    expected = "windows-x64" if WINDOWS else "linux-x64"
    if manifest["platform"] != expected:
        raise SystemExit(f"这是 {manifest['platform']} 的离线包，当前系统需要 {expected}")
    files = [f for f in manifest["files"] if everything or f["path"].startswith("bundle/")]
    say(f"校验 {len(files)} 个文件（MANIFEST.json）")
    bad = []
    for entry in files:
        path = ROOT / entry["path"]
        if not path.is_file():
            bad.append(entry["path"] + "（缺失）")
            continue
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != entry["sha256"]:
                bad.append(entry["path"])
    if bad:
        raise SystemExit("离线包不完整或被改动，请重新解压完整的离线包：\n  " + "\n  ".join(bad[:20]))
    return manifest


def unpack(archive: Path, target: Path) -> Path:
    marker = target / ".unpacked"
    if marker.is_file() and marker.read_text() == str(archive.stat().st_size):
        return target
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    say(f"解压 {archive.name} → {target.relative_to(ROOT)}")
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                destination = (target / info.filename).resolve()
                if not destination.is_relative_to(target.resolve()):
                    raise SystemExit(f"{archive.name} 含越界路径：{info.filename}")
                zf.extract(info, target)
                mode = (info.external_attr >> 16) & 0o777
                if mode and not WINDOWS:
                    os.chmod(destination, mode)  # zipfile drops the executable bit (ripgrep, bfs, ugrep)
    else:
        with tarfile.open(archive) as tf:
            tf.extractall(target, filter="data")
    marker.write_text(str(archive.stat().st_size))
    return target


def place(template: Path, target: Path) -> None:
    if target.exists():
        say(f"保留已有配置 {target.relative_to(ROOT)}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(template, target)
    say(f"已生成 {target.relative_to(ROOT)}（请按注释修改）")


def private(path: Path) -> None:
    """Only the current user may read the credential file."""
    if WINDOWS:
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{os.environ.get('USERNAME', '')}:F"],
                       capture_output=True)
    else:
        path.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pip", action="store_true", help="always use pip, even when uv is available")
    parser.add_argument("--reinstall", action="store_true", help="recreate code/mars-core/.venv")
    parser.add_argument("--verify-all", action="store_true", help="also re-check the source files")
    parser.add_argument("--no-check", action="store_true", help="skip the self-check at the end")
    args = parser.parse_args()
    LOCAL.mkdir(parents=True, exist_ok=True)
    first = not (LOCAL / "paths.json").exists()
    manifest = verify(first or args.verify_all)

    node_dir = unpack(next(BUNDLE.glob("node.*")), RUNTIME / "node")
    node = next(node_dir.rglob("node.exe" if WINDOWS else "bin/node"))
    codingagent = unpack(BUNDLE / "codingagent.zip", RUNTIME / "codingagent") / "glm/zcode.cjs"
    git_cmd = unpack(BUNDLE / "mingit.zip", RUNTIME / "git") / "cmd" if WINDOWS else None
    if not WINDOWS and not shutil.which("git"):
        raise SystemExit("Linux 上需要系统自带的 git（内核用 git 管理候选分支）；请让管理员安装 git")

    base = RUNTIME / "python" / ("python.exe" if WINDOWS else "bin/python3")
    venv = CODE / "mars-core/.venv"
    python = venv / ("Scripts/python.exe" if WINDOWS else "bin/python")
    if args.reinstall or not python.exists():
        run([base, "-m", "venv", "--clear", venv])
    requirements, wheels = BUNDLE / "requirements.txt", BUNDLE / "wheels"
    uv = None if args.pip else shutil.which("uv")
    if uv:
        run([uv, "pip", "install", "--offline", "--no-index", "--find-links", wheels, "--require-hashes",
             "--python", python, "-r", requirements])
    else:
        run([python, "-m", "pip", "install", "--no-index", "--find-links", wheels, "--require-hashes", "--no-cache-dir",
             "--disable-pip-version-check", "--quiet", "-r", requirements])
    run([python, "-m", "pip", "check", "--disable-pip-version-check"])
    if WINDOWS and manifest.get("training") == "CPU":
        # Reuse the app-local C++ runtime already authenticated inside the pinned NumPy wheel.
        runtime = next((venv / "Lib/site-packages/numpy.libs").glob("msvcp140-*.dll"))
        for directory in (python.parent, base.parent):
            shutil.copyfile(runtime, directory / "msvcp140.dll")

    templates, defaults = DEPLOY / "templates", BUNDLE / "defaults"
    for name in ("models.yaml", "mars.env"):  # site defaults from the bundle win over the public templates
        if (defaults / name).is_file():
            place(defaults / name, LOCAL / name)
    place(templates / "models.intranet.yaml", LOCAL / "models.yaml")
    place(templates / "sources.intranet.yaml", LOCAL / "sources.yaml")
    place(templates / "mars.env", LOCAL / "mars.env")
    place(templates / "machine.intranet.yaml", CODE / "mars-core/configs/machine.yaml")
    if manifest.get("project"):
        # A fresh project package uses its bundled training environment. Preserve existing user configuration.
        machine = CODE / "mars-core/configs/machine.yaml"
        content = machine.read_text(encoding="utf-8")
        if "python: D:/envs/project/python.exe" in content:
            content = content.replace("python: D:/envs/project/python.exe", "python: '" + str(python).replace("'", "''") + "'")
            machine.write_text(content, encoding="utf-8")
        project = ROOT / manifest["project"]["path"]
        source, archive = project / "source", project / "source.bundle"
        git = str(git_cmd / "git.exe") if git_cmd else "git"
        if archive.is_file() and not source.exists():
            run([git, "-c", "core.autocrlf=false", "clone", "--quiet", str(archive), str(source)])
    keys = LOCAL / "models.env"
    if (defaults / "models.env").is_file():
        place(defaults / "models.env", keys)
    if not keys.exists():
        keys.write_text("# 模型 API Key：每行 KEY=VALUE，变量名与 local/models.yaml 的 api_key_env 一致。不要提交、不要外传。\n"
                        "MARS_STRONG_API_KEY=\nMARS_FAST_API_KEY=\nMARS_CODER_API_KEY=\n", encoding="utf-8")
    private(keys)
    (LOCAL / "paths.json").write_text(json.dumps({
        "platform": manifest["platform"], "installed": time.strftime("%Y-%m-%d %H:%M:%S"), "venv_python": str(python),
        "node": str(node), "codingagent_cjs": str(codingagent), "git_cmd_dir": str(git_cmd) if git_cmd else None,
        "training_cpu": manifest.get("training") == "CPU",
        "installer": "uv" if uv else "pip"}, ensure_ascii=False, indent=1), encoding="utf-8")
    say("安装完成")
    if args.no_check:
        return 0
    return subprocess.run([str(base), "-I", str(DEPLOY / "install/run.py"), "check"]).returncode


if __name__ == "__main__":
    raise SystemExit(main())
