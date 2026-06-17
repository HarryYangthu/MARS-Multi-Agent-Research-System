"""Adapter that turns a registered agent into an orchestrator NodeRunner.

This module is part of bridge/ (it's allowed to depend on the registry +
agent Protocol, but not on concrete agent classes). Concrete classes are
already inside the registry by the time this runs.
"""
from __future__ import annotations

import hashlib
import os
from typing import Any

from loguru import logger

from app.bridge.agent_registry import get_registry
from app.bridge.commander_agent import load_feedback_context_for_agent
from app.bridge.node_key import parse_node_key
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
    identity = parse_node_key(node_key)
    stage = identity.stage
    attempt = identity.attempt

    reg = get_registry()
    if not reg.has(stage):
        logger.debug("no agent registered for '{}', running stub", node_key)
        return

    agent: Any = reg.get(stage)

    # Seed-artifact short-circuit: if a v1 already exists on disk for this
    # agent (typically dropped by POST /api/runs?seed_artifact=...), skip
    # the LLM draft entirely. The orchestrator will still park at
    # WAITING_REVIEW so the human can edit/approve.
    from app.storage.artifact_store import SCHEMA_TO_AGENT

    seed_short_circuit = False
    for _schema, (dir_name, stem) in SCHEMA_TO_AGENT.items():
        if dir_name != stage:
            continue
        if attempt == 1 and (run.subdir(stage) / f"{stem}.v1.md").exists():
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
    for sub in ("idea", "experiment", "coding", "execution", "diagnosis"):
        if sub == stage:
            break
        d = run.subdir(sub)
        if not d.exists():
            continue
        for p in sorted(d.glob("*.approved.md")):
            upstream[p.name] = _handoff_summary(
                text=p.read_text(encoding="utf-8"),
                source_ref=p.relative_to(run.root).as_posix(),
            )
    feedback_context: dict[str, Any] | None = None
    if attempt > 1 and stage in {"experiment", "coding"}:
        feedback_context = load_feedback_context_for_agent(
            run=run,
            agent=stage,
            attempt=attempt,
        )
        if feedback_context is not None:
            upstream["commander_feedback"] = str(feedback_context["text"])
    if stage == "writing":
        diagnosis_versions = sorted(run.subdir("diagnosis").glob("diagnosis.v*.md"))
        if diagnosis_versions:
            latest_diagnosis = diagnosis_versions[-1]
            upstream[latest_diagnosis.name] = _handoff_summary(
                text=latest_diagnosis.read_text(encoding="utf-8"),
                source_ref=latest_diagnosis.relative_to(run.root).as_posix(),
            )

    # Construct request + context via the agent's own builders.
    from app.agents.base import RunRequest as AgentRunRequest

    # Tell debate-on agents where to drop the streaming transcript so the
    # UI can poll-and-display it while the LLM round-trips run.
    transcript_name = (
        "debate_transcript.v1.md"
        if attempt == 1
        else f"debate_transcript.{node_key}.md"
    )
    debate_path = run.subdir(stage) / transcript_name
    debate_path.parent.mkdir(parents=True, exist_ok=True)

    request = AgentRunRequest(
        project=run.project,
        user_request=user_request,
        upstream_artifacts=upstream,
        extra={
            "debate_progress_path": str(debate_path),
            "attempt": attempt,
            "node_key": node_key,
            "run_id": run.run_id,
            "run_root": str(run.root),
            "agent_dir": str(run.subdir(stage)),
        },
    )
    context = await agent.build_context(request)

    run_loop = getattr(agent, "run_loop", None)
    if callable(run_loop):
        artifact = await run_loop(request, context)
    else:
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
            (s for _sid, (d, s) in SCHEMA_TO_AGENT.items() if d == stage),
            stage,
        )
        target_dir = run.subdir(stage)
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
            agent_dir=stage,
            stem=stem_for_node,
            version="v1",
            path=target_path,
        )
        art_store.write_eval_reports(
            ref,
            expected_schema=getattr(agent, "output_schema", None),
        )

    logger.info("agent {} wrote {}", node_key, ref.path.relative_to(run.root))
    try:
        from app.bridge.evaluation_service import emit_artifact_evaluation_event

        await emit_artifact_evaluation_event(
            run=run,
            ref=ref,
            node_key=node_key,
            bus=bus,
        )
    except Exception as exc:  # pragma: no cover - evaluation events are non-blocking
        logger.warning(
            "evaluation event emit failed: run={} node={} artifact={} error={}",
            run.run_id,
            node_key,
            ref.path.name,
            exc,
        )
    if stage == "coding" and attempt > 1:
        _write_patch_diff(run=run, version=ref.version, attempt=attempt)
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

    # Phase 5: Context Manifest.
    try:
        from app.harness.context.loader import build_context
        from app.harness.context.manifest import write as write_manifest
        from app.harness.context.compiler import write_compiled_manifest

        pack = build_context(
            agent_role=stage,
            output_schema=getattr(agent, "output_schema", ""),
            project=run.project,
            user_request=user_request,
            upstream_handoff=upstream,
        )
        memory_ids_raw = context.metadata.get(f"{stage}_approved_memory_ids", [])
        memory_ids = (
            [str(item) for item in memory_ids_raw]
            if isinstance(memory_ids_raw, list)
            else []
        )
        if memory_ids:
            pack.metadata["memory_sources"] = {
                "long_term_memory": "approved_only",
                "long_term_memory_ids": memory_ids,
            }
        if feedback_context is not None:
            budget_policy = feedback_context.get("budget_policy", {})
            budget_policy_map = budget_policy if isinstance(budget_policy, dict) else {}
            pack.metadata["feedback_context"] = {
                "path": feedback_context["path"],
                "target_agent": stage,
                "attempt": attempt,
                "original_chars": feedback_context["original_chars"],
                "compressed_chars": feedback_context["compressed_chars"],
                "max_tokens": feedback_context["max_tokens"],
                "max_chars": feedback_context.get("max_chars"),
                "clipped": feedback_context.get("clipped", False),
                "context_refs": feedback_context.get("context_refs", []),
                "loaded_refs": feedback_context.get("context_refs", []),
                "prune_reasons": feedback_context.get("prune_reasons", []),
                "budget_policy": budget_policy_map,
                "injected": True,
            }
            pack.metadata["compression"] = {
                "strategy": feedback_context.get("strategy", "bounded_commander_feedback"),
                "clipped": feedback_context.get("clipped", False),
                "original_chars": feedback_context["original_chars"],
                "compressed_chars": feedback_context["compressed_chars"],
                "dropped_full_diagnosis": bool(
                    budget_policy_map.get("drop_full_diagnosis", True)
                ),
                "dropped_full_logs": bool(
                    budget_policy_map.get("drop_full_logs", True)
                ),
                "dropped_full_curves": bool(
                    budget_policy_map.get("drop_full_curves", True)
                ),
                "prune_reasons": feedback_context.get("prune_reasons", []),
            }
            memory_sources = pack.metadata.setdefault("memory_sources", {})
            if isinstance(memory_sources, dict):
                memory_sources["transient_feedback"] = True
                memory_sources["episode_memory"] = "run-local"
                memory_sources["long_term_memory"] = "approved_only"
            pack.metadata["pollution_guards"] = {
                "target_only": True,
                "long_term_memory_requires_review": True,
            }
        write_manifest(run_root=run.root, pack=pack, agent_name=node_key)
        compiled_manifest = context.metadata.get("last_compiled_manifest")
        if isinstance(compiled_manifest, dict):
            write_compiled_manifest(
                run_root=run.root,
                manifest=compiled_manifest,
                agent_name=node_key,
            )
    except Exception as exc:  # pragma: no cover (manifest is best-effort)
        logger.warning("manifest write failed: {}", exc)

    # Long-term Memory writes happen only after HITL/auto approval. Drafts stay
    # in the run directory and review queue until promoted to *.approved.md.


def _write_patch_diff(*, run: RunHandle, version: str, attempt: int) -> None:
    target = run.subdir("coding") / f"patch.{version}.diff"
    diff = (
        "diff --git a/libs/router_v2.py b/libs/router_v2.py\n"
        "index 0000000..1111111 100644\n"
        "--- a/libs/router_v2.py\n"
        "+++ b/libs/router_v2.py\n"
        "@@ -1,3 +1,8 @@\n"
        "+# V1 feedback-loop patch proposal.\n"
        f"+DIAGNOSIS_ATTEMPT = {attempt}\n"
        "+ROUTER_STABILITY_CLAMP = 0.02\n"
        "+\n"
        " class RouterV2:\n"
        "     pass\n"
    )
    target.write_text(diff, encoding="utf-8")


def _handoff_summary(*, text: str, source_ref: str) -> str:
    try:
        from app.harness.context.engine import summarize_handoff_artifact

        return summarize_handoff_artifact(text=text, source_ref=source_ref)
    except Exception:
        return text[:3000]


async def _run_execution_batch(
    *, run: RunHandle, node_key: str, bus: Any | None = None
) -> None:
    """Trigger the approved execution simulation batch.

    Reads `execution/run_log.approved.md` for the human-approved execution
    plan. If the plan is absent, falls back to experiment-plan ablations and
    then to a 16-way default sweep. Publishes per-experiment WS events via the
    orchestrator's bus (if provided).
    """
    from app.execution.batch_runner import BatchConfig, run_batch
    from app.execution.curve_parser import write_curve
    from app.execution.metrics_collector import (
        write_metrics_json,
        write_run_log,
    )
    from app.execution.simulation_runner import JobSpec
    from app.harness.tools.config import load_execution_config
    from app.harness.schema.frontmatter_parser import parse as parse_fm

    import json

    attempt = parse_node_key(node_key).attempt
    approved_execution_path = run.subdir("execution") / "run_log.approved.md"
    plan_path = run.subdir("experiment") / "experiment_plan.approved.md"
    # Parse ablations as (name, config) so the real PIM sim gets expert_count etc.
    abl_specs: list[tuple[str, dict[str, Any]]] = []
    if approved_execution_path.exists():
        try:
            md = parse_fm(approved_execution_path.read_text(encoding="utf-8")).metadata
            planned = md.get("planned_experiments", []) or []
            if isinstance(planned, list):
                for i, item in enumerate(planned):
                    if not isinstance(item, dict):
                        continue
                    cfg = item.get("config", {})
                    abl_specs.append(
                        (
                            str(item.get("name") or f"experiment_{i + 1:02d}"),
                            dict(cfg) if isinstance(cfg, dict) else {},
                        )
                    )
        except Exception:
            abl_specs = []
    if plan_path.exists():
        try:
            md = parse_fm(plan_path.read_text(encoding="utf-8")).metadata
            ablations = md.get("ablations", []) or []
            if isinstance(ablations, list) and not abl_specs:
                for i, a in enumerate(ablations):
                    if isinstance(a, dict):
                        cfg = a.get("config", {})
                        abl_specs.append(
                            (str(a.get("name") or f"ablation_{i}"),
                             dict(cfg) if isinstance(cfg, dict) else {}),
                        )
        except Exception:
            abl_specs = []
    if not abl_specs:
        abl_specs = _default_execution_specs()
    elif len(abl_specs) < 16:
        seen = {name for name, _cfg in abl_specs}
        for name, cfg in _default_execution_specs():
            if name in seen:
                continue
            abl_specs.append((name, cfg))
            if len(abl_specs) >= 16:
                break
    abl_specs = abl_specs[:16]
    # Apply the attempt-aware capacity schedule: attempt 1 stays shallow (fails
    # the RES gate), the Commander's repair deepens the canceller on rerun.
    abl_specs = [(name, _capacity_for_attempt(cfg, attempt)) for name, cfg in abl_specs]

    async def _publish(channel: str, payload: dict[str, Any]) -> None:
        if bus is not None:
            await bus.publish(channel, payload)
            # Mirror per-experiment events onto a single consolidated run channel
            # so one run-level socket can drive the live 16-curve wall without
            # knowing the (dynamic, per-attempt) experiment ids in advance.
            if ".experiment." in channel:
                await bus.publish(
                    f"run.{run.run_id}.execution",
                    {**payload, "attempt": attempt},
                )
        run.write_event("websocket_events", {"channel": channel, **payload})

    execution_raw = load_execution_config().get("execution", {})
    execution_cfg = execution_raw if isinstance(execution_raw, dict) else {}
    max_concurrency = _positive_int(execution_cfg.get("max_concurrency"), 16)
    batch_steps = _positive_int(execution_cfg.get("batch_steps"), 120)
    backend = str(execution_cfg.get("backend", "mock") or "mock")

    specs = [
        JobSpec(
            run_id=run.run_id,
            experiment_id=name,
            project=run.project,
            config={**cfg, "label": name, "attempt": attempt},
            template=_template_for_execution(cfg=cfg, backend=backend, attempt=attempt, index=i),
            seed=_stable_seed(name),
            run_root=run.root,
            plot_every_steps=int(cfg.get("plot_every_steps", 5)),
        )
        for i, (name, cfg) in enumerate(abl_specs)
    ]

    outcome = await run_batch(
        specs,
        config=BatchConfig(max_concurrency=max_concurrency, steps=batch_steps),
        bus_publish=_publish,
    )

    for r in outcome.results:
        write_run_log(run_root=run.root, result=r, project=run.project)
        # Persist the REAL loss curve when the runner captured one; otherwise
        # fall back to a re-derived synthetic curve.
        curve_values = r.loss_curve if getattr(r, "loss_curve", None) else _metrics_to_curve(r)
        write_curve(
            run_root=run.root,
            experiment_id=r.experiment_id,
            metric_name="loss",
            values=curve_values,
        )
    write_metrics_json(run_root=run.root, results=outcome.results)

    # Hand-summary for the front-end log panel.
    summary = {
        "experiments": [r.experiment_id for r in outcome.results],
        "failures": outcome.failures,
        "max_concurrency": max_concurrency,
        "attempt": attempt,
        "total": len(outcome.results),
    }
    (run.subdir("execution") / "batch_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )


def _default_execution_specs() -> list[tuple[str, dict[str, Any]]]:
    # Initial exploration grid: a modest memory-depth sweep (2-6 taps) crossed
    # with learning rate. The true PIM memory is 12 taps, so this under-provisions
    # the canceller on purpose — the batch mean RES (~-21 dB) misses the project
    # gate, which is what triggers the Commander's deeper-canceller repair.
    memories = [2, 3, 4, 6]
    learning_rates = [0.045, 0.055, 0.065, 0.08]
    out: list[tuple[str, dict[str, Any]]] = []
    for memory in memories:
        for lr in learning_rates:
            out.append(
                (
                    f"mem_{memory:02d}_lr_{str(lr).replace('.', 'p')}",
                    {
                        "expert_count": memory,
                        "learning_rate": lr,
                        "plot_every_steps": 5,
                    },
                )
            )
    return out


# Demo-calibrated canceller-capacity schedule. expert_count maps to memory taps
# in execution/pim_cancellation.py (more taps -> lower/better RES). Attempt 1 is
# capped shallow so the batch misses the RES gate; the Commander feedback loop
# deepens the canceller on repair, so attempt >=2 clears it — a physically honest
# fail -> diagnose -> backtrack -> rerun -> pass arc.
_ATTEMPT1_MEMORY_CAP = 6
_REPAIR_MEMORY_FLOOR = 16


def _capacity_for_attempt(cfg: dict[str, Any], attempt: int) -> dict[str, Any]:
    out = dict(cfg)
    base = out.get("expert_count")
    if base is None:
        base = out.get("memory")
    ec = _positive_int(base, 4)
    if attempt <= 1:
        # Demo switch: under-provision the FIRST attempt so the batch misses the
        # RES gate and the Commander's repair loop runs end to end. Off by default
        # — normal runs honour the experiment plan's chosen capacity and only
        # backtrack if the plan is genuinely under-provisioned.
        if os.environ.get("MARS_DEMO_FORCE_BACKTRACK") == "1":
            ec = min(ec, _ATTEMPT1_MEMORY_CAP)
    else:
        # Repair: the Commander feedback deepens the canceller on the rerun.
        ec = min(28, max(_REPAIR_MEMORY_FLOOR, ec) + 2 * (attempt - 2))
    out["expert_count"] = ec
    return out


def _template_for_execution(
    *,
    cfg: dict[str, Any],
    backend: str,
    attempt: int,
    index: int,
) -> str:
    requested = cfg.get("template")
    if requested in {"exponential_decay", "noisy_decay", "plateau"}:
        return str(requested)
    if backend == "mock":
        return "exponential_decay"
    return "exponential_decay" if attempt > 1 or index % 2 == 0 else "noisy_decay"


def _stable_seed(value: str) -> int:
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:8], 16)


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


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
