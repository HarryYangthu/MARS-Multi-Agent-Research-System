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

    request = AgentRunRequest(
        project=run.project,
        user_request=user_request,
        upstream_artifacts=upstream,
        extra={"debate_progress_path": str(debate_path)},
    )
    context = await agent.build_context(request)

    artifact = await agent.draft(request, context)

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

    # Phase 6: Execution Agent additionally runs the mock simulation batch
    # so the run gets per-experiment run_log_<exp>.v1.md + curves + metrics.
    if node_key == "execution":
        try:
            await _run_execution_batch(run=run, bus=bus)
        except Exception as exc:  # pragma: no cover
            logger.warning("execution batch failed: {}", exc)


async def _run_execution_batch(*, run: RunHandle, bus: Any | None = None) -> None:
    """Trigger 6-way mock simulations using the upstream experiment_plan.

    Reads `experiment_plan.approved.md` (if present) for ablation names;
    otherwise spawns 4 default experiments. Publishes per-experiment WS
    events via the orchestrator's bus (if provided).
    """
    import json

    from app.execution.batch_runner import BatchConfig, run_batch
    from app.execution.curve_parser import write_curve
    from app.execution.metrics_collector import (
        write_metrics_json,
        write_run_log,
    )
    from app.execution.simulation_runner import JobSpec
    from app.harness.schema.frontmatter_parser import parse as parse_fm

    plan_path = run.subdir("experiment") / "experiment_plan.approved.md"
    abl_names: list[str] = []
    if plan_path.exists():
        try:
            md = parse_fm(plan_path.read_text(encoding="utf-8")).metadata
            ablations = md.get("ablations", []) or []
            if isinstance(ablations, list):
                abl_names = [
                    str(a.get("name") or f"ablation_{i}")
                    for i, a in enumerate(ablations)
                    if isinstance(a, dict)
                ]
        except Exception:
            abl_names = []
    if not abl_names:
        abl_names = ["ablation_a", "ablation_b", "ablation_c", "ablation_d"]
    abl_names = abl_names[:6]  # max concurrency cap

    async def _publish(channel: str, payload: dict[str, Any]) -> None:
        if bus is not None:
            await bus.publish(channel, payload)
        run.write_event("websocket_events", {"channel": channel, **payload})

    specs = [
        JobSpec(
            run_id=run.run_id,
            experiment_id=name,
            project=run.project,
            config={"label": name},
            template=("exponential_decay" if i % 2 == 0 else "noisy_decay"),
            seed=hash(name) & 0xFFFFFF,
        )
        for i, name in enumerate(abl_names)
    ]

    outcome = await run_batch(
        specs,
        config=BatchConfig(max_concurrency=6, steps=20),
        bus_publish=_publish,
    )

    for r in outcome.results:
        write_run_log(run_root=run.root, result=r, project=run.project)
        # Persist a fake curve as well — mock_simulation publishes ticks via WS,
        # we don't keep them in memory, so re-derive a synthetic curve.
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


def _metrics_to_curve(result: Any) -> list[float]:
    # Re-derive a deterministic curve from the result's seed so the file
    # matches what the WS stream pushed.
    from app.execution.mock_simulation import _loss_curve

    seed = abs(hash(f"{result.run_id}:{result.experiment_id}")) & 0xFFFFFF
    return _loss_curve(20, template="exponential_decay", seed=seed)


__all__ = ["run_agent_node"]


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
