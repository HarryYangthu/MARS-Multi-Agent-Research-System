"""Build a Windows x64 source/dependency bundle on an Internet-connected host.

This is a local transfer package, not the public-release gate or a signed
installer. Every dependency is checksummed and target-platform wheels are
selected explicitly. The target installation never resolves from the Internet.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
import urllib.parse
import urllib.request
import zipfile

from packaging.tags import compatible_tags, cpython_tags
from packaging.utils import parse_wheel_filename
import yaml

PYTHON_URL = "https://github.com/astral-sh/python-build-standalone/releases/download/20251014/cpython-3.11.14%2B20251014-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
PYTHON_SHA256 = "c2a7b0bb86eb9f1cc094a01bdabef7ddc77f89f8e45161fa7819f2b4a7ba7bc1"
ZCODE_URL = "https://cdn-zcode.z.ai/zcode/electron/releases/3.14.5/windows-x64/ZCode-3.14.5-win-x64.exe"


def _prepared_file(root: Path, name: str) -> Path:
    if (not re.fullmatch(r'[A-Za-z0-9_+.\-/]+', name) or name.startswith('/')
            or any(part in {'.', '..', ''} for part in name.split('/'))):
        raise ValueError('Invalid prepared dependency path')
    current = root
    for part in Path(name).parts:
        current /= part
        if current.is_symlink():
            raise ValueError('Prepared dependencies cannot contain symbolic links')
    if not current.is_file() or not current.resolve().is_relative_to(root):
        raise ValueError('Prepared dependency file is missing or outside its root: ' + name)
    return current


def verify_prepared_dependencies(prepared: Path, source: Path) -> None:
    """Validate the original cache receipt before accepting or copying bytes."""
    if prepared.is_symlink() or not prepared.is_dir():
        raise ValueError('Prepared dependency root is unavailable or symbolic')
    root = prepared.resolve(strict=True)
    manifest = json.loads(_prepared_file(root, 'manifest.json').read_text(encoding='utf-8'))
    if (not isinstance(manifest, dict) or manifest.get('schema') != 'mars.windows.dependencies.v1'
            or manifest.get('platform') != 'windows-x64' or manifest.get('python') != '3.11.14'
            or not isinstance(manifest.get('files'), list) or not manifest['files']):
        raise ValueError('Invalid prepared dependency manifest')
    seen: set[str] = set()
    declared: set[str] = set()
    for entry in manifest['files']:
        if not isinstance(entry, dict) or not isinstance(entry.get('path'), str):
            raise ValueError('Invalid prepared dependency entry')
        name = entry['path']
        checksum = entry.get('sha256')
        if (name.casefold() in seen or name == 'manifest.json' or not isinstance(checksum, str)
                or not re.fullmatch('[a-f0-9]{64}', checksum) or type(entry.get('bytes')) is not int):
            raise ValueError('Invalid or duplicate prepared dependency entry')
        path = _prepared_file(root, name)
        if path.stat().st_size != entry['bytes'] or digest(path) != checksum:
            raise ValueError('Prepared dependency checksum mismatch: ' + name)
        seen.add(name.casefold())
        declared.add(name)
    required = {'uv.zip', 'node.zip', 'git.zip', 'python.zip', 'zcode.zip', 'npm-cache.zip',
                'vc_redist.x64.exe', 'requirements.windows.txt'}
    if not required.issubset(declared):
        raise ValueError('Prepared dependency cache lacks required files')
    for path in root.rglob('*'):
        if path.is_symlink():
            raise ValueError('Prepared dependencies cannot contain symbolic links')
        if (path.is_file() and path.relative_to(root).as_posix() != 'manifest.json'
                and path.relative_to(root).as_posix() not in declared):
            raise ValueError('Prepared dependency cache contains unlisted files')
    config = yaml.safe_load(_prepared_file(source.resolve(strict=True), 'configs/windows_native.yaml').read_text(encoding='utf-8'))
    if not isinstance(config, dict):
        raise ValueError('Windows dependency configuration must be a mapping')
    for name in ('uv', 'node', 'git'):
        expected = config.get(name + '_sha256')
        if expected is not None and (not isinstance(expected, str) or not re.fullmatch('[a-fA-F0-9]{64}', expected)
                or digest(_prepared_file(root, name + '.zip')) != expected.lower()):
            raise ValueError('Prepared dependency differs from its pinned checksum: ' + name)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch(url: str, path: Path, expected: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and (expected is None or digest(path) == expected):
        return
    part = path.with_suffix(path.suffix + ".part")
    with urllib.request.urlopen(url, timeout=180) as response, part.open("wb") as target:
        shutil.copyfileobj(response, target)
    if expected is not None and digest(part) != expected:
        raise ValueError("Dependency checksum mismatch: " + path.name)
    part.replace(path)


def zip_tree(root: Path, target: Path, prefix: str = "") -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=4) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ValueError("Dependency archive cannot contain symbolic links")
            if path.is_file():
                archive.write(path, prefix + path.relative_to(root).as_posix())


def copy_source(source: Path, stage: Path, *, product: bool) -> None:
    roots = {"backend", "frontend", "configs", "deploy", "scripts", "desktop", "templates", "projects", "docs"}
    top_files = {"LICENSE", "pyproject.toml", "uv.lock", ".env.example", ".gitignore", "PRODUCT-SNAPSHOT.json", "README.md", "README.zh-CN.md"}
    cache_names = {"__pycache__", "node_modules", ".venv", ".pytest_cache", ".mypy_cache"}
    for directory, directories, files in os.walk(source, followlinks=False):
        parent = Path(directory)
        rel = parent.relative_to(source)
        for name in list(directories):
            path = parent / name
            relative = path.relative_to(source).as_posix()
            reject = name in cache_names or name.startswith(".next") or path.is_symlink()
            reject |= parent == source and name not in roots
            if not product:
                reject |= relative in {"backend/tests", "frontend/tests", "frontend/.tmp", "desktop/tests"}
                reject |= relative == "docs" or (bool(rel.parts) and rel.parts[0] == "scripts" and relative not in {"scripts/release"})
                reject |= relative.startswith("deploy/") and not relative.startswith("deploy/windows-native")
                reject |= relative.startswith("projects/")
            if reject:
                directories.remove(name)
        for name in files:
            path = parent / name
            relative = path.relative_to(source).as_posix()
            if path.is_symlink() or name == ".DS_Store" or name.endswith((".pyc", ".pyo", ".tsbuildinfo", ".log", ".lock.tmp")):
                continue
            if name.startswith(".env") and name != ".env.example":
                continue
            if parent == source and name not in top_files and not name.endswith(".cmd"):
                continue
            if not product and relative.startswith("configs/") and name in {"skills.lock", "report_skill_selection.yaml", "report_skill_selection.lock"}:
                continue
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    (stage / "projects").mkdir(exist_ok=True)
    if not product:
        execution_path = stage / "configs/execution.yaml"
        config = yaml.safe_load(execution_path.read_text(encoding="utf-8"))
        execution = config["execution"]
        execution.update(device="cpu", backend="local_command", local_commands=[])
        execution["paper_static"].update(enabled=False, python="python", repo_path="", data_path="")
        execution_path.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")


def prepare_dependencies(source: Path, offline: Path, downloads: Path, *, uv: str, sevenzip: str) -> None:
    config = yaml.safe_load((source / "configs/windows_native.yaml").read_text(encoding="utf-8"))
    for name in ("uv", "node", "git"):
        fetch(config[name + "_url"], offline / (name + ".zip"), config[name + "_sha256"])
    fetch(config["vc_runtime_url"], offline / "vc_redist.x64.exe")
    python_archive = downloads / "python-win.tar.gz"
    fetch(PYTHON_URL, python_archive, PYTHON_SHA256)
    with tempfile.TemporaryDirectory(prefix="mars-python-") as directory:
        extracted = Path(directory)
        with tarfile.open(python_archive, "r:gz") as archive:
            archive.extractall(extracted, filter="data")
        if not (extracted / "python/python.exe").is_file():
            raise ValueError("Windows Python distribution has no executable")
        zip_tree(extracted, offline / "python.zip")
    zcode_installer = downloads / "ZCode-3.14.5-win-x64.exe"
    fetch(ZCODE_URL, zcode_installer)
    with tempfile.TemporaryDirectory(prefix="mars-zcode-") as directory:
        extracted = Path(directory)
        subprocess.run([sevenzip, "x", "-y", str(zcode_installer), "-o" + str(extracted / "installer")], check=True, stdout=subprocess.DEVNULL)
        inner = extracted / "installer/$PLUGINSDIR/app-64.7z"
        subprocess.run([sevenzip, "x", "-y", str(inner), "-o" + str(extracted / "app")], check=True, stdout=subprocess.DEVNULL)
        resources = extracted / "app/resources"
        packed = extracted / "runtime"
        for name in ("glm", "tools", "config"):
            shutil.copytree(resources / name, packed / name)
        (packed / "DISTRIBUTION-SOURCE.txt").write_text("Official ZCode Windows 3.14.5 runtime\n" + ZCODE_URL + "\ninstaller_sha256=" + digest(zcode_installer) + "\n", encoding="utf-8")
        zip_tree(packed, offline / "zcode.zip")
    with tempfile.TemporaryDirectory(prefix="mars-wheels-") as directory:
        temp = Path(directory)
        requirements = temp / "requirements.txt"
        subprocess.run([uv, "export", "--frozen", "--all-extras", "--no-hashes", "--no-emit-project", "--no-emit-package", "torch", "--output-file", str(requirements)], cwd=source, check=True, stdout=subprocess.DEVNULL)
        base_lock = temp / "pylock.toml"
        subprocess.run([uv, "pip", "compile", str(requirements), "--python-platform", "x86_64-pc-windows-msvc", "--python-version", "3.11.14", "--only-binary", ":all:", "--format", "pylock.toml", "-o", str(base_lock), "--quiet"], check=True)
        torch_version = next(package["version"] for package in tomllib.loads((source / "uv.lock").read_text())["package"] if package["name"] == "torch")
        (temp / "torch.txt").write_text("torch==" + torch_version + "\n")
        torch_lock = temp / "torch/pylock.toml"
        torch_lock.parent.mkdir()
        subprocess.run([uv, "pip", "compile", str(temp / "torch.txt"), "--no-deps", "--python-platform", "x86_64-pc-windows-msvc", "--python-version", "3.11.14", "--only-binary", ":all:", "--format", "pylock.toml", "--index-url", config["torch_index"], "-o", str(torch_lock), "--quiet"], check=True)
        tags = list(cpython_tags((3, 11), platforms=["win_amd64"])) + list(compatible_tags((3, 11), interpreter="cp311", platforms=["win_amd64"]))
        rank = {tag: index for index, tag in enumerate(tags)}
        wheels = []
        for lock in (base_lock, torch_lock):
            for package in tomllib.loads(lock.read_text())["packages"]:
                candidates = []
                for wheel in package["wheels"]:
                    filename = urllib.parse.unquote(wheel["url"].rsplit("/", 1)[-1])
                    matching = parse_wheel_filename(filename)[3] & rank.keys()
                    if matching:
                        candidates.append((min(rank[tag] for tag in matching), wheel, filename))
                if not candidates:
                    raise ValueError("No Windows 3.11 wheel for " + package["name"])
                _, wheel, filename = min(candidates, key=lambda item: item[0])
                wheels.append({"name": package["name"], "version": package["version"], "url": wheel["url"].replace("download-r2.pytorch.org", "download.pytorch.org"), "filename": filename, "sha256": wheel["hashes"]["sha256"]})
        for name in ("pip", "wheel"):
            data = json.load(urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json", timeout=30))
            file = next(item for item in data["urls"] if item["filename"].endswith("-py3-none-any.whl"))
            wheels.append({"name": name, "version": data["info"]["version"], "url": file["url"], "filename": file["filename"], "sha256": file["digests"]["sha256"]})
        wheelhouse = offline / "wheelhouse"
        def get_wheel(record: dict[str, str]) -> None:
            fetch(record["url"], wheelhouse / record["filename"], record["sha256"])
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(get_wheel, wheels))
        (offline / "requirements.windows.txt").write_text("\n".join(f"{row['name']}=={row['version']} --hash=sha256:{row['sha256']}" for row in wheels) + "\n", encoding="utf-8")
        (offline / "wheels.json").write_text(json.dumps(wheels, indent=2), encoding="utf-8")
    lock = json.loads((source / "frontend/package-lock.json").read_text())
    urls = sorted({package["resolved"] for package in lock["packages"].values() if package.get("resolved")
        and (not package.get("os") or "win32" in package["os"]) and (not package.get("cpu") or "x64" in package["cpu"])})
    node = shutil.which("node")
    npm = shutil.which("npm")
    if node is None or npm is None:
        raise ValueError("The build host needs Node.js and npm")
    npm_cli = (Path(node).parent / "node_modules/npm/bin/npm-cli.js") if os.name == "nt" else Path(npm).resolve()
    cache = downloads / "npm-cache"
    def get_npm(url: str) -> None:
        subprocess.run([node, str(npm_cli), "cache", "add", url, "--cache", str(cache), "--ignore-scripts", "--no-audit", "--no-fund"], check=True, stdout=subprocess.DEVNULL)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(get_npm, urls))
    zip_tree(cache / "_cacache", offline / "npm-cache.zip", "_cacache/")
    print(f"Verified {len(wheels)} Windows wheels and cached {len(urls)} Windows npm packages", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-profile", choices=("product", "repository"), default="product")
    parser.add_argument("--prepared-dependencies", type=Path)
    parser.add_argument("--download-cache", type=Path)
    parser.add_argument("--sevenzip")
    arguments = parser.parse_args()
    source, output = arguments.source.resolve(), arguments.output.resolve()
    if output.exists():
        raise ValueError("Output must be a new directory; existing packages are never overwritten")
    stage = output / "MARS-Product"
    stage.mkdir(parents=True)
    copy_source(source, stage, product=arguments.source_profile == "product")
    offline_readme = stage / "deploy/windows-native/README-OFFLINE.md"
    if offline_readme.is_file():
        for name in ("README.md", "README.zh-CN.md"):
            shutil.copyfile(offline_readme, stage / name)
    # Persist offline intent in every target entrypoint, so deleting the marker
    # and caches cannot silently turn a double-click into an online installer.
    for entry in stage.glob("*.cmd"):
        content = entry.read_text(encoding="utf-8")
        if "windows-native\\Mars.ps1" in content and "%*" in content and "-OfflineRequired" not in content:
            content = content.replace(" %*", " -OfflineRequired %*")
            entry.write_bytes(content.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
    offline = stage / "offline"
    if arguments.prepared_dependencies:
        verify_prepared_dependencies(arguments.prepared_dependencies, source)
        shutil.copytree(arguments.prepared_dependencies, offline)
        verify_prepared_dependencies(offline, source)
    else:
        offline.mkdir()
        uv = shutil.which("uv")
        sevenzip = arguments.sevenzip or shutil.which("7z") or shutil.which("7zz")
        if uv is None or sevenzip is None:
            raise ValueError("The online build host needs uv and 7-Zip")
        prepare_dependencies(stage, offline, arguments.download_cache or output / "downloads", uv=uv, sevenzip=sevenzip)
    files = [{"path": path.relative_to(offline).as_posix(), "bytes": path.stat().st_size, "sha256": digest(path)}
        for path in sorted(offline.rglob("*")) if path.is_file() and path.name != "manifest.json"]
    manifest = offline / "manifest.json"
    manifest.write_text(json.dumps({"schema": "mars.windows.dependencies.v1", "platform": "windows-x64", "python": "3.11.14", "files": files}, indent=2), encoding="utf-8")
    source_files = [{"path": path.relative_to(stage).as_posix(), "sha256": digest(path)}
        for path in sorted(stage.rglob("*")) if path.is_file() and not path.is_relative_to(offline)]
    (stage / "WINDOWS-OFFLINE.json").write_text(json.dumps({"schema": "mars.windows.offline.v1", "manifest_sha256": digest(manifest), "source_files": source_files, "windows_acceptance": "see accompanying verification receipt"}, indent=2), encoding="utf-8")
    zip_tree(stage, output / "MARS-Product-Windows-Offline.zip", "MARS-Product/")
    print(json.dumps({"archive": str(output / "MARS-Product-Windows-Offline.zip"), "source_files": len(source_files), "dependency_files": len(files), "offline_bytes": sum(item["bytes"] for item in files)}), flush=True)


if __name__ == "__main__":
    main()
