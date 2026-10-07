"""Reference validation and actual archived job evidence; no service substitutes."""
import json
from pathlib import Path

import pytest

from app.bridge.execution_evidence_handoff import execution_job_evidence
from app.harness.reporting.source_refs import report_source_errors
from app.harness.schema.frontmatter_parser import dumps
from app.settings import repo_root
from app.bridge.report_revision_context import report_revision_reason
from app.storage.run_store import RunStore


def report(refs: list[str]) -> str:
    return dumps({"chain_refs": {"runs": refs}}, "Authored reference-validation input")


def test_missing_and_truncated_measured_refs_are_rejected(tmp_path: Path) -> None:
    (tmp_path / "execution").mkdir()
    (tmp_path / "execution/metrics.json").write_text("[]")
    errors = report_source_errors(tmp_path, report(["execution/metrics."]))
    assert len(errors) == 2
    assert "not an available file" in errors[0] and "metrics.json" in errors[1]
    assert report_source_errors(tmp_path, report(["execution/metrics.json"])) == []


def test_reference_scope_rejects_escape_and_symlink(tmp_path: Path) -> None:
    (tmp_path / "outside.md").write_text("Authored parser text")
    root = tmp_path / "run"
    root.mkdir()
    (root / "link.md").symlink_to(tmp_path / "outside.md")
    assert len(report_source_errors(root, report(["../outside.md", "link.md"]))) == 2


def test_job_handoff_never_admits_another_project(tmp_path: Path) -> None:
    jobs = tmp_path / "execution/jobs"
    jobs.mkdir(parents=True)
    (jobs / "authored-identity-input.json").write_text(json.dumps({"run_id": "other", "project": "other"}))
    text = execution_job_evidence(tmp_path, "requested", "requested")
    assert "evidence_error" in text and "identity differs" in text
    assert '"files"' not in text


def test_absent_jobs_do_not_invent_results(tmp_path: Path) -> None:
    text = execution_job_evidence(tmp_path, "requested", "requested")
    assert '"status"' not in text and '"lr_values"' not in text


def test_review_feedback_survives_retry_only_for_exact_unapproved_draft(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="authored-review-binding", project="classification")
    source = run.subdir("writing") / "research_report.v1.md"
    source.write_text(report([]))
    assert report_revision_reason(run, "Keep the explicit reviewer correction")
    assert report_revision_reason(run, "") == "Keep the explicit reviewer correction"
    source.write_text(source.read_text() + "Changed authored draft")
    assert report_revision_reason(run, "") == ""
    assert report_revision_reason(run, "New correction") == "New correction"
    (run.subdir("writing") / "research_report.approved.md").write_bytes(source.read_bytes())
    assert report_revision_reason(run, "") == ""


def test_review_feedback_does_not_leak_to_new_report_or_project(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="authored-review-binding", project="classification")
    assert report_revision_reason(run, "") == ""
    (run.subdir("writing") / "research_report.v1.md").write_text(report([]))
    report_revision_reason(run, "Correction for first draft")
    receipt = run.subdir("hitl") / "report_revision_request.json"
    data = json.loads(receipt.read_text())
    data["project"] = "other"
    receipt.write_text(json.dumps(data))
    assert report_revision_reason(run, "") == ""
    report_revision_reason(run, "Second correction")
    (run.subdir("writing") / "research_report.v2.md").write_text(report([]))
    assert report_revision_reason(run, "") == ""


def test_current_real_archive_preserves_job_paths_hashes_and_lr() -> None:
    name = "2026-10-07T1221_static_pimc_lr_acceptance"
    root = repo_root() / "runs" / name
    if not (root / "execution/metrics.json").is_file():
        pytest.skip("This specific real acceptance archive is unavailable")
    text = execution_job_evidence(root, name, "folder_463822e8e13f43a2b02facc478134dc0")
    assert "evidence_error" not in text
    records = [json.loads(line) for line in text.splitlines() if line.startswith('{')]
    assert len(records) == 2
    for record in records:
        assert record["status"] == "completed" and record["attempt"] == 1
        assert record["actual_output_dir"].startswith("execution/paper_static/")
        steps = next(item for item in record["files"] if item["source"].endswith("/steps.jsonl"))
        assert steps["rows"] == steps["lr_rows"] == steps["last_optimizer_step"] == 50
        assert steps["lr_values"] == ([0.0004] if record["experiment_id"] == "accept_baseline50" else [0.0006])
        assert len(steps["sha256"]) == 64
    original = (root / "writing/research_report.v1.md").read_text()
    assert report_source_errors(root, original), "The observed truncated evidence references must be rejected"
