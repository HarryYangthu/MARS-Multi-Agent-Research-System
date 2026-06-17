"""Execution Agent — code_spec → run_log.

In Phase 3 this agent only validates / serializes the run_log shape via the
LLM (or mock). Phase 6 wires in the real ``execution/simulation_runner.py``
and the multi-experiment WS plumbing.
"""
from __future__ import annotations

from app.agents.base import Artifact, BaseAgent, ContextPack, RunRequest
from app.harness.schema.frontmatter_parser import dumps as fm_dumps


def _default_execution_plan() -> list[dict[str, object]]:
    memories = [2, 4, 8, 16]
    learning_rates = [0.045, 0.055, 0.065, 0.08]
    out: list[dict[str, object]] = []
    for memory in memories:
        for lr in learning_rates:
            out.append(
                {
                    "name": f"mem_{memory:02d}_lr_{str(lr).replace('.', 'p')}",
                    "config": {
                        "expert_count": memory,
                        "learning_rate": lr,
                        "plot_every_steps": 5,
                    },
                }
            )
    return out


class ExecutionAgent(BaseAgent):
    name = "execution"
    output_schema = "run_log.v1"
    agent_brief = (
        "你负责把代码规格转化为可执行的仿真批次并汇总 run_log。实际仿真由 Execution "
        "流水线驱动(无 GPU 时走 mock_simulation);可用 execution.metrics_collector / "
        "execution.log_streamer 读取已完成 run 的指标与日志来汇总结果。"
    )

    async def draft(
        self, request: RunRequest, context: ContextPack
    ) -> Artifact:
        experiments = _default_execution_plan()
        metadata = {
            "schema": self.output_schema,
            "project": request.project,
            "agent": self.name,
            "upstream_artifact": "code_spec.approved.md",
            "run_id": "pending-human-approval",
            "batch_size": 512,
            "gpu_used": ["cpu-local"],
            "duration_seconds": 0,
            "status": "interrupted",
            "metrics": {
                "planned_experiments": len(experiments),
                "max_concurrency": 16,
                "plot_every_steps": 5,
            },
            "fingerprint_hash": "sha256:0000000000000000",
            "is_mock": False,
            "planned_experiments": experiments,
            "requires_human_approval": True,
        }
        rows = "\n".join(
            "| {idx} | `{name}` | `{expert}` | `{lr}` |".format(
                idx=i + 1,
                name=exp["name"],
                expert=(exp["config"] if isinstance(exp["config"], dict) else {}).get(
                    "expert_count", ""
                ),
                lr=(exp["config"] if isinstance(exp["config"], dict) else {}).get(
                    "learning_rate", ""
                ),
            )
            for i, exp in enumerate(experiments)
        )
        body = (
            "# 执行计划\n\n"
            "Execution Agent 将在人工批准后运行以下 16 组 PIM cancellation CPU 仿真。\n"
            "每组仿真每 5 次迭代覆盖刷新一次 loss PNG，前端可以在执行过程中看到曲线逐步下降。\n\n"
            "| # | 实验 | Expert / memory taps | Learning rate |\n"
            "|---:|---|---:|---:|\n"
            f"{rows}\n\n"
            "批准该产物后，将启动完整的 16 组仿真批处理。"
        )
        return Artifact(
            text=fm_dumps(metadata, body),
            schema_id=self.output_schema,
            metadata=metadata,
            body=body,
        )
