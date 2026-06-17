#!/usr/bin/env python3
"""Create a 16-way simulation run that demonstrates Commander backtracking."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any


async def _main(args: argparse.Namespace) -> int:
    os.environ["MARS_MOCK_MODE"] = "always"
    os.environ.setdefault("LOCAL_VLLM_BASE_URL", "")

    from app.main import register_default_agents
    from app.bridge import commander_agent as commander_mod
    from app.bridge.diagnostics import DiagnosticsConfig, MetricRule
    from app.bridge.orchestrator import Orchestrator, RunRequest

    def demo_diagnostics(project: str) -> DiagnosticsConfig:
        return DiagnosticsConfig(
            project=project,
            max_iterations=2,
            allowed_targets=("coding", "experiment"),
            default_target="coding",
            analyzers={
                "metrics_gap": True,
                "config_sanity": True,
                "code_change_risk": True,
            },
            metric_rules=(
                MetricRule(
                    name="loss",
                    target=0.0,
                    direction="lte",
                    aggregation="max",
                ),
            ),
        )

    commander_mod.load_diagnostics_config = demo_diagnostics

    register_default_agents()
    orchestrator = Orchestrator()
    session = orchestrator.create_session(
        RunRequest(
            task=args.task,
            project=args.project,
            entrypoint="pipeline",
            user_request=args.user_request,
            auto_approve=True,
        )
    )
    await orchestrator.run(session.run.run_id)

    summary_path = session.run.subdir("execution") / "batch_summary.json"
    diagnosis_dir = session.run.subdir("diagnosis")
    summary: dict[str, Any] = {}
    if summary_path.exists():
        parsed = json.loads(summary_path.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            summary = parsed
    diagnoses = sorted(path.name for path in diagnosis_dir.glob("diagnosis.v*.md"))
    result = {
        "run_id": session.run.run_id,
        "project": session.run.project,
        "states": {key: state.value for key, state in session.graph.all_states().items()},
        "execution_summary": summary,
        "diagnoses": diagnoses,
        "run_dir": str(session.run.root),
    }
    if args.run_id_file:
        Path(args.run_id_file).write_text(f"{session.run.run_id}\n", encoding="utf-8")
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="moe-pimc")
    parser.add_argument("--task", default="sixteen_parallel_backtracking_demo")
    parser.add_argument(
        "--user-request",
        default=(
            "请启动一个联合调试任务：同时运行 16 组 PIMC 仿真实验；"
            "如果 loss/RES 指标不达标，由 Commander Agent 完成归因、回溯到目标 Agent，"
            "并追加第二轮执行链路。"
        ),
    )
    parser.add_argument("--run-id-file", default="")
    return asyncio.run(_main(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
