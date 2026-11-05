"""Build the offline MARS bundle (mars-core + mars-app + mars-deploy + every dependency) on an Internet-connected
machine, for installation on an intranet machine without any network access.

    python build/build_bundle.py --platform windows-x64 --out <dir>      # -> MARS-intranet-windows-x64-<date>.zip
    python build/build_bundle.py --platform linux-x64 --out <dir>        # -> MARS-intranet-linux-x64-<date>.tar.gz

Needs on the build machine: uv (resolution only), 7-Zip as 7zz/7z (Windows CodingAgent installer), and the developer's
verified mars-core venv (its versions become the pins). Every download is checked against the publisher's
checksum (python-build-standalone SHA256SUMS, nodejs.org SHASUMS256.txt, the CodingAgent update feed, PyPI hashes,
the pinned MinGit hash); a mismatch stops the build. Personal configs and secrets are never packed: models.yaml /
machine.yaml are replaced by templates on the target, and the staged tree is scanned for key material.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from packaging.tags import compatible_tags, cpython_tags
from packaging.utils import parse_wheel_filename

DEPLOY = Path(__file__).resolve().parents[1]
CODE = DEPLOY.parent
PY_RELEASE, PY_VERSION, NODE, CODINGAGENT = "20251014", "3.13.9", "22.23.2", "3.14.5"
PBS = f"https://github.com/astral-sh/python-build-standalone/releases/download/{PY_RELEASE}/"
CODINGAGENT_CDN = f"https://cdn-zcode.z.ai/zcode/electron/releases/{CODINGAGENT}/"
MINGIT = ("https://github.com/git-for-windows/git/releases/download/v2.55.0.windows.5/MinGit-2.55.0.5-64-bit.zip",
          "56d7b226b7693196cfc71fef26568f536c4a021ab6c37ff2db4287bed908e96e")
PLATFORMS = {
    "windows-x64": {"uv": "x86_64-pc-windows-msvc", "wheel_platforms": ["win_amd64"],
                    "python": f"cpython-{PY_VERSION}+{PY_RELEASE}-x86_64-pc-windows-msvc-install_only_stripped.tar.gz",
                    "node": f"node-v{NODE}-win-x64.zip", "codingagent": f"windows-x64/ZCode-{CODINGAGENT}-win-x64.exe"},
    "linux-x64": {"uv": "x86_64-manylinux_2_28",  # glibc 2.28+: RHEL/Rocky 8+, Ubuntu 20.04+, Kylin V10, UOS 20
                  "wheel_platforms": [f"manylinux_2_{m}_x86_64" for m in range(28, 16, -1)] + ["manylinux2014_x86_64"],
                  "python": f"cpython-{PY_VERSION}+{PY_RELEASE}-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz",
                  "node": f"node-v{NODE}-linux-x64.tar.xz", "codingagent": f"linux-x64/ZCode-{CODINGAGENT}-linux-x64.deb"},
}
SITE_DEFAULTS = ("models.yaml", "mars.env", "models.env")
NEVER_PACK = {"configs/models.yaml", "configs/machine.yaml", "configs/sources.yaml"}  # personal; templates on the target
SKIP_DIRS = {".venv", "node_modules", "__pycache__", ".pytest_cache", ".git", "runs", "workspace", "data", "dist", "local"}
SECRETS = [re.compile(p) for p in (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", r"\b[0-9a-f]{32}\.[A-Za-z0-9]{16}\b",
                                   r"\bsk-[A-Za-z0-9_-]{24,}", r"(?i)\b(api_?key|secret|password)\s*[:=]\s*['\"][^'\"\s$]{12,}['\"]")]


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def digest(path: Path, algorithm: str = "sha256") -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def fetch(url: str, path: Path) -> Path:
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    log(f"下载 {url}")
    part = path.with_name(path.name + ".part")
    with urllib.request.urlopen(url, timeout=600) as response, part.open("wb") as target:
        shutil.copyfileobj(response, target)
    part.replace(path)
    return path


def verified(path: Path, expected: str, algorithm: str = "sha256") -> Path:
    actual = digest(path, algorithm)
    if actual != expected.lower():
        path.unlink()
        raise SystemExit(f"校验失败：{path.name} {algorithm} {actual} != {expected}（文件已删除）")
    return path


def checksum_from_list(url: str, name: str, cache: Path) -> str:
    text = fetch(url, cache / ("sums-" + hashlib.sha1(url.encode()).hexdigest()[:10] + ".txt")).read_text()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name:
            return parts[0]
    raise SystemExit(f"{url} 里没有 {name} 的校验和")


# ---------------- sources ----------------
def packable(rel: str) -> bool:
    parts = rel.split("/")
    return not (rel in NEVER_PACK or any(p in SKIP_DIRS or p.endswith(".egg-info") for p in parts[:-1])
                or parts[-1] == ".DS_Store" or rel.endswith((".pyc", ".tsbuildinfo", ".part")))


def head_commit(root: Path) -> str | None:
    if not (root / ".git").exists():
        return None
    done = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=root, capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else None


def stage_tree(root: Path, target: Path) -> dict:
    """A repository with commits is packed from HEAD (git archive): a consistent snapshot, never a half-finished edit
    of the working tree. Other directories are packed from the working tree."""
    commit = head_commit(root)
    if commit:
        data = subprocess.run(["git", "archive", "--format=tar", commit], cwd=root, capture_output=True, check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            members = [m for m in archive.getmembers() if m.isfile() and packable(m.name)]
            archive.extractall(target, members=members, filter="data")
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root,
                                    capture_output=True, text=True).stdout.strip())
        return {"files": len(members), "commit": commit, "uncommitted_changes_not_packed": dirty}
    files = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()]
    files = [rel for rel in files if packable(rel)]
    for rel in files:
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, target / rel)
    return {"files": len(files), "commit": None}


def stage_sources(stage: Path) -> dict[str, dict]:
    dist, src = CODE / "mars-app/web/dist/index.html", CODE / "mars-app/web"
    if not dist.is_file():
        raise SystemExit("mars-app/web/dist 不存在：请先在 mars-app 构建前端（npm run build），内网不需要 Node")
    newest = max(p.stat().st_mtime for p in [*(src / "src").rglob("*"), src / "index.html", src / "package.json"] if p.is_file())
    if newest > dist.stat().st_mtime:
        raise SystemExit("mars-app/web/dist 比前端源码旧：请先重新构建前端（npm run build）")
    counts = {}
    for name in ("mars-core", "mars-app", "mars-deploy"):
        counts[name] = stage_tree(CODE / name, stage / "code" / name)
        if counts[name].get("uncommitted_changes_not_packed"):
            log(f"{name} 有未提交的修改，按 HEAD {counts[name]['commit'][:8]} 打包，未提交的修改不会进入离线包"
                + ("；注意 web/dist 取自工作区，可能和 HEAD 的前端源码不一致" if name == "mars-app" else ""))
    for path in (src / "dist").rglob("*"):  # the built frontend is git-ignored but is exactly what the intranet needs
        if path.is_file() and path.name != ".DS_Store":
            target = stage / "code/mars-app" / path.relative_to(CODE / "mars-app")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    leaks = []
    for path in (stage / "code").rglob("*"):
        if path.is_file() and path.stat().st_size < 2_000_000:
            text = path.read_bytes().decode("utf-8", "ignore")
            if any(p.search(text) for p in SECRETS):
                leaks.append(str(path.relative_to(stage)))
    if leaks:
        raise SystemExit("暂存的源码里疑似有密钥材料，已停止打包（只列文件名，不显示内容）：\n  " + "\n  ".join(leaks))
    return counts


# ---------------- dependencies ----------------
def requirements(work: Path, code: Path = CODE, training: bool = False) -> tuple[Path, Path]:
    """One shared environment: the dependencies (and test runners) of both pyproject.toml files, pinned to the versions
    of the developers' verified venvs (mars-core's first; packages only mars-app uses from mars-app's venv)."""
    core = tomllib.loads((code / "mars-core/pyproject.toml").read_text())  # the staged snapshot, not the working tree
    app = tomllib.loads((code / "mars-app/pyproject.toml").read_text())
    wanted = [*core["project"]["dependencies"], *core["project"].get("optional-dependencies", {}).get("dev", []),
              *app["project"]["dependencies"], *app.get("dependency-groups", {}).get("dev", [])]
    pins: dict[str, str] = {}
    for venv in ("mars-app", "mars-core"):  # mars-core last: its pins win
        python = CODE / venv / ".venv/bin/python"
        frozen = subprocess.run(["uv", "pip", "freeze", "--python", str(python)], capture_output=True, text=True, check=True).stdout
        pins.update({line.split("==")[0].lower(): line for line in frozen.splitlines()
                     if "==" in line and not line.startswith(("-e", "mars-core", "mars-app"))})
    if training:
        scientific = {"torch": "2.11.0+cpu", "numpy": "2.3.5", "scipy": "1.16.3", "matplotlib": "3.10.6",
                      "seaborn": "0.13.2", "pandas": "2.3.3", "tensorboard": "2.21.0", "protobuf": "6.33.6"}
        pins.update({name: f"{name}=={version}" for name, version in scientific.items()})
        wanted += [f"{name}=={version}" for name, version in scientific.items() if name != "torch"]
        wanted.append("torch @ https://download-r2.pytorch.org/whl/cpu/torch-2.11.0%2Bcpu-cp313-cp313-win_amd64.whl"
                      "#sha256=ed70d4a4fc9f8b826c02fa1a9800a83820fb2fa6ae607680b53390f9ef394d85")
    (work / "requirements.in").write_text("\n".join(wanted) + "\n")
    (work / "constraints.txt").write_text("\n".join(sorted(pins.values())) + "\n")
    return work / "requirements.in", work / "constraints.txt"


def wheels(platform: str, bundle: Path, cache: Path, training: bool = False) -> int:
    spec = PLATFORMS[platform]
    work = Path(tempfile.mkdtemp(prefix="mars-resolve-"))
    wanted, constraints = requirements(work, bundle.parent / "code", training)
    lock = work / f"pylock.{platform}.toml"
    log(f"解析 {platform} 依赖（版本锁定为本机已验证的 mars-core / mars-app 环境）")
    subprocess.run(["uv", "pip", "compile", str(wanted), "-c", str(constraints),
                    "--python-platform", spec["uv"], "--python-version", PY_VERSION.rsplit(".", 1)[0], "--only-binary", ":all:",
                    "--format", "pylock.toml", "-o", str(lock), "--quiet"], check=True)
    shutil.copy2(wanted, bundle / "requirements.in")
    major, minor = map(int, PY_VERSION.split(".")[:2])
    tags = [*cpython_tags((major, minor), platforms=spec["wheel_platforms"]),
            *compatible_tags((major, minor), interpreter=f"cp{major}{minor}", platforms=spec["wheel_platforms"])]
    rank = {tag: i for i, tag in enumerate(tags)}
    rows = []
    for package in tomllib.loads(lock.read_text())["packages"]:
        choices = []
        for wheel in package.get("wheels", []) or ([package["archive"]] if "archive" in package else []):
            filename = urllib.parse.unquote(wheel["url"].rsplit("/", 1)[-1])
            matching = parse_wheel_filename(filename)[3] & rank.keys()
            if matching:
                choices.append((min(rank[t] for t in matching), filename, wheel))
        if not choices:
            raise SystemExit(f"{package['name']} {package['version']} 没有 {platform} 可用的 wheel")
        _, filename, wheel = min(choices, key=lambda c: c[0])
        target = bundle / "wheels" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(verified(fetch(wheel["url"], cache / "wheels" / filename), wheel["hashes"]["sha256"]), target)
        rows.append(f"{package['name']}=={package['version']} --hash=sha256:{wheel['hashes']['sha256']}")
    (bundle / "requirements.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")
    shutil.copy2(lock, bundle / lock.name)
    return len(rows)


def codingagent_runtime(platform: str, bundle: Path, cache: Path) -> str:
    """Only the command runtime (glm/, tools/, config/ under resources/) of the official CodingAgent release."""
    url = CODINGAGENT_CDN + PLATFORMS[platform]["codingagent"]
    installer = fetch(url, cache / Path(url).name)
    note = f"Official CodingAgent {CODINGAGENT} ({platform}) runtime, extracted from {url}\nsha256={digest(installer)}\n"
    work = Path(tempfile.mkdtemp(prefix="mars-codingagent-"))
    if platform == "windows-x64":
        feed = fetch(CODINGAGENT_CDN + "windows-x64/latest.yml", cache / f"codingagent-{CODINGAGENT}-latest.yml").read_text()
        match = re.search(rf"url: {re.escape(Path(url).name)}\s+sha512: (\S+)", feed)
        if not match or base64.b64encode(bytes.fromhex(digest(installer, "sha512"))).decode() != match.group(1):
            raise SystemExit("CodingAgent 安装包与官方更新源 latest.yml 的 sha512 不一致")
        note += "verified against the official update feed latest.yml (sha512)\n"
        sevenzip = shutil.which("7zz") or shutil.which("7z")
        if not sevenzip:
            raise SystemExit("需要 7-Zip 解开 Windows 版 CodingAgent 安装包：brew install sevenzip")
        subprocess.run([sevenzip, "x", "-y", str(installer), f"-o{work / 'nsis'}"], check=True, stdout=subprocess.DEVNULL)
        subprocess.run([sevenzip, "x", "-y", str(work / "nsis/$PLUGINSDIR/app-64.7z"), f"-o{work / 'app'}"], check=True,
                       stdout=subprocess.DEVNULL)
        resources = work / "app/resources"
    else:
        subprocess.run(["tar", "-xf", str(installer), "-C", str(work)], check=True)  # bsdtar/GNU ar-aware tar reads .deb
        data = next(work.glob("data.tar*"))
        (work / "root").mkdir()
        subprocess.run(["tar", "-xf", str(data), "-C", str(work / "root")], check=True)
        resources = next((work / "root").rglob("resources/glm")).parent
        note += "no upstream checksum is published for the .deb; sha256 recorded above and in the bundle manifest\n"
    target = bundle / "codingagent.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in ("glm", "tools", "config"):
            for path in sorted((resources / name).rglob("*")):
                if path.is_file() and not path.is_symlink():
                    archive.write(path, path.relative_to(resources).as_posix())
        archive.writestr("CODINGAGENT-SOURCE.txt", note)
    shutil.rmtree(work, ignore_errors=True)
    return note.splitlines()[0]


def stage_project(src: Path, stage: Path, directory: str = "demo") -> dict:
    """Pack a prepared demo project (standard layout with .mars/project.yaml) verbatim, as a safety net dropping any
    `codingagent/` raw-log directory and a top-level `workspace/` (weights/repo) even if the source still has them. The
    kernel's diff.json / codingagent_summary.json caches under runs/<id>/step2/.../i<k>/ are kept, so the project is
    viewable without the project's repository or data."""
    if not (src / ".mars/project.yaml").is_file():
        raise SystemExit(f"演示项目缺少 .mars/project.yaml：{src}")
    dst_root = stage / directory / src.name
    stripped = {"codingagent": 0, "workspace": 0}
    for path in sorted(src.rglob("*")):
        if path.is_symlink():
            continue
        parts = path.relative_to(src).parts
        if {"codingagent", "zcode"}.intersection(parts[:-1]):  # current or upstream legacy runtime directories
            stripped["codingagent"] += path.is_file()
            continue
        if parts and parts[0] == "workspace":  # candidate repo / weights, not needed to view results
            stripped["workspace"] += path.is_file()
            continue
        if path.name == "run.lock" or path.name.startswith("checkpoint.sqlite"):  # kernel internals, not for viewing
            stripped["internal"] = stripped.get("internal", 0) + path.is_file()
            continue
        if path.is_file() and path.name != ".DS_Store":
            target = dst_root / path.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    kept = sum(1 for p in dst_root.rglob("*") if p.is_file())
    log(f"{'研究项目' if directory == 'projects' else '演示项目'} {src.name}：打入 {kept} 个文件，剔除 codingagent 原始日志 {stripped['codingagent']}、workspace {stripped['workspace']}、"
        f"内核内部文件 {stripped.get('internal', 0)}")
    return {"name": src.name, "path": f"{directory}/{src.name}", "files": kept, "stripped": stripped}


def build(platform: str, out: Path, cache: Path, demo: Path | None = None, project: Path | None = None,
          model_keys: Path | None = None, site_defaults: Path | None = None) -> Path:
    spec = PLATFORMS[platform]
    name = f"MARS-{'study' if project else 'intranet'}-{platform}-{time.strftime('%Y%m%d')}"
    stage = Path(tempfile.mkdtemp(prefix="mars-bundle-")) / name
    bundle = stage / "bundle"
    bundle.mkdir(parents=True)
    log("暂存源码")
    counts = stage_sources(stage)
    demo_info = stage_project(demo.resolve(), stage) if demo else None
    if project and platform != "windows-x64":
        raise SystemExit("带CPU训练环境的研究项目包当前仅构建Windows x64")
    project_info = stage_project(project.resolve(), stage, "projects") if project else None
    python = fetch(PBS + urllib.parse.quote(spec["python"]), cache / spec["python"])
    verified(python, checksum_from_list(PBS + "SHA256SUMS", spec["python"], cache))
    shutil.copy2(python, bundle / "python.tar.gz")
    node = fetch(f"https://nodejs.org/dist/v{NODE}/{spec['node']}", cache / spec["node"])
    verified(node, checksum_from_list(f"https://nodejs.org/dist/v{NODE}/SHASUMS256.txt", spec["node"], cache))
    shutil.copy2(node, bundle / ("node.zip" if spec["node"].endswith(".zip") else "node.tar.xz"))
    if platform == "windows-x64":
        shutil.copy2(verified(fetch(MINGIT[0], cache / Path(MINGIT[0]).name), MINGIT[1]), bundle / "mingit.zip")
    codingagent = codingagent_runtime(platform, bundle, cache)
    packages = wheels(platform, bundle, cache, training=project is not None)
    defaults = bundle / "defaults"
    for name in SITE_DEFAULTS:  # site connection defaults (intranet addresses) live outside the public source
        if site_defaults and (site_defaults / name).is_file():
            defaults.mkdir(exist_ok=True)
            shutil.copyfile(site_defaults / name, defaults / name)
    if model_keys:
        defaults.mkdir(exist_ok=True)
        shutil.copyfile(model_keys, defaults / "models.env")
    if (defaults / "models.env").is_file():
        (defaults / "models.env").chmod(0o600)
    for script in sorted((DEPLOY / "install/entry").iterdir()):  # double-click / ./ entry points at the bundle root
        if script.is_file() and (script.suffix == ".cmd") == (platform == "windows-x64"):
            shutil.copy2(script, stage / script.name)  # copy2 keeps the executable bit of the Linux scripts
    if (DEPLOY / "README.md").is_file():
        shutil.copy2(DEPLOY / "README.md", stage / "README.md")
    files = [{"path": p.relative_to(stage).as_posix(), "bytes": p.stat().st_size, "sha256": digest(p)}
             for p in sorted(stage.rglob("*")) if p.is_file()]
    total = sum(f["bytes"] for f in files)
    manifest = {"schema": "mars.intranet.bundle.v1", "platform": platform, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                "python": PY_VERSION, "node": NODE, "codingagent": codingagent, "wheels": packages, "sources": counts,
                "total_bytes": total, "total_mb": round(total / 1048576, 1), "demo": demo_info,
                "project": project_info, "training": "CPU" if project else None,
                "model_keys_preconfigured": (defaults / "models.env").is_file(),
                "site_defaults": sorted(p.name for p in defaults.iterdir()) if defaults.is_dir() else [], "files": files}
    (stage / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    out.mkdir(parents=True, exist_ok=True)
    if platform == "windows-x64":
        archive = out / f"{name}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    zf.write(path, f"{name}/{path.relative_to(stage).as_posix()}")
    else:
        archive = out / f"{name}.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            tf.add(stage, arcname=name)
    shutil.rmtree(stage.parent, ignore_errors=True)
    size = archive.stat().st_size
    if size > 1 << 30:  # R3: the delivered package must be under 1 GB
        archive.unlink()
        raise SystemExit(f"交付包 {size / 1048576:.0f} MB 超过 1 GB 上限；请精简内容后重打")
    log(f"完成：{archive}（压缩包 {size / 1048576:.0f} MB，解压 {manifest['total_mb']:.0f} MB，"
        f"{len(files)} 个文件，{packages} 个 wheel）")
    return archive


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=sorted(PLATFORMS), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=DEPLOY / "dist/cache")
    parser.add_argument("--demo-project", type=Path, help="已组装好的演示项目目录（标准项目布局），原样打进 demo/<名称>/")
    parser.add_argument("--project", type=Path, help="可写研究项目，打进 projects/<名称>/，附Windows CPU训练依赖")
    parser.add_argument("--model-keys", type=Path, help="用户明确指定的本机默认Key文件；只作为离线安装数据，不进入源码")
    parser.add_argument("--site-defaults", type=Path, help="本单位默认配置目录（models.yaml、mars.env、models.env），如 local/site-defaults；不进入源码")
    args = parser.parse_args()
    if not shutil.which("uv"):
        raise SystemExit("需要 uv（只用于解析依赖版本）")
    build(args.platform, args.out.resolve(), args.cache.resolve(), args.demo_project, args.project, args.model_keys, args.site_defaults)


if __name__ == "__main__":
    sys.exit(main())
