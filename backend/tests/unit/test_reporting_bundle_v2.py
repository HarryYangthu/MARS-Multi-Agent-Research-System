from __future__ import annotations

import json
import zipfile
from pathlib import Path

from app.harness.schema.frontmatter_parser import dumps as fm_dumps
from app.harness.schema.validator import validate_document
from app.reporting import generate_report_bundle, read_latest_report_bundle
from app.storage.artifact_store import ArtifactStore
from app.storage.run_store import RunStore


def test_generate_report_bundle_writes_data_pack_and_office_files(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task="report bundle", project="pimc")
    metrics_path = run.subdir("execution") / "metrics.json"
    metrics_path.write_text(
        json.dumps(
            [
                {
                    "run_id": "exp_1",
                    "metrics": {"RES": -31.2, "loss": 0.02, "pim_suppression_db": 18.4},
                    "fingerprint_hash": "abc",
                    "duration_seconds": 1.2,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    ArtifactStore(run).write(
        text=fm_dumps(
            {
                "schema": "report.v1",
                "project": "pimc",
                "agent": "writing",
                "deliverable_type": "research_report",
                "target_audience": "research team",
                "chain_refs": {"runs": ["exp_1"]},
            },
            "# Research Report\n\nRES improved in the mock run.\n",
        ),
        expected_schema="report.v1",
    )

    result = generate_report_bundle(run, actor="test")

    assert result["exists"] is True
    manifest = run.root / str(result["manifest"])
    assert manifest.exists()
    assert validate_document(manifest.read_text(encoding="utf-8"), expected_schema="report_bundle.v1").valid

    data_pack = run.subdir("writing") / "report_data_pack.v1.json"
    assert data_pack.exists()
    parsed = json.loads(data_pack.read_text(encoding="utf-8"))
    assert parsed["summary"]["experiment_count"] == 1

    deliverables = run.subdir("writing") / "deliverables"
    for filename in ("results_workbook.xlsx", "research_report.docx", "research_deck.pptx"):
        path = deliverables / filename
        assert path.exists()
        with zipfile.ZipFile(path) as zf:
            assert zf.testzip() is None

    latest = read_latest_report_bundle(run)
    assert latest is not None
    assert latest["metadata"]["qa_status"]["status"] in {"passed", "degraded"}

