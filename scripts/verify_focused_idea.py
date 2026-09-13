"""Exercise the real local API; never seed an artifact or replace a service."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen

from loguru import logger


def revision_analysis(root: Path) -> str:
    candidates = list(root.glob("idea/candidates/*/*.md"))
    if not candidates:
        raise ValueError("previous run has no candidate to revise")
    candidate = max(candidates, key=lambda path: path.stat().st_mtime).read_text()
    reviews = [json.loads(path.read_text()) for path in sorted(root.glob("idea/reviews/*/*.json"))]
    return ("此前生成但未通过验收的候选与评审意见，仅作修订输入，不是已确认的方法或论文证据。"
            "请重新核对必要源码与原文，明确原方法和新设计的区别，修正整份方案；不要把候选直接当作交付。\n"
            + candidate + "\n此前评审：\n" + json.dumps(reviews, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "execute", "stop", "status"])
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--revise-run", help="Supply a failed candidate and its reviews as untrusted analysis input, never a seed artifact")
    args = parser.parse_args()
    record = Path("runs/verification/focused_live_run.json")
    url = f"http://127.0.0.1:{args.port}/api/runs"
    if args.action == "start":
        payload = {
            "task": "PIMC LUT 方法研究与真实自验证", "project": "pimc",
            "entrypoint": "idea", "standalone": True, "auto_approve": False,
            "idea_scope": "project_proposal",
            "user_request": "基于项目已有知识和当前 StaticPIMC，研究幅度分布不均匀时是否可以调整 LUT 节点，提高有效表示能力，同时保留其他层与接口。请核对当前代码，调研相关方法并阅读完整方法，提出一个最小改动方案及验证办法。本次不训练，不假设已经选定了真实数据。",
        }
        if args.revise_run:
            from app.storage.run_store import RunStore
            previous = RunStore().get(args.revise_run)
            if previous is None:
                raise ValueError("previous run not found")
            payload["idea_context"] = {"analysis_results": revision_analysis(previous.root)}
        request = Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    else:
        run_id = json.loads(record.read_text())["run_id"]
        suffix = {"execute": "/start", "stop": "/stop"}.get(args.action, "")
        request = Request(url + "/" + run_id + suffix, data=b"" if suffix else None)
    with urlopen(request, timeout=60) as response:
        result = json.load(response)
    if args.action == "start":
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        with urlopen(Request(url + "/" + result["run_id"] + "/start", data=b""), timeout=60) as response:
            result["execution"] = json.load(response)
    logger.info(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
