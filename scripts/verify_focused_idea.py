"""Exercise the real local API; never seed an artifact or replace a service."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
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


def is_terminal(result: dict[str, Any]) -> bool:
    """A human review boundary completes this verification; it is not approval."""
    terminal = {"waiting_review", "failed", "completed", "cancelled", "stopped", "done"}
    states = set(result.get("states", {}).values())
    return result.get("status") in terminal or bool(states & {"waiting_review", "failed", "cancelled"}) or bool(states and states <= {"done", "skipped"})


def wait_for_run(url: str, record: Path, *, timeout: float, interval: float,
                 required_artifact: Path | None = None) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    previous = None
    heartbeat_at = 0.0
    while time.monotonic() < deadline:
        with urlopen(url, timeout=30) as response:
            result: dict[str, Any] = json.load(response)
        record.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        summary = {key: result.get(key) for key in ("run_id", "status", "states", "failure_summary")}
        if summary != previous:
            logger.info("{}", json.dumps(summary, ensure_ascii=False))
            previous = summary
        if time.monotonic() >= heartbeat_at:
            from app.storage.run_store import RunStore
            run = RunStore().get(str(result.get("run_id", "")))
            traces = list(run.root.glob("agent_traces/idea/*/facts.json")) if run else []
            if traces:
                facts = json.loads(max(traces, key=lambda path: path.stat().st_mtime).read_text())
                logger.info("Idea progress: {}", json.dumps({key: facts.get(key)
                    for key in ("status", "counts", "usage", "usage_complete")}, ensure_ascii=False))
            heartbeat_at = time.monotonic() + 30
        awaiting_old_review = ("waiting_review" in result.get("states", {}).values()
                               and required_artifact is not None and not required_artifact.exists())
        if is_terminal(result) and not awaiting_old_review:
            return result
        time.sleep(min(interval, max(0, deadline - time.monotonic())))
    raise TimeoutError("verification deadline reached; inspect the persisted run; no success was assumed")


def execute(args: argparse.Namespace) -> None:
    record = Path(args.record)
    record.parent.mkdir(parents=True, exist_ok=True)
    url = f"http://127.0.0.1:{args.port}/api/runs"
    required_artifact = None
    if args.action == "start":
        payload: dict[str, Any] = {
            "task": "PIMC LUT 方法研究与真实自验证", "project": "pimc",
            "entrypoint": "idea", "standalone": True, "auto_approve": False,
            "idea_scope": "project_proposal",
            "user_request": "基于项目已有知识和当前 StaticPIMC，研究幅度分布不均匀时是否可以调整 LUT 节点，提高有效表示能力，同时保留其他层与接口。请核对当前代码，调研相关方法并阅读完整方法，提出一个最小改动方案及验证办法。本次不训练，不假设已经选定了真实数据。",
        }
        if args.request_file:
            payload = json.loads(Path(args.request_file).read_text())
            if payload.get("entrypoint") != "idea" or payload.get("standalone") is not True or payload.get("auto_approve") is not False:
                raise ValueError("verification requires standalone Idea with auto_approve=false")
        if args.revise_run:
            from app.storage.run_store import RunStore
            previous = RunStore().get(args.revise_run)
            if previous is None:
                raise ValueError("previous run not found")
            payload.setdefault("idea_context", {})["analysis_results"] = revision_analysis(previous.root)
        request = Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    else:
        run_id = json.loads(record.read_text())["run_id"]
        suffix = {"execute": "/start", "stop": "/stop"}.get(args.action, "")
        request = Request(url + "/" + run_id + suffix, data=b"" if suffix else None)
        if args.action == "revise":
            from app.storage.run_store import RunStore
            from app.storage.artifact_store import ArtifactStore
            run = RunStore().get(run_id)
            if run is None or not args.reason_file:
                raise ValueError("revise requires an existing run and --reason-file")
            versions = [int(ref.version[1:]) for ref in ArtifactStore(run).list_versions(agent_dir="idea", stem="idea_proposal")
                        if ref.version.startswith("v")]
            required_artifact = run.root / "idea" / f"idea_proposal.v{max(versions, default=0) + 1}.md"
            reason = Path(args.reason_file).read_text()
            request = Request(url + "/" + run_id + "/agents/idea/retry",
                              data=json.dumps({"reason": reason}).encode(), headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=60) as response:
        result = json.load(response)
    if args.action == "start":
        record.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        with urlopen(Request(url + "/" + result["run_id"] + "/start", data=b""), timeout=60) as response:
            json.load(response)
    logger.info("{}", json.dumps({k: result.get(k) for k in ("run_id", "status", "states")}, ensure_ascii=False))
    if args.wait and args.action in {"start", "execute", "status", "revise"}:
        run_id = result.get("run_id") or json.loads(record.read_text())["run_id"]
        result = wait_for_run(url + "/" + run_id, record, timeout=args.timeout, interval=args.poll_interval,
                              required_artifact=required_artifact)
        if result.get("status") == "failed" or "failed" in result.get("states", {}).values():
            raise RuntimeError("real Idea run failed; inspect its archived diagnostic and candidate")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "execute", "stop", "status", "revise"])
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--revise-run", help="Supply a failed candidate and its reviews as untrusted analysis input, never a seed artifact")
    parser.add_argument("--request-file", help="Actual API task JSON; never a prewritten output")
    parser.add_argument("--reason-file", help="Concrete feedback for the existing Idea draft, used by revise")
    parser.add_argument("--record", default="runs/verification/focused_live_run.json")
    parser.add_argument("--wait", action="store_true", help="Stop at human review, completion or failure")
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--poll-interval", type=float, default=3)
    parser.add_argument("--serve", action="store_true", help="Start a local backend in this process namespace and stop it afterwards")
    args = parser.parse_args()
    if args.timeout <= 0 or not 0 < args.poll_interval <= 30:
        parser.error("timeout must be positive; poll-interval must be in (0,30]")
    if not args.serve:
        execute(args)
        return
    if not args.wait:
        parser.error("--serve requires --wait so the backend outlives the actual task")
    log_path = Path(args.record).with_suffix(".backend.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w") as log:
        server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                   "--port", str(args.port)], stdout=log, stderr=log)
        try:
            for _ in range(80):
                if server.poll() is not None:
                    raise RuntimeError(f"backend exited before readiness; see {log_path}")
                try:
                    with urlopen(f"http://127.0.0.1:{args.port}/health", timeout=2) as response:
                        if json.load(response).get("status") == "ok":
                            break
                except OSError:
                    time.sleep(0.25)
            else:
                raise RuntimeError(f"backend readiness deadline exceeded; see {log_path}")
            execute(args)
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == "__main__":
    main()
