"""Exercise the real local API; never seed an artifact or replace a service."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen

from loguru import logger


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "execute", "stop", "status"])
    parser.add_argument("--port", type=int, default=8011)
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
