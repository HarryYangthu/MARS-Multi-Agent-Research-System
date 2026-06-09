"""Adapter that turns a registered agent into an orchestrator NodeRunner.

This module is part of bridge/ (it's allowed to depend on the registry +
agent Protocol, but not on concrete agent classes). Concrete classes are
already inside the registry by the time this runs.
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from app.bridge.agent_registry import get_registry
from app.harness.schema.frontmatter_parser import parse as parse_fm
from app.storage.artifact_store import ArtifactStore, ArtifactValidationError
from app.storage.run_store import RunHandle


async def run_agent_node(
    run: RunHandle,
    node_key: str,
    *,
    bus: Any | None = None,
) -> None:
    """Default NodeRunner: look the agent up by key, draft, validate, persist.

    Falls back to a no-op if the agent isn't registered (e.g. during
    Phase 2 tests). This keeps the orchestrator capable of progressing
    without breaking schema-pillar guarantees.
    """
    reg = get_registry()
    if not reg.has(node_key):
        logger.debug("no agent registered for '{}', running stub", node_key)
        return

    agent: Any = reg.get(node_key)

    # Seed-artifact short-circuit: if a v1 already exists on disk for this
    # agent (typically dropped by POST /api/runs?seed_artifact=...), skip
    # the LLM draft entirely. The orchestrator will still park at
    # WAITING_REVIEW so the human can edit/approve.
    from app.storage.artifact_store import SCHEMA_TO_AGENT

    seed_short_circuit = False
    for _schema, (dir_name, stem) in SCHEMA_TO_AGENT.items():
        if dir_name != node_key:
            continue
        if (run.subdir(node_key) / f"{stem}.v1.md").exists():
            logger.info(
                "agent {} has seed artifact on disk; skipping LLM draft", node_key
            )
            seed_short_circuit = True
        break
    if seed_short_circuit:
        return

    # Build a RunRequest from on-disk state.
    user_request = ""
    user_request_path = run.subdir("input") / "user_request.md"
    if user_request_path.exists():
        user_request = user_request_path.read_text(encoding="utf-8")

    # Pick up upstream approved artifacts as handoff.
    upstream: dict[str, str] = {}
    for sub in ("idea", "experiment", "coding", "execution"):
        if sub == node_key:
            break
        d = run.subdir(sub)
        if not d.exists():
            continue
        for p in sorted(d.glob("*.approved.md")):
            upstream[p.name] = p.read_text(encoding="utf-8")

    # Construct request + context via the agent's own builders.
    from app.agents.base import RunRequest as AgentRunRequest

    # Tell debate-on agents where to drop the streaming transcript so the
    # UI can poll-and-display it while the LLM round-trips run.
    debate_path = run.subdir(node_key) / "debate_transcript.v1.md"
    debate_path.parent.mkdir(parents=True, exist_ok=True)

    # Live "thinking" stream: forward reasoning/content deltas to the run's
    # thinking channel, and buffer them so we can persist a replayable copy.
    think_buf: dict[str, list[str]] = {"reasoning": [], "content": []}

    async def _thinking_sink(payload: dict[str, Any]) -> None:
        if payload.get("event") == "thinking.delta":
            kind = str(payload.get("kind", "content"))
            think_buf.setdefault(kind, []).append(str(payload.get("text", "")))
        if bus is not None:
            await bus.publish(f"run.{run.run_id}.thinking", payload)
        run.write_event("thinking", payload)

    request = AgentRunRequest(
        project=run.project,
        user_request=user_request,
        upstream_artifacts=upstream,
        extra={
            "debate_progress_path": str(debate_path),
            "stream_publish": _thinking_sink,
            "run_id": run.run_id,
        },
    )
    context = await agent.build_context(request)

    artifact = await agent.draft(request, context)

    # Persist the streamed thinking for replay on page reload.
    if think_buf["reasoning"] or think_buf["content"]:
        try:
            (run.subdir(node_key) / "thinking.md").write_text(
                "# 思考过程 (reasoning)\n\n"
                + "".join(think_buf["reasoning"])
                + "\n\n# 输出 (content)\n\n"
                + "".join(think_buf["content"]),
                encoding="utf-8",
            )
        except OSError:
            pass

    # Validate; ALWAYS persist the artifact under <agent>/<stem>.v1.md, even
    # when schema validation fails — the HITL UI will then show the validation
    # errors and let the human fix the markdown directly. Never silently drop
    # an invalid output (that used to cause the orchestrator to "latest is
    # None"-fallback into auto-approve, skipping HITL entirely).
    validation = await agent.validate_output(artifact)
    art_store = ArtifactStore(run)

    target_text = artifact.text
    if not validation.valid:
        target_text = artifact.text + "\n\n<!-- VALIDATION ERRORS -->\n" + "\n".join(
            f"- {e.path}: {e.message}" for e in validation.errors
        )

    try:
        ref = art_store.write(text=artifact.text)
    except ArtifactValidationError as exc:
        # Schema-invalid output: write a v1 anyway (raw, bypassing validation)
        # so HITL has something to render and edit.
        from app.storage.artifact_store import SCHEMA_TO_AGENT, ArtifactRef

        stem_for_node = next(
            (s for _sid, (d, s) in SCHEMA_TO_AGENT.items() if d == node_key),
            node_key,
        )
        target_dir = run.subdir(node_key)
        target_dir.mkdir(exist_ok=True)
        target_path = target_dir / f"{stem_for_node}.v1.md"
        annotated = artifact.text + "\n\n<!-- VALIDATION ERRORS -->\n" + "\n".join(
            f"- {e.path}: {e.message}" for e in exc.result.errors
        )
        target_path.write_text(annotated, encoding="utf-8")
        logger.warning(
            "agent {} schema-invalid; v1 written for HITL ({}): {}",
            node_key,
            target_path.name,
            exc.result.first_error(),
        )
        ref = ArtifactRef(
            run_id=run.run_id,
            agent_dir=node_key,
            stem=stem_for_node,
            version="v1",
            path=target_path,
        )

    logger.info("agent {} wrote {}", node_key, ref.path.relative_to(run.root))
    # Phase 4: orchestrator owns the approval transition (HITL or auto).

    # Fallback: if the agent didn't stream the transcript directly to disk
    # (e.g. older code path), copy whatever it stuck in metadata.
    if not debate_path.exists():
        excerpt = (
            artifact.metadata.get("debate_transcript_full")
            or artifact.metadata.get("debate_transcript_excerpt")
        )
        if excerpt:
            try:
                debate_path.write_text(str(excerpt), encoding="utf-8")
            except OSError:
                pass

    # Phase 5: Context Manifest + Sedimentation hooks.
    try:
        from app.harness.context.loader import build_context
        from app.harness.context.manifest import write as write_manifest

        pack = build_context(
            agent_role=node_key,
            output_schema=getattr(agent, "output_schema", ""),
            project=run.project,
            user_request=user_request,
            upstream_handoff=upstream,
        )
        write_manifest(run_root=run.root, pack=pack, agent_name=node_key)
    except Exception as exc:  # pragma: no cover (manifest is best-effort)
        logger.warning("manifest write failed: {}", exc)

    try:
        from app.harness.sedimentation.hooks import on_agent_completed

        on_agent_completed(
            agent=node_key,
            project=run.project,
            run_id=run.run_id,
            artifact_text=artifact.text,
        )
    except Exception as exc:  # pragma: no cover
        logger.warning("sedimentation failed: {}", exc)

    # Execution Agent only PROPOSES the experiment grid at draft time; the
    # actual simulation batch runs after the human approves (orchestrator calls
    # run_execution_batch on the APPROVED transition). This gives the
    # "propose → confirm → simulate live" flow.
    if node_key == "execution":
        try:
            write_planned_experiments(run)
        except Exception as exc:  # pragma: no cover
            logger.warning("planning experiments failed: {}", exc)


def _stable_seed(text: str) -> int:
    import hashlib

    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:6], 16)


def _base_ablations(run: RunHandle) -> list[dict[str, Any]]:
    """Ablation seeds from the approved experiment_plan, else defaults."""
    plan_path = run.subdir("experiment") / "experiment_plan.approved.md"
    out: list[dict[str, Any]] = []
    if plan_path.exists():
        try:
            md = parse_fm(plan_path.read_text(encoding="utf-8")).metadata
            ablations = md.get("ablations", []) or []
            if isinstance(ablations, list):
                for i, a in enumerate(ablations):
                    if isinstance(a, dict):
                        out.append(
                            {
                                "name": str(a.get("name") or f"ablation_{i}"),
                                "config": dict(a.get("config", {}) or {}),
                            }
                        )
        except Exception:
            out = []
    if not out:
        out = [
            {"name": "expert_count_8", "config": {"expert_count": 8}},
            {"name": "expert_count_4", "config": {"expert_count": 4}},
            {"name": "router_topk_1", "config": {"router_topk": 1}},
            {"name": "snr_30db", "config": {"snr_db": 30}},
        ]
    return out


def write_planned_experiments(run: RunHandle) -> list[dict[str, Any]]:
    """Expand the ablation matrix into ~`planned_experiments` concrete jobs and
    persist them so the UI can show "what will run" before the human approves."""
    import json

    from app.execution.config import get_execution_config

    target = max(1, get_execution_config().planned_experiments)
    base = _base_ablations(run)
    templates = ("exponential_decay", "noisy_decay", "plateau")
    experiments: list[dict[str, Any]] = []
    idx = 0
    while len(experiments) < target:
        a = base[idx % len(base)]
        variant = idx // len(base)
        exp_id = a["name"] if variant == 0 else f"{a['name']}_s{variant}"
        cfg = dict(a["config"])
        cfg["label"] = exp_id
        experiments.append(
            {
                "experiment_id": exp_id,
                "label": exp_id,
                "config": cfg,
                "template": templates[len(experiments) % len(templates)],
                "seed": _stable_seed(exp_id),
            }
        )
        idx += 1
    payload = {"experiments": experiments, "count": len(experiments)}
    (run.subdir("execution") / "planned_experiments.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return experiments


async def run_execution_batch(*, run: RunHandle, bus: Any | None = None) -> None:
    """Run the planned experiment grid (≤ max_concurrency) with live streaming.

    Reads `execution/planned_experiments.json` (written at draft time). Streams
    per-experiment events to `run.<id>.experiment.<exp>` AND an aggregated
    `run.<id>.execution` channel so a single run-socket can drive the curve wall.
    """
    import json

    from app.execution.batch_runner import BatchConfig, run_batch
    from app.execution.config import get_execution_config
    from app.execution.curve_parser import write_curve
    from app.execution.metrics_collector import (
        write_metrics_json,
        write_run_log,
    )
    from app.execution.simulation_runner import JobSpec

    exec_cfg = get_execution_config()
    planned_path = run.subdir("execution") / "planned_experiments.json"
    if planned_path.exists():
        plan = json.loads(planned_path.read_text(encoding="utf-8")).get("experiments", [])
    else:
        plan = write_planned_experiments(run)

    specs = [
        JobSpec(
            run_id=run.run_id,
            experiment_id=str(e["experiment_id"]),
            project=run.project,
            config=dict(e.get("config", {})),
            template=str(e.get("template", "exponential_decay")),
            seed=int(e["seed"]) if e.get("seed") is not None else None,
        )
        for e in plan
    ]

    async def _publish(channel: str, payload: dict[str, Any]) -> None:
        if bus is not None:
            await bus.publish(channel, payload)
            # Mirror per-experiment events onto one aggregated channel so the
            # run detail page only needs a single socket for all 16 panels.
            if channel.startswith(f"run.{run.run_id}.experiment."):
                await bus.publish(f"run.{run.run_id}.execution", payload)
        run.write_event("websocket_events", {"channel": channel, **payload})

    if bus is not None:
        await bus.publish(
            f"run.{run.run_id}.execution",
            {
                "event": "execution.batch_started",
                "total": len(specs),
                "experiments": [s.experiment_id for s in specs],
            },
        )

    outcome = await run_batch(
        specs,
        config=BatchConfig(
            max_concurrency=exec_cfg.max_concurrency,
            steps=exec_cfg.agent_batch_steps,
            tick_seconds=exec_cfg.tick_seconds,
        ),
        bus_publish=_publish,
    )

    for r in outcome.results:
        write_run_log(run_root=run.root, result=r, project=run.project)
        write_curve(
            run_root=run.root,
            experiment_id=r.experiment_id,
            metric_name="loss",
            values=_metrics_to_curve(r),
        )
    write_metrics_json(run_root=run.root, results=outcome.results)

    # Hand-summary for the front-end log panel.
    summary = {
        "experiments": [r.experiment_id for r in outcome.results],
        "failures": outcome.failures,
        "total": len(outcome.results),
    }
    (run.subdir("execution") / "batch_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )

    if bus is not None:
        await bus.publish(
            f"run.{run.run_id}.execution",
            {
                "event": "execution.batch_done",
                "total": len(outcome.results),
                "failures": outcome.failures,
            },
        )


def _metrics_to_curve(result: Any) -> list[float]:
    # Re-derive a deterministic curve from the result's seed so the file
    # matches what the WS stream pushed.
    from app.execution.mock_simulation import _loss_curve

    seed = abs(hash(f"{result.run_id}:{result.experiment_id}")) & 0xFFFFFF
    return _loss_curve(20, template="exponential_decay", seed=seed)


__all__ = ["run_agent_node", "run_execution_batch", "write_planned_experiments"]


# Side-effect free helpers used by tests
def list_artifacts_text(run: RunHandle) -> dict[str, str]:
    out: dict[str, str] = {}
    for sub in ("idea", "experiment", "coding", "execution", "writing"):
        d = run.subdir(sub)
        if not d.exists():
            continue
        for p in sorted(d.glob("*.approved.md")):
            out[f"{sub}/{p.name}"] = p.read_text(encoding="utf-8")
    return out


def parse_artifact_metadata(text: str) -> dict[str, Any]:
    return parse_fm(text).metadata
