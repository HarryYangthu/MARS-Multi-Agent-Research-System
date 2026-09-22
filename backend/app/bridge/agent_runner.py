"""Adapter that turns a registered agent into an orchestrator NodeRunner.

This module is part of bridge/ (it's allowed to depend on the registry +
agent Protocol, but not on concrete agent classes). Concrete classes are
already inside the registry by the time this runs.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

from loguru import logger

from app.bridge.agent_registry import AgentRegistry, get_registry
from app.bridge.agent_progress import build_agent_progress_sink
from app.bridge.commander_agent import load_feedback_context_for_agent
from app.bridge.node_key import parse_node_key
from app.bridge.task_runtime import admit_handoffs, bind_task
from app.harness.agent_loop.trace import atomic_json
from app.harness.runtime.task_contract import FailureEnvelope, ResultEnvelope
from app.harness.execution_intent import (
    requested_experiment_count,
    wants_execution_sweep,
)
from app.harness.llm.provider_base import LLMCompletionError
from app.harness.schema.frontmatter_parser import parse as parse_fm
from app.settings import get_settings
from app.storage.data_source_store import selection_summary
from app.storage.artifact_store import ArtifactStore, ArtifactValidationError
from app.storage.run_store import RunHandle


async def run_agent_node(
    run: RunHandle,
    node_key: str,
    *,
    bus: Any | None = None,
    revision_reason: str = "",
    registry: AgentRegistry | None = None,
    resume_invocation: str | None = None,
    predecessor_task_ids: list[str] | None = None,
) -> None:
    """Default NodeRunner: look the agent up by key, draft, validate, persist.

    A missing agent is an explicit configuration failure.
    """
    identity = parse_node_key(node_key)
    stage = identity.stage
    attempt = identity.attempt

    reg = registry if registry is not None else get_registry()
    if not reg.has(stage):
        raise RuntimeError(f"no agent registered for {stage!r}; node was not executed")

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
        if (
            attempt == 1
            and not revision_reason
            and resume_invocation is None
            and (run.subdir(stage) / f"{stem}.v1.md").exists()
        ):
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

    upstream, feedback_context = load_agent_handoff_context(run, node_key, revision_reason=revision_reason, registry=reg)
    admit_handoffs(run, node_key, supplied_context=upstream)
    if revision_reason:
        run.write_event(
            "agent_events",
            {
                "event": "agent.revision_started",
                "agent": stage,
                "node": node_key,
                "reason": revision_reason,
            },
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

    request_extra = _load_run_request_extra(run)
    skill_selection = request_extra.get("selected_skills_by_agent", {})
    if not isinstance(skill_selection, dict):
        raise ValueError("selected_skills_by_agent must be an object")
    selected_skills = skill_selection.get(stage, [])
    if not isinstance(selected_skills, list) or any(not isinstance(name, str) for name in selected_skills):
        raise ValueError("selected skills must be explicit names")
    request_extra["skills"] = selected_skills
    task = bind_task(run, node_key, goal=user_request, upstream=upstream,
                    output_schema=str(agent.output_schema), resume_invocation=resume_invocation,
                    predecessor_task_ids=predecessor_task_ids)
    request_extra.update(task.model_dump(include={"task_id", "parent_task_id", "node_id", "invocation_id", "parent_invocation_id"}))
    request_extra["trace_id"] = run.run_id
    request_extra["task_contract"] = task.model_dump()
    if resume_invocation is not None:
        request_extra["resume_invocation"] = resume_invocation
    request_extra.update(
        {
            "debate_progress_path": str(debate_path),
            "attempt": attempt,
            "node_key": node_key,
            "run_id": run.run_id,
            "run_root": str(run.root),
            "agent_dir": str(run.subdir(stage)),
            "revision_reason": revision_reason,
            "required_upstream_refs": list(upstream),
        }
    )
    request = AgentRunRequest(
        project=run.project,
        user_request=user_request,
        upstream_artifacts=upstream,
        extra=request_extra,
        progress_sink=build_agent_progress_sink(run=run, node_key=node_key, bus=bus),
    )
    failure_phase = "build_context"
    try:
        context = await agent.build_context(request)

        failure_phase = "draft"
        run_loop = getattr(agent, "run_loop", None)
        if callable(run_loop):
            artifact = await run_loop(request, context)
        else:
            artifact = await agent.draft(request, context)
    except Exception as exc:
        failure = FailureEnvelope(task_id=task.task_id, invocation_id=task.invocation_id,
            code="agent_execution_failed", message=str(exc) or type(exc).__name__, outcome_known=False,
            evidence_refs=[f"agent_traces/{stage}/{task.invocation_id}"])
        atomic_json(run.root / "input/task_results" / (task.invocation_id + ".json"),
                    ResultEnvelope(task_id=task.task_id, invocation_id=task.invocation_id,
                                   status="failed", failure=failure).model_dump())
        _write_agent_failure_diagnostic(
            run=run,
            node_key=node_key,
            agent=stage,
            phase=failure_phase,
            exc=exc,
        )
        raise

    # Invalid output is evidence of failure, never a successful artifact version.
    validation = await agent.validate_output(artifact)
    art_store = ArtifactStore(run)

    if not validation.valid:
        target = run.subdir(stage) / "invalid_outputs" / (task.invocation_id + ".md")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as handle:
            handle.write(artifact.text)
        failure = FailureEnvelope(task_id=task.task_id, invocation_id=task.invocation_id,
            code="output_schema_invalid", message=str(validation.first_error()),
            evidence_refs=[target.relative_to(run.root).as_posix()])
        atomic_json(run.root / "input/task_results" / (task.invocation_id + ".json"),
            ResultEnvelope(task_id=task.task_id, invocation_id=task.invocation_id, status="invalid",
                           failure=failure).model_dump())
        raise ArtifactValidationError(validation)
    ref = art_store.write(text=artifact.text, expected_schema=str(agent.output_schema))
    atomic_json(run.root / "input/task_results" / (task.invocation_id + ".json"),
        ResultEnvelope(task_id=task.task_id, invocation_id=task.invocation_id, status="awaiting_review",
            artifact_ref=ref.path.relative_to(run.root).as_posix(), schema_valid=True,
            artifact_sha256=hashlib.sha256(ref.path.read_bytes()).hexdigest()).model_dump())

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
    if stage == "coding":
        _write_patch_diff(run=run, version=ref.version, artifact_text=artifact.text)
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

    # Preserve the manifest from the actual model context; never reload live
    # knowledge after execution and mislabel that reconstruction as consumed.
    try:
        from app.harness.context.compiler import write_compiled_manifest
        compiled_manifest = context.metadata.get("last_compiled_manifest")
        if isinstance(compiled_manifest, dict):
            write_compiled_manifest(run_root=run.root, manifest=compiled_manifest, agent_name=node_key)
        atomic_json(run.root / "context" / (task.invocation_id + ".source_receipt.json"), {
            "schema_id": "context.consumed_sources.v1", "invocation_id": task.invocation_id,
            "task_id": task.task_id, "source": "actual_agent_context_metadata",
            "compiled_manifest_available": isinstance(compiled_manifest, dict),
            "required_upstream_refs": context.metadata.get("required_upstream_refs", []),
            "feedback_context": feedback_context,
            "sources": {key: value for key, value in context.metadata.items()
                        if "skill" in key or "memory" in key or key in {"project_knowledge", "folder_context"}},
        })
    except Exception as exc:
        logger.warning("actual context manifest persistence failed: {}", type(exc).__name__)

    if stage == "idea":
        try:
            write_acceptance_report = getattr(agent, "write_acceptance_report", None)
            if not callable(write_acceptance_report):
                return

            report_path = write_acceptance_report(
                run=run,
                artifact_ref=ref,
                node_key=node_key,
            )
            run.write_event(
                "agent_events",
                {
                    "event": "idea.acceptance_report_written",
                    "agent": stage,
                    "node": node_key,
                    "path": report_path.relative_to(run.root).as_posix(),
                },
            )
        except Exception as exc:  # pragma: no cover (acceptance report is audit-only)
            logger.warning(
                "idea acceptance report write failed: run={} node={} error={}",
                run.run_id,
                node_key,
                exc,
            )

    # Long-term Memory writes happen only after HITL/auto approval. Drafts stay
    # in the run directory and review queue until promoted to *.approved.md.


def _write_agent_failure_diagnostic(
    *,
    run: RunHandle,
    node_key: str,
    agent: str,
    phase: str,
    exc: Exception,
) -> None:
    """Persist a content-free, machine-readable failure before propagation."""

    diagnostic: dict[str, Any] = {
        "code": "agent_stage_failed",
        "exception_type": type(exc).__name__,
    }
    reason: Mapping[str, object] | None = None
    raw_reason = getattr(exc, "reason", None)
    if isinstance(exc, LLMCompletionError):
        reason = exc.reason
    elif isinstance(raw_reason, Mapping):
        reason = raw_reason
    if reason is not None:
        allowed_keys = (
            "code",
            "role",
            "provider",
            "model",
            "finish_reason",
            "empty_final",
        )
        details = {key: reason[key] for key in allowed_keys if key in reason}
        diagnostic["code"] = str(details.get("code") or diagnostic["code"])
        diagnostic["details"] = details
    run.write_event(
        "agent_events",
        {
            "event": "agent.node_failed",
            "agent": agent,
            "node": node_key,
            "phase": phase,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "diagnostic": diagnostic,
        },
    )


def _write_patch_diff(*, run: RunHandle, version: str, artifact_text: str) -> None:
    target = run.subdir("coding") / f"patch.{version}.diff"
    blocks = re.findall(r"^```(?:diff|patch)\s*\n(.*?)^```\s*$", artifact_text, re.MULTILINE | re.DOTALL)
    if not blocks:
        run.write_event("agent_events", {"event": "coding.patch_not_provided", "version": version})
        return
    # Copy the actual proposal for review; application still requires ToolRegistry.
    target.write_text("\n".join(block.rstrip() for block in blocks) + "\n", encoding="utf-8")


def load_agent_handoff_context(
    run: RunHandle, node_key: str, *, revision_reason: str = "", registry: AgentRegistry | None = None,
) -> tuple[dict[str, str], dict[str, Any] | None]:
    """Load actual approved upstream documents without silently truncating them."""
    identity = parse_node_key(node_key)
    stage, attempt = identity.stage, identity.attempt
    # Pick up upstream approved artifacts as handoff.
    from app.bridge.research_context import load_research_context

    extras = _load_run_request_extra(run, strict=True)
    upstream = load_research_context(run, extras, allow_legacy=stage == "idea")
    execution_context = extras.get("execution_context", {})
    if not isinstance(execution_context, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                                    or not v.strip() for k, v in execution_context.items()):
        raise ValueError("execution_context must contain named nonempty text")
    upstream.update(execution_context)
    selected_data_source = _load_selected_data_source(run)
    if selected_data_source:
        upstream["input.selected_data_source"] = selection_summary(selected_data_source)
    for sub in ("idea", "experiment", "coding", "execution", "diagnosis"):
        if sub == stage:
            break
        d = run.subdir(sub)
        if not d.exists():
            continue
        for p in sorted(d.glob("*.approved.md")):
            text = p.read_text(encoding="utf-8")
            upstream[p.name] = _handoff_summary(
                text=text,
                source_ref=p.relative_to(run.root).as_posix(),
            )
            if sub == "idea" and "research_assessment" in parse_fm(text).metadata:
                idea_registry = registry if registry is not None else get_registry()
                if not idea_registry.has("idea"):
                    raise ValueError("Idea research handoff requires a registered Idea agent")
                loader = getattr(idea_registry.get("idea"), "load_approved_research_context", None)
                if not callable(loader):
                    raise ValueError("registered Idea agent does not support verified research handoff")
                evidence = loader(run_root=run.root, proposal_text=text, project=run.project)
                if not isinstance(evidence, dict):
                    raise ValueError("Idea research handoff did not return verified evidence")
                upstream[p.name + ".research_evidence"] = json.dumps(evidence, ensure_ascii=False)
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
        upstream.update(_execution_result_handoffs(run))
        diagnosis_versions = sorted(run.subdir("diagnosis").glob("diagnosis.v*.md"))
        if diagnosis_versions:
            latest_diagnosis = diagnosis_versions[-1]
            upstream[latest_diagnosis.name] = _handoff_summary(
                text=latest_diagnosis.read_text(encoding="utf-8"),
                source_ref=latest_diagnosis.relative_to(run.root).as_posix(),
            )
    if revision_reason:
        if stage == "idea":
            versions = [ref for ref in ArtifactStore(run).list_versions(agent_dir="idea", stem="idea_proposal")
                        if ref.version.startswith("v")]
            if versions:
                current = versions[-1]
                upstream["revision_candidate"] = (
                    "Current model-generated draft for targeted revision; not an approved conclusion. "
                    "Preserve unaffected content, verify the specific feedback against actual evidence, "
                    "and avoid restarting broad research without an identified gap.\n"
                    + _handoff_summary(text=current.path.read_text(), source_ref=current.path.relative_to(run.root).as_posix()))
        upstream["human_revision_request"] = (
            "Human reviewer rejected the current draft and requested a revised "
            f"version. Feedback: {revision_reason}"
        )
    return upstream, feedback_context


def _handoff_summary(*, text: str, source_ref: str) -> str:
    # Compression belongs to the loop packer, which records a manifest.
    # A bridge exception must never silently discard an approved contract.
    return f"[upstream artifact: {source_ref}]\n{text}"



def _execution_result_handoffs(run: RunHandle) -> dict[str, str]:
    """Summarize actual execution outputs for the Writing Agent.

    ``run_log.approved.md`` is the human-approved execution plan, not the
    measured result. Writing must also see the post-run JSON evidence so it
    cannot accidentally report "not yet executed" after a batch has finished.
    """
    execution_dir = run.subdir("execution")
    out: dict[str, str] = {}

    metrics_path = execution_dir / "metrics.json"
    if metrics_path.exists():
        try:
            rows_raw = json.loads(metrics_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            rows_raw = []
        rows = rows_raw if isinstance(rows_raw, list) else []
        out["execution.metrics.json"] = _summarize_execution_metrics(
            rows=rows,
            source_ref=metrics_path.relative_to(run.root).as_posix(),
        )

    batch_path = execution_dir / "batch_summary.json"
    if batch_path.exists():
        try:
            batch_raw = json.loads(batch_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            batch_raw = {}
        batch = batch_raw if isinstance(batch_raw, dict) else {}
        out["execution.batch_summary.json"] = _summarize_execution_batch(
            batch=batch,
            source_ref=batch_path.relative_to(run.root).as_posix(),
        )

    curves_path = execution_dir / "loss_curves_16.png"
    if curves_path.exists():
        out["execution.loss_curves_16.png"] = (
            "# Actual execution plot\n"
            f"source: {curves_path.relative_to(run.root).as_posix()}\n"
            "This PNG is the generated loss curve panel from the completed "
            "Execution Agent batch. Cite it as visual evidence when discussing convergence."
        )
    return out


def _load_selected_data_source(run: RunHandle) -> dict[str, Any]:
    path = run.subdir("input") / "selected_data_source.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return raw if isinstance(raw, dict) else {}


def _load_run_request_extra(run: RunHandle, *, strict: bool = False) -> dict[str, Any]:
    path = run.subdir("input") / "run_request_options.v1.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if strict:
            raise ValueError("Idea run request options are unreadable") from exc
        logger.warning("run request options are unreadable: {}", path)
        return {}
    if not isinstance(raw, dict) or raw.get("schema_id") != "run_request_options.v1":
        if strict:
            raise ValueError("Idea run request options use an unsupported schema")
        logger.warning("run request options use an unsupported schema: {}", path)
        return {}
    extra = raw.get("extra")
    if strict and not isinstance(extra, dict):
        raise ValueError("Idea run request extra must be an object")
    return {str(key): value for key, value in extra.items()} if isinstance(extra, dict) else {}


def _summarize_execution_batch(*, batch: dict[str, Any], source_ref: str) -> str:
    experiments = batch.get("experiments")
    failures = batch.get("failures")
    return (
        "# Actual execution batch summary\n"
        f"source: {source_ref}\n"
        "This is post-run evidence, not the execution plan.\n"
        f"- attempt: {batch.get('attempt', 'unknown')}\n"
        f"- total: {batch.get('total', 'unknown')}\n"
        f"- max_concurrency: {batch.get('max_concurrency', 'unknown')}\n"
        f"- plan_source: {batch.get('plan_source', 'unknown')}\n"
        f"- intent_experiment_count: {batch.get('intent_experiment_count', 'not explicit')}\n"
        f"- planned_before_intent_count: {batch.get('planned_before_intent_count', 'unknown')}\n"
        f"- failures: {len(failures) if isinstance(failures, list) else 'unknown'}\n"
        f"- experiments: {', '.join(str(item) for item in experiments[:16]) if isinstance(experiments, list) else '(unavailable)'}"
    )


def _summarize_execution_metrics(*, rows: list[Any], source_ref: str) -> str:
    metric_rows: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        metrics = item.get("metrics")
        if not isinstance(metrics, dict):
            continue
        metric_rows.append(
            {
                "run_id": item.get("run_id", ""),
                "metrics": metrics,
                "duration_seconds": item.get("duration_seconds"),
            }
        )
    numeric: dict[str, list[float]] = {}
    for item in metric_rows:
        metrics = item["metrics"]
        if not isinstance(metrics, dict):
            continue
        for key, value in metrics.items():
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            numeric.setdefault(str(key), []).append(number)

    lines = [
        "# Actual execution metrics",
        f"source: {source_ref}",
        "This is measured post-run evidence. Do not describe the batch as unexecuted if this section is present.",
        f"- rows: {len(metric_rows)}",
    ]
    for key in sorted(numeric):
        values = numeric[key]
        if not values:
            continue
        mean = sum(values) / len(values)
        lines.append(
            f"- {key}: min={min(values):.6g}, max={max(values):.6g}, mean={mean:.6g}"
        )

    best_res = _best_metric_row(metric_rows, metric="RES", lower_is_better=True)
    if best_res is not None:
        lines.append(
            "- best_RES: run_id={run_id}, RES={res}, loss={loss}, PIM={pim}, APE={ape}".format(
                run_id=best_res.get("run_id", ""),
                res=_metric_value(best_res, "RES"),
                loss=_metric_value(best_res, "loss"),
                pim=_metric_value(best_res, "PIM"),
                ape=_metric_value(best_res, "APE"),
            )
        )
    top_rows = sorted(
        metric_rows,
        key=lambda item: float(_metric_value(item, "RES", default=999999.0)),
    )[:5]
    if top_rows:
        lines.append("## Top rows by lower RES")
        for item in top_rows:
            lines.append(
                "- {run_id}: RES={res}, loss={loss}, PIM={pim}, APE={ape}".format(
                    run_id=item.get("run_id", ""),
                    res=_metric_value(item, "RES"),
                    loss=_metric_value(item, "loss"),
                    pim=_metric_value(item, "PIM"),
                    ape=_metric_value(item, "APE"),
                )
            )
    return "\n".join(lines)


def _best_metric_row(
    rows: list[dict[str, Any]],
    *,
    metric: str,
    lower_is_better: bool,
) -> dict[str, Any] | None:
    candidates = [
        item
        for item in rows
        if _metric_value(item, metric, default=None) is not None
    ]
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda item: float(_metric_value(item, metric, default=0.0)),
        reverse=not lower_is_better,
    )[0]


def _metric_value(
    row: dict[str, Any],
    metric: str,
    *,
    default: Any = "n/a",
) -> Any:
    metrics = row.get("metrics")
    if not isinstance(metrics, dict):
        return default
    return metrics.get(metric, default)


def _execution_intent_text(run: RunHandle) -> str:
    parts = [run.task]
    raw_request = run.meta.get("user_request")
    if isinstance(raw_request, str) and raw_request.strip():
        parts.append(raw_request)
    request_path = run.subdir("input") / "user_request.md"
    if request_path.exists():
        parts.append(request_path.read_text(encoding="utf-8"))
    return "\n\n".join(part for part in parts if part.strip())


def _planned_seed(name: str, config: dict[str, Any]) -> int:
    seed = config.get("seed")
    if seed is None:
        return _stable_seed(name)
    if type(seed) is not int or seed < 0:
        raise ValueError("approved experiment seed must be a nonnegative integer")
    return seed


async def _run_execution_batch(
    *, run: RunHandle, node_key: str, bus: Any | None = None
) -> None:
    """Trigger the approved execution simulation batch.

    Reads `execution/run_log.approved.md` for the human-approved execution
    plan. If the plan is absent, reads experiment-plan ablations; missing plans
    fail closed. Publishes per-experiment WS events via the
    orchestrator's bus when provided.
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
    intent_text = _execution_intent_text(run)
    intent_count = requested_experiment_count(intent_text)
    intent_wants_sweep = wants_execution_sweep(intent_text)
    plan_source = "none"
    # Parse ablations as (name, config) so the execution backend gets supported knobs.
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
            if abl_specs:
                plan_source = "execution_run_log"
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
                if abl_specs:
                    plan_source = "experiment_plan"
        except Exception:
            abl_specs = []
    planned_before_intent = len(abl_specs)
    if not abl_specs:
        raise RuntimeError("no valid approved experiment configurations; execution was not started")
    elif intent_count is not None and len(abl_specs) > intent_count:
        abl_specs = abl_specs[:intent_count]
        plan_source = f"{plan_source}_intent_capped"
    selected_data_source = _load_selected_data_source(run)
    if selected_data_source:
        data_path = str(selected_data_source.get("stored_path") or "")
        data_source_id = str(selected_data_source.get("id") or "")
        fs_mhz = selected_data_source.get("fs_mhz")
        channel_count = selected_data_source.get("channel_count")
        injected_specs: list[tuple[str, dict[str, Any]]] = []
        for name, cfg in abl_specs:
            next_cfg = dict(cfg)
            if data_path:
                next_cfg["data_path"] = data_path
            if data_source_id:
                next_cfg["data_source_id"] = data_source_id
            if fs_mhz not in (None, ""):
                next_cfg["fs_mhz"] = fs_mhz
            if channel_count not in (None, ""):
                next_cfg["channel_count"] = channel_count
            injected_specs.append((name, next_cfg))
        abl_specs = injected_specs
    execution_raw = load_execution_config().get("execution", {})
    execution_cfg = execution_raw if isinstance(execution_raw, dict) else {}
    backend = str(execution_cfg.get("backend", "local_command") or "local_command")
    runtime_backend = get_settings().mars_execution_backend
    async def _publish(channel: str, payload: dict[str, Any]) -> None:
        if bus is not None:
            await bus.publish(channel, payload)
            # Mirror per-experiment events onto a single consolidated run channel
            # so one run-level socket can drive the live curve wall without
            # knowing the (dynamic, per-attempt) experiment ids in advance.
            if ".experiment." in channel:
                await bus.publish(
                    f"run.{run.run_id}.execution",
                    {**payload, "attempt": attempt},
                )
        run.write_event("websocket_events", {"channel": channel, **payload})

    configured_max_concurrency = _positive_int(execution_cfg.get("max_concurrency"), 16)
    max_concurrency = configured_max_concurrency
    batch_steps = _positive_int(execution_cfg.get("batch_steps"), 120)
    if backend == "paper_static":
        paper_cfg_raw = execution_cfg.get("paper_static", {})
        paper_cfg = paper_cfg_raw if isinstance(paper_cfg_raw, dict) else {}
        max_concurrency = min(
            max_concurrency,
            _positive_int(paper_cfg.get("max_concurrency"), 1),
        )
        batch_steps = _positive_int(paper_cfg.get("default_max_iters"), 1)
        experiment_limit = _positive_int(paper_cfg.get("batch_experiments"), 1)
        abl_specs = abl_specs[:experiment_limit]
    max_concurrency = min(max_concurrency, max(1, len(abl_specs)))

    specs = [
        JobSpec(
            run_id=run.run_id,
            experiment_id=name,
            project=run.project,
            config={**cfg, "label": name, "attempt": attempt},
            seed=_planned_seed(name, cfg),
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
        # Only persist measured curve values; absence is not synthetic evidence.
        curve_values = r.loss_curve if getattr(r, "loss_curve", None) else []
        write_curve(
            run_root=run.root,
            experiment_id=r.experiment_id,
            metric_name="loss",
            values=curve_values,
        )
    if any(result.loss_curve for result in outcome.results):
        from app.execution.pim_cancellation import plot_loss_curves

        batch_plot_curves = {
            r.experiment_id: (
                [float(v) for v in r.loss_curve]
                if getattr(r, "loss_curve", None)
                else []
            )
            for r in outcome.results
        }
        plot_loss_curves(
            batch_plot_curves,
            run.subdir("execution") / "loss_curves_16.png",
            title=f"{len(batch_plot_curves)}-experiment measured loss",
        )
    write_metrics_json(run_root=run.root, results=outcome.results)

    # Hand-summary for the front-end log panel.
    summary = {
        "experiments": [r.experiment_id for r in outcome.results],
        "failures": outcome.failures,
        "max_concurrency": max_concurrency,
        "configured_max_concurrency": configured_max_concurrency,
        "attempt": attempt,
        "total": len(outcome.results),
        "plan_source": plan_source,
        "planned_before_intent_count": planned_before_intent,
        "intent_experiment_count": intent_count,
        "intent_requested_sweep": intent_wants_sweep,
        "configured_backend": backend,
        "runtime_backend": runtime_backend,
        "data_source": selected_data_source or {},
        "data_source_passed_to_backend": bool(selected_data_source),
        # Local commands receive the selected path, but generic dispatch cannot
        # attest consumption; the command's own measurement evidence must do so.
        "data_source_consumed_by_backend": True if selected_data_source and runtime_backend == "paper_static" else None,
    }
    (run.subdir("execution") / "batch_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    if outcome.failures or len(outcome.results) != len(specs):
        raise RuntimeError("actual execution batch failed; inspect execution/batch_summary.json")


def _representative_execution_spec() -> tuple[str, dict[str, Any]]:
    return (
        "mem_16_lr_0p065",
        {
            "expert_count": 16,
            "learning_rate": 0.065,
            "plot_every_steps": 5,
        },
    )


def _stable_seed(value: str) -> int:
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:8], 16)


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


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
