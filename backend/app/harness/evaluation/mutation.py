"""Real baseline/candidate task comparisons for reviewed system mutations.

Only repository-owned task suites and one fixed Python evaluator are runnable.
There is no arbitrary command interface. A passing comparison establishes
tested Agent contract behavior, never a measured scientific gain.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

import yaml

from app.harness.agent_loop.trace import atomic_json, audit_trace, digest
from app.harness.tools.process_runtime import start_process, wait_process
from app.settings import env_or_local, repo_root


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def runtime_digest() -> str:
    """Bind measured behavior to the actual Python and configuration bytes."""
    root = repo_root()
    paths = [*root.joinpath("backend/app").rglob("*.py"), *root.joinpath("backend/app").rglob("*.md"),
             *root.joinpath("configs").rglob("*.yaml")]
    return digest({p.relative_to(root).as_posix(): file_digest(p) for p in sorted(paths) if p.is_file()})


def load_mutation_suite(suite_id: str) -> tuple[dict[str, Any], Path]:
    if not suite_id or not all(c.isalnum() or c in "_-" for c in suite_id):
        raise ValueError("mutation suite must be a registered suite ID")
    base = repo_root() / "configs" / "evaluation_suites"
    path = (base / (suite_id + ".yaml")).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ValueError("mutation suite cannot escape the trusted configuration directory")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema") != "agent_mutation_suite.v1":
        raise ValueError("suite must declare agent_mutation_suite.v1")
    if raw.get("agent") not in {"idea", "experiment", "coding", "writing"}:
        raise ValueError("mutation suite requires a native Agent with traceable model calls")
    tasks = raw.get("tasks")
    if not isinstance(tasks, list) or not 2 <= len(tasks) <= 8:
        raise ValueError("mutation comparison requires two to eight held-out tasks")
    ids: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict) or set(task) - {"id", "project", "user_request", "upstream", "extra"}:
            raise ValueError("invalid mutation task fields")
        for field in ("id", "project", "user_request"):
            if not isinstance(task.get(field), str) or not task[field].strip():
                raise ValueError(f"mutation task requires {field}")
        if task["id"] in ids or not all(c.isalnum() or c in "_-" for c in task["id"]):
            raise ValueError("mutation task IDs must be unique safe names")
        ids.add(task["id"])
        upstream = task.get("upstream", {})
        if not isinstance(upstream, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in upstream.items()):
            raise ValueError("mutation task upstream must contain text artifacts")
        extra = task.get("extra", {})
        if not isinstance(extra, dict) or set(extra) - {"idea_requirements", "scope", "context_sources", "skills"}:
            raise ValueError("mutation task extra contains unsupported runtime controls")
    timeout = raw.get("timeout_seconds", 300)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 1 <= timeout <= 900:
        raise ValueError("mutation task timeout must be between 1 and 900 seconds")
    if raw.get("validation_scope") != "agent_contract_behavior":
        raise ValueError("mutation suites must label their scope agent_contract_behavior")
    return raw, path


def compare_task_results(baseline: list[dict[str, Any]], candidate: list[dict[str, Any]]) -> dict[str, Any]:
    """Pure comparison; evidence verification is separate and mandatory."""
    errors: list[str] = []
    if len(baseline) < 2 or len(baseline) != len(candidate):
        errors.append("matching baseline and candidate task sets are required")
    if [x.get("task_id") for x in baseline] != [x.get("task_id") for x in candidate]:
        errors.append("task IDs differ between versions")
    baseline_successes = sum(x.get("host_accepted") is True for x in baseline)
    candidate_successes = sum(x.get("host_accepted") is True for x in candidate)
    if candidate_successes != len(candidate) or not candidate:
        errors.append("candidate must pass the host acceptance of every held-out task")
    if candidate_successes < baseline_successes:
        errors.append("candidate regressed on task success")
    def total_tokens(rows: list[dict[str, Any]]) -> int | None:
        values = [row.get("total_tokens") for row in rows]
        if any(type(x) is not int or x < 0 for x in values):
            return None
        return sum(int(x) for x in values if isinstance(x, int))
    before_tokens, after_tokens = total_tokens(baseline), total_tokens(candidate)
    improved = candidate_successes > baseline_successes
    if before_tokens is not None and after_tokens is not None and before_tokens > 0:
        improved = improved or (candidate_successes == baseline_successes and after_tokens < before_tokens)
    if not improved:
        errors.append("no measured task-success or token-cost improvement; do not promote an unproven mutation")
    return {"passed": not errors, "errors": errors, "baseline_successes": baseline_successes,
            "candidate_successes": candidate_successes, "baseline_tokens": before_tokens,
            "candidate_tokens": after_tokens, "validation_scope": "agent_contract_behavior",
            "scientific_validated": False}


def verify_task_evidence(root: Path, row: dict[str, Any], *, context_sha256: str) -> list[str]:
    """Re-read the actual trace and resource bytes before trusting a task score."""
    errors: list[str] = []
    if row.get("context_sha256") != context_sha256:
        return ["task measured a different resource version"]
    trace_ref = row.get("trace_ref")
    if not isinstance(trace_ref, str):
        return ["task has no model trace"]
    trace = (root / trace_ref).resolve()
    if not trace.is_relative_to(root.resolve()):
        return ["task trace escapes evaluation directory"]
    try:
        audit = audit_trace(trace)
        if audit.get("consistent") is not True:
            errors.append("task trace is inconsistent")
        facts = audit.get("facts", {})
        counts = facts.get("counts", {})
        if counts.get("sdk_attempts", 0) < 1 or counts.get("model_responses", 0) < 1:
            errors.append("task has no actual model attempt and response")
        events = [json.loads(x) for x in (trace / "events.jsonl").read_text().splitlines()]
        expected_resource = row.get("resource_text")
        consumed = isinstance(expected_resource, str) and expected_resource and any(
            event.get("kind") == "model_request" and isinstance(event.get("visible"), list)
            and event.get("visible_sha256") == digest(event["visible"])
            and any(isinstance(msg, dict) and expected_resource in str(msg.get("content", ""))
                    for msg in event["visible"])
            for event in events)
        if not consumed:
            errors.append("frozen resource was not found in actual model input")
        if row.get("host_accepted") is True and facts.get("status") != "passed":
            errors.append("task claims acceptance without passed native loop")
        artifact = root / str(row.get("artifact_ref", ""))
        if row.get("host_accepted") is True:
            if not artifact.resolve().is_relative_to(root.resolve()) or not artifact.is_file() or file_digest(artifact) != row.get("artifact_sha256"):
                errors.append("accepted artifact evidence missing or changed")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append("task evidence unavailable: " + type(exc).__name__)
    return errors


async def execute_mutation_comparison(
    *, mutation: dict[str, Any], evaluation_root: Path, suite_id: str,
) -> dict[str, Any]:
    suite, suite_path = load_mutation_suite(suite_id)
    if suite["agent"] != mutation["agent"]:
        raise ValueError("mutation suite targets a different Agent")
    evaluation_root.mkdir(parents=True, exist_ok=False)
    script = repo_root() / "scripts" / "evaluators" / "agent_mutation_task.py"
    runtime_before = runtime_digest()
    suite_before = file_digest(suite_path)
    evaluator_before = file_digest(script)
    atomic_json(evaluation_root / "suite.json", suite)
    rows: dict[str, list[dict[str, Any]]] = {"baseline": [], "candidate": []}
    snapshots = {"baseline": str(mutation["current_content"]), "candidate": str(mutation["proposed_content"])}
    for variant, content in snapshots.items():
        context_file = evaluation_root / (variant + ".md")
        context_file.write_bytes(content.encode("utf-8"))
        for task in suite["tasks"]:
            task_root = evaluation_root / variant / task["id"]
            task_root.mkdir(parents=True)
            request_path = task_root / "request.json"
            atomic_json(request_path, {"task": task, "agent": mutation["agent"], "resource_path": mutation["path"],
                                      "context_file": str(context_file.resolve()), "output_root": str(task_root.resolve())})
            output_path = task_root / "result.json"
            # Only the fixed repository evaluator runs; no task-supplied argv,
            # environment names, shell, entrypoint, or interpreter is accepted.
            with (task_root / "stdout.log").open("wb") as stdout, (task_root / "stderr.log").open("wb") as stderr:
                process = await start_process([sys.executable, str(script), str(request_path.resolve())],
                    cwd=repo_root(), env={"PYTHONPATH": str(repo_root() / "backend"), "MARS_CODING_BACKEND": "native_llm"},
                    credential_env=_model_credentials(str(mutation["agent"])), stdout=stdout, stderr=stderr)
                try:
                    returncode = await wait_process(process, timeout=float(suite.get("timeout_seconds", 300)))
                except asyncio.TimeoutError:
                    returncode = -1
            if output_path.is_file():
                row = json.loads(output_path.read_text())
                if not isinstance(row, dict):
                    row = {}
            else:
                row = {}
            row.update(task_id=task["id"], process_returncode=returncode,
                       result_ref=output_path.relative_to(evaluation_root).as_posix())
            errors = verify_task_evidence(task_root, row, context_sha256=file_digest(context_file))
            if returncode != 0:
                errors.append("task evaluator process failed or exceeded deadline")
            row["evidence_errors"] = errors
            row["evidence_verified"] = not errors
            row["evidence_files"] = {p.relative_to(task_root).as_posix(): file_digest(p)
                for p in sorted(task_root.rglob("*")) if p.is_file() and p.name in {
                    "result.json", "request.json", "artifact.md", "facts.json", "events.jsonl", "checkpoint.json"}}
            rows[variant].append(row)
    comparison = compare_task_results(rows["baseline"], rows["candidate"])
    errors = [str(error) for variant in rows.values() for row in variant for error in row["evidence_errors"]]
    if runtime_digest() != runtime_before:
        errors.append("runtime source/configuration changed during the comparison")
    if file_digest(suite_path) != suite_before or file_digest(script) != evaluator_before:
        errors.append("suite changed during comparison")
    passed = bool(comparison["passed"]) and not errors
    receipt = {"schema": "agent_mutation_evaluation.v1", "mutation_id": mutation["id"],
               "current_hash": mutation["current_hash"], "proposed_hash": mutation["proposed_hash"],
               "suite_id": suite_id, "suite_sha256": suite_before, "runtime_sha256": runtime_before,
               "evaluator_sha256": evaluator_before, "rows": rows, "comparison": comparison,
               "evidence_errors": errors, "passed": passed, "scientific_validated": False}
    atomic_json(evaluation_root / "receipt.json", receipt)
    return receipt


def _model_credentials(agent: str) -> dict[str, str]:
    """Pass only provider key names declared by trusted repository configuration."""
    raw = yaml.safe_load((repo_root() / "configs" / "agents.yaml").read_text()) or {}
    names: set[str] = set()
    roles = {agent}
    if agent == "idea":
        roles.add("idea_research")
        focused = yaml.safe_load((repo_root() / "configs" / "idea_focused.yaml").read_text()) or {}
        roles.update(str(focused[key]) for key in ("author_agent", "review_agent") if key in focused)
    for role in roles:
        body = raw.get(role, {})
        if isinstance(body, dict) and isinstance(body.get("model"), dict):
            name = body["model"].get("api_key_env")
            if isinstance(name, str) and name:
                names.add(name)
    return {name: value for name in names if (value := env_or_local(name))}
