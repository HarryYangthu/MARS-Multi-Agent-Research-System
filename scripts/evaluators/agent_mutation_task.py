"""Repository-owned live evaluator; never returns success without model evidence.

Invoked only by the mutation comparison runner with a frozen task request.
The checked outcome is native Agent contract acceptance, not scientific gain.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

from app.agents.base import BaseAgent, RunRequest
from app.bridge.agent_registry import get_registry
from app.harness.agent_loop.trace import atomic_json
from app.main import register_default_agents
from app.storage.agent_context_store import load_agent_runtime_resources
from app.storage.run_store import RunStore


async def evaluate(path: Path) -> int:
    raw = json.loads(path.read_text())
    task, agent_name = raw["task"], raw["agent"]
    output_root = Path(raw["output_root"]).resolve()
    if output_root != path.resolve().parent:
        raise ValueError("evaluator output must stay beside its frozen task")
    version = Path(raw["context_file"]).read_bytes().decode("utf-8")
    overrides = {str(raw["resource_path"]): version}
    resources = load_agent_runtime_resources(agent_name, overrides=overrides)
    result: dict[str, Any] = {"schema": "agent_mutation_task.v1", "task_id": task["id"],
        "host_accepted": False, "scientific_validated": False,
        "context_sha256": hashlib.sha256(version.encode()).hexdigest(),
        "resource_manifest": resources.manifest, "resource_text": resources.context,
        "total_tokens": None}
    context = None
    try:
        register_default_agents()
        agent = get_registry().get(agent_name)
        if not isinstance(agent, BaseAgent):
            raise ValueError("mutation evaluation requires a native BaseAgent")
        run = RunStore(output_root / "runs").create(task=task["id"], project=task["project"])
        request = RunRequest(project=task["project"], user_request=task["user_request"],
            upstream_artifacts=task.get("upstream", {}),
            extra={**task.get("extra", {}), "run_root": str(run.root), "run_id": run.run_id},
            runtime={"agent_resource_overrides": overrides})
        context = await agent.build_context(request)
        artifact = await agent.run_loop(request, context)
        artifact_path = output_root / "artifact.md"
        artifact_path.write_text(artifact.text, encoding="utf-8")
        result.update(host_accepted=True, artifact_ref="artifact.md",
                      artifact_sha256=hashlib.sha256(artifact_path.read_bytes()).hexdigest())
    except Exception as exc:
        # A rejected candidate or unavailable real provider is a failed task.
        # This record never stands in for successful model/tool execution.
        result.update(host_accepted=False, error_type=type(exc).__name__, error=str(exc)[:1000])
    if context is not None and context.metadata.get("loop_trace_root"):
        trace_root = Path(str(context.metadata["loop_trace_root"]))
        result["trace_ref"] = trace_root.relative_to(output_root).as_posix()
        facts_path = trace_root / "facts.json"
        if facts_path.is_file():
            facts = json.loads(facts_path.read_text())
            usage = facts.get("usage", {})
            if facts.get("usage_complete") is True:
                result["total_tokens"] = usage.get("total_tokens")
            result["counts"] = facts.get("counts", {})
    atomic_json(output_root / "result.json", result)
    # Rejection is a measured task outcome when real trace evidence is present;
    # the host independently audits every task before accepting the comparison.
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(evaluate(Path(sys.argv[1]))))
