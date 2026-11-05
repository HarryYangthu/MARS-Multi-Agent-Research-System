"""Materialize a shipped study's historical candidate commits for NEW evaluations, never resume old runs.

Called after datasets are configured. Uses mars.api.baseline_dir to establish and validate the current binding;
Git imports exact exported commits. Original history is retained, with an explicit imported-history annotation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-c", "core.autocrlf=false", "-C", str(repo), *args],
                                   text=True, encoding="utf-8", stderr=subprocess.PIPE).strip()


def prepare(root: Path) -> dict:
    from mars import api
    root = root.resolve()
    manifest = root / ".mars/candidate-imports.json"
    raw = manifest.read_bytes()
    imports = json.loads(raw)
    source = (root / imports["source"]).resolve()
    if not source.is_relative_to(root):
        raise ValueError("候选来源必须在项目内")
    base = Path(api.baseline_dir(project_root=root))
    workspace = base.parent
    snapshot = json.loads((workspace / "binding.json").read_text(encoding="utf-8"))
    stamp = {"binding_id": snapshot["binding"]["id"], "imports_sha256": hashlib.sha256(raw).hexdigest(),
             "project_root": str(root)}
    marker = workspace / "imported-candidates.json"
    entries = imports["candidates"]
    wanted = []
    for item in entries:
        run, route, iteration = item["run_id"], item["route"], item["iteration"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", run) or not re.fullmatch(r"[A-Z][A-Za-z0-9_-]*", route) or type(iteration) is not int or iteration < 1:
            raise ValueError("候选导入声明中的运行、路线或轮次无效")
        if not re.fullmatch(r"[0-9a-f]{40}", item["commit"]):
            raise ValueError("候选导入必须固定完整Git提交")
        record = root / "runs" / run / "step2" / route / f"i{iteration}" / "record.json"
        saved = json.loads(record.read_text(encoding="utf-8"))
        name = f"{run}-{route}-i{iteration}"
        if saved.get("commit") != item["commit"] or saved.get("branch") != f"cand/{name}":
            raise ValueError("候选导入与历史记录的提交或分支不一致")
        wanted.append((item, name))
    if not marker.exists() or json.loads(marker.read_text(encoding="utf-8")) != stamp:
        paths = [str(workspace / "candidates" / name) for _, name in wanted if (workspace / "candidates" / name).exists()]
        if paths:
            git(base, "worktree", "repair", *paths)
        if wanted:
            git(base, "fetch", "-q", str(source), *dict.fromkeys(item["commit"] for item, _ in wanted))
        for item, name in wanted:
            candidate = workspace / "candidates" / name
            if candidate.exists():
                if git(candidate, "rev-parse", "HEAD") != item["commit"]:
                    raise ValueError(f"已有候选 {name} 的版本不同，拒绝覆盖")
            else:
                candidate.parent.mkdir(exist_ok=True)
                git(base, "worktree", "add", "-q", "-b", f"cand/{name}", str(candidate), item["commit"])
        for run in {item["run_id"] for item, _ in wanted}:
            target = root / "runs" / run / "execution.json"
            old = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
            if old and not old.get("imported_history"):
                raise ValueError("不能覆盖真实运行的执行快照")
            value = {**snapshot, "imported_history": {"purpose": "candidate reuse for new experiments; original run is historical",
                     "imports_sha256": stamp["imports_sha256"]}}
            temporary = target.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(temporary, target)
        marker.write_text(json.dumps(stamp), encoding="utf-8")
    return {"prepared": len(wanted), **stamp}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.project), ensure_ascii=False))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
