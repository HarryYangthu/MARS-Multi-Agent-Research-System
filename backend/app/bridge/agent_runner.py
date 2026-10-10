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
import math
import re
from statistics import mean
from typing import Any

from loguru import logger

from app.bridge.agent_registry import AgentRegistry, get_registry
from app.bridge.agent_progress import build_agent_progress_sink
from app.bridge.commander_agent import load_feedback_context_for_agent
from app.bridge.node_key import parse_node_key
from app.bridge.task_runtime import admit_handoffs, bind_task
from app.bridge.research_run_service import load_run_research_contract
from app.bridge.research_stage_runtime import (
    BoundResearchStage, load_bound_research_stage, record_research_stage_result, research_stage_dispatch,
)
from app.harness.agent_loop.trace import atomic_json
from app.harness.runtime.task_contract import FailureEnvelope, ResultEnvelope
from app.harness.execution_intent import (
    requested_experiment_count,
    wants_execution_sweep,
)
from app.harness.llm.provider_base import LLMCompletionError
from app.harness.llm.accounting import RunModelBudget
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
    """Use the same runner, with sealed SQL ownership for contract-backed work."""
    from app.harness.runtime.state_journal import StateJournal
    authority = StateJournal.from_authority(run.root, run_id=run.run_id)
    saved_request = authority.read().get("request", {}) if authority is not None else {}
    saved_extra = saved_request.get("extra", {}) if isinstance(saved_request, dict) else {}
    if not isinstance(saved_extra, dict):
        raise ValueError("Agent request authority has invalid options")
    if load_run_research_contract(run, saved_extra if authority is not None else None) is None:
        from app.bridge.research_branch import research_branch_scope
        with research_branch_scope(run, node_key):
            await _execute_agent_node(run, node_key, bus=bus, revision_reason=revision_reason, registry=registry,
                resume_invocation=resume_invocation, predecessor_task_ids=predecessor_task_ids)
        return
    if revision_reason:
        raise ValueError("Contract revisions require a new bound graph attempt; legacy revision dispatch is unavailable")
    selected = registry if registry is not None else get_registry()
    stage = load_bound_research_stage(run, node_key, agent=selected.get(parse_node_key(node_key).stage))
    if predecessor_task_ids is not None and predecessor_task_ids != stage.task.predecessor_task_ids:
        raise ValueError("Caller dependencies differ from the authoritative stage")
    with research_stage_dispatch(stage, resume_invocation=resume_invocation):
        await _execute_agent_node(run, node_key, bus=bus, registry=selected, research_stage=stage)


async def _execute_agent_node(
    run: RunHandle,
    node_key: str,
    *,
    bus: Any | None = None,
    revision_reason: str = "",
    registry: AgentRegistry | None = None,
    resume_invocation: str | None = None,
    predecessor_task_ids: list[str] | None = None,
    research_stage: BoundResearchStage | None = None,
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
            research_stage is None
            and attempt == 1
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
    user_request = research_stage.task.goal if research_stage is not None else ""
    user_request_path = run.subdir("input") / "user_request.md"
    if research_stage is None and user_request_path.exists():
        user_request = user_request_path.read_text(encoding="utf-8")

    if research_stage is None:
        if resume_invocation is not None and not revision_reason:
            from app.bridge.revision_resume import resume_revision_reason
            revision_reason = resume_revision_reason(run, node_key, resume_invocation)
        upstream, feedback_context = load_agent_handoff_context(run, node_key, revision_reason=revision_reason, registry=reg)
        from app.harness.tools.git_branch import current_git_branch
        branch = current_git_branch(run.project, run.run_id)
        if branch is not None:
            upstream["research_git_branch"] = (
                f"实际代码目录：{branch.repo_path}\n当前实验分支：{branch.branch}\n"
                f"基线分支：{branch.baseline_branch}\n基线提交：{branch.baseline_commit}\n"
                "在现有目录的此实验分支中修改；不得切换分支、修改基线分支或 Git 控制文件。"
                "受保护路径、接口和允许修改范围继续适用。"
            )
        admit_handoffs(run, node_key, supplied_context=upstream)
    else:
        upstream, feedback_context = dict(research_stage.upstream), {}
        from app.bridge.research_stage_runtime import validate_bound_handoffs
        validate_bound_handoffs(research_stage)
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

    # Mutable legacy request options cannot override the sealed context, choose
    # skills, or change model/execution behavior for contract-backed stages.
    request_extra = _load_run_request_extra(run) if research_stage is None else {}
    skill_selection = request_extra.get("selected_skills_by_agent", {})
    if not isinstance(skill_selection, dict):
        raise ValueError("selected_skills_by_agent must be an object")
    selected_skills = skill_selection.get(stage, [])
    if research_stage is None and stage == "writing" and stage not in skill_selection:
        from app.storage.report_skill_store import selected_report_skills
        selected_skills = selected_report_skills(run.project)
    if not isinstance(selected_skills, list) or any(not isinstance(name, str) for name in selected_skills):
        raise ValueError("selected skills must be explicit names")
    if stage == "writing":
        if research_stage is not None:
            selected_skills = list(research_stage.skills)
        else:
            from app.bridge.report_skill_binding import frozen_report_skills
            selected_skills = frozen_report_skills(run, node_key, selected_skills, create=True)
    request_extra["skills"] = selected_skills
    task = research_stage.task if research_stage is not None else bind_task(run, node_key, goal=user_request, upstream=upstream,
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
    if stage == "coding":
        from app.bridge.coding_approval import coding_candidate_errors
        request.candidate_validator = lambda text: coding_candidate_errors(run, text)
    if research_stage is None and branch is not None and request.progress_sink is not None:
        await request.progress_sink({"kind": "action", "phase": "workspace",
            "message": f"使用实验分支 {branch.branch}，原分支 {branch.baseline_branch} 保留。"})
    failure_phase = "build_context"
    try:
        context = await agent.build_context(request)

        if revision_reason and resume_invocation is None and getattr(agent, "requires_model", True):
            failure_phase = "resource_revision"
            RunModelBudget(run.root).begin_revision(invocation_id=task.invocation_id, reason=revision_reason)

        failure_phase = "draft"
        run_loop = getattr(agent, "run_loop", None)
        if callable(run_loop):
            artifact = await run_loop(request, context)
        else:
            artifact = await agent.draft(request, context)
    except Exception as exc:
        failure = FailureEnvelope(task_id=task.task_id, invocation_id=task.invocation_id,
            code="agent_execution_failed", message=("Contract Agent execution failed; inspect its native receipts"
                if research_stage is not None else str(exc) or type(exc).__name__), outcome_known=False,
            evidence_refs=[f"agent_traces/{stage}/{task.invocation_id}"])
        _save_stage_result(run, research_stage, ResultEnvelope(task_id=task.task_id, invocation_id=task.invocation_id,
                                   status="failed", failure=failure))
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
        _save_stage_result(run, research_stage, ResultEnvelope(task_id=task.task_id, invocation_id=task.invocation_id,
                           status="invalid", failure=failure))
        raise ArtifactValidationError(validation)
    if stage == "coding":
        issues = coding_candidate_errors(run, artifact.text)
        if issues:
            raise ValueError(issues[0])
    ref = art_store.write(text=artifact.text, expected_schema=str(agent.output_schema))
    _save_stage_result(run, research_stage, ResultEnvelope(task_id=task.task_id, invocation_id=task.invocation_id, status="awaiting_review",
            artifact_ref=ref.path.relative_to(run.root).as_posix(), schema_valid=True,
            artifact_sha256=hashlib.sha256(ref.path.read_bytes()).hexdigest()))

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


def _save_stage_result(run: RunHandle, stage: BoundResearchStage | None, result: ResultEnvelope) -> None:
    if stage is not None:
        record_research_stage_result(stage, result)
    else:
        atomic_json(run.root / "input/task_results" / (result.invocation_id + ".json"), result.model_dump())


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
    from app.harness.runtime.project_scope import validated_diff_paths
    diff = "".join(block.rstrip("\r\n") + "\n" for block in blocks)
    try:
        validated_diff_paths(diff)
    except ValueError as exc:
        run.write_event("agent_events", {"event": "coding.patch_not_executable", "version": version,
                                        "reason": str(exc)})
        return
    # Explanatory snippets stay in Markdown; only executable patches are archived.
    # Application still requires ToolRegistry and does not replace write verification.
    target.write_text(diff, encoding="utf-8")


def load_agent_handoff_context(
    run: RunHandle, node_key: str, *, revision_reason: str = "", registry: AgentRegistry | None = None,
) -> tuple[dict[str, str], dict[str, Any] | None]:
    """Load actual approved upstream documents without silently truncating them."""
    identity = parse_node_key(node_key)
    stage, attempt = identity.stage, identity.attempt
    if stage == "writing":
        from app.bridge.report_revision_context import report_revision_reason
        revision_reason = report_revision_reason(run, revision_reason)
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
            else:
                from app.bridge.idea_revision_context import failed_idea_revision_context
                upstream.update(failed_idea_revision_context(run.root, run.project))
        upstream["human_revision_request"] = (
            "Human reviewer rejected the current draft and requested a revised "
            f"version. Feedback: {revision_reason}"
        )
    if stage in {"coding", "execution"}:
        from app.bridge.repository_handoff import attach_repository_handoff
        attach_repository_handoff(run.project, upstream)
        if selected_data_source and not upstream.get("data_description", "").strip():
            upstream["data_description"] = selection_summary(selected_data_source)
        if stage == "execution" and not upstream.get("data_description", "").strip():
            from app.bridge.repository_handoff import baseline_data_description
            from app.harness.tools.project_repo import load_project_repo
            description = baseline_data_description(load_project_repo(run.project), upstream)
            if description:
                upstream["data_description"] = description
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
    from app.bridge.execution_evidence_handoff import execution_job_evidence
    out["execution.job_files"] = execution_job_evidence(run.root, run.run_id, run.project)
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
    """Summarize finite stored values without inventing metric direction or success.

    The file alone does not define comparison policy. Keep source row identities
    so callers can inspect receipts before making acceptance or ranking claims.
    """
    metric_rows: list[dict[str, Any]] = []
    numeric: dict[str, list[float]] = {}
    ignored_values = 0
    for index, item in enumerate(rows):
        if not isinstance(item, dict):
            continue
        metrics = item.get("metrics")
        if not isinstance(metrics, dict):
            continue
        finite: dict[str, float] = {}
        for key, value in metrics.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                ignored_values += 1
                continue
            try:
                number = float(value)
            except OverflowError:
                ignored_values += 1
                continue
            if not math.isfinite(number):
                ignored_values += 1
                continue
            finite[str(key)] = number
            numeric.setdefault(str(key), []).append(number)
        run_id = item.get("run_id")
        metric_rows.append({"source_row": index, "run_id": run_id if isinstance(run_id, str) else "",
                            "metrics": finite})

    lines = [
        "# Actual execution metrics",
        f"source: {source_ref}",
        "These are stored post-run metric records, not an execution plan. Verify job status and provenance in the original receipts.",
        "Metric direction, units, targets and comparison protocol must come from this task's approved contract; no ranking or goal decision is inferred here.",
        f"- rows: {len(metric_rows)}",
        f"- omitted_non_numeric_or_non_finite_values: {ignored_values}",
    ]
    for key in sorted(numeric):
        values = numeric[key]
        if not values:
            continue
        lines.append(
            f"- {key}: min={min(values):.6g}, max={max(values):.6g}, mean={mean(values):.6g}, count={len(values)}"
        )
    if not numeric:
        lines.append("- finite measurements: unavailable; no outcome can be established from these records")
    if metric_rows:
        lines.append("## First stored rows in source order (not ranked)")
        for item in metric_rows[:5]:
            lines.append("- " + json.dumps(item, ensure_ascii=False, sort_keys=True, allow_nan=False))
        if len(metric_rows) > 5:
            lines.append(f"- additional rows: {len(metric_rows) - 5}; inspect {source_ref} for all records")
    return "\n".join(lines)


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

    import json

    from app.bridge.tensorboard_service import get_tensorboard_manager
    from app.execution.tensorboard_writer import ExecutionScalars

    from app.bridge.execution_confirmation import require_confirmation
    confirmed = require_confirmation(run, node_key)
    attempt = parse_node_key(node_key).attempt
    from app.bridge.execution_batch_plan import prepare_execution
    prepared = prepare_execution(run, node_key)
    plan_source = prepared.plan_source
    planned_before_intent = prepared.planned_before_intent
    intent_count = prepared.intent_count
    intent_wants_sweep = prepared.intent_wants_sweep
    selected_data_source = prepared.selected_data_source
    backend = prepared.configured_backend
    runtime_backend = prepared.runtime_backend
    max_concurrency = prepared.max_concurrency
    configured_max_concurrency = prepared.configured_max_concurrency
    batch_steps = prepared.batch_steps
    specs = prepared.specs
    for spec in specs:
        spec.config["confirmation_token"] = confirmed["token"]

    async def check_inputs() -> None:
        import asyncio
        current = await asyncio.to_thread(require_confirmation, run, node_key)
        if current["token"] != confirmed["token"]:
            raise ValueError("执行输入已变化；尚未启动后续作业。")
    scalars = ExecutionScalars(run.subdir("execution") / "tensorboard" / f"attempt_{attempt}")
    async def _publish(channel: str, payload: dict[str, Any]) -> None:
        try:
            scalars.record(payload)
        except Exception as exc:
            logger.warning("TensorBoard metric write failed: {}", exc)
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

    display = get_tensorboard_manager()
    try:
        activation = await display.activate(run, attempt)
        await _publish(f"run.{run.run_id}.execution", {"event": "execution.tensorboard_ready", **activation})
    except Exception as exc:
        logger.warning("TensorBoard display unavailable: {}", exc)
        await _publish(f"run.{run.run_id}.execution", {"event": "execution.tensorboard_failed", "error": str(exc)})
    phase = "failed"
    try:
        outcome = await run_batch(
            specs,
            config=BatchConfig(max_concurrency=max_concurrency, steps=batch_steps),
            bus_publish=_publish,
            check_inputs=check_inputs,
        )
        phase = "failed" if outcome.failures else "completed"
    finally:
        scalars.close()
        display.finish(run, phase)

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
