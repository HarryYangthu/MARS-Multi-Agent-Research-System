from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.execution.paper_static_adapter import run_paper_static_simulation
from app.execution.simulation_runner import JobSpec


@pytest.mark.asyncio
async def test_paper_static_adapter_maps_external_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "paper_code"
    repo.mkdir()
    config = repo / "configs" / "static.yaml"
    config.parent.mkdir()
    config.write_text("data_name: fake\n", encoding="utf-8")
    data = tmp_path / "capture.pth"
    data.write_bytes(b"fake")
    (repo / "train_static.py").write_text(
        """
from __future__ import annotations

import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--cfg")
parser.add_argument("--max-iters")
parser.add_argument("--tag")
parser.add_argument("--dry-run", action="store_true")
parser.add_argument("--set", action="append", default=[])
args = parser.parse_args()

output_dir = ""
for item in args.set:
    if item.startswith("output_dir="):
        output_dir = item.split("=", 1)[1]
run_dir = Path(output_dir) / ("external_" + args.tag)
run_dir.mkdir(parents=True, exist_ok=True)
summary = {
    "run_id": run_dir.name,
    "data_name": "fake_capture",
    "model": "StaticPIMC",
    "channels": 16,
    "epochs": 1,
    "PIM": 25.18,
    "RES": 16.53,
    "APE": 8.74,
}
(run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
print("epoch: 0_0 PIM: 25.18 RES: 16.53 APE: 8.74 PIM_MAX: 29.75 RES_MAX: 21.17")
print("done ->", run_dir)
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "app.execution.paper_static_adapter._paper_static_config",
        lambda: {
            "python": sys.executable,
            "repo_path": str(repo),
            "config_path": str(config),
            "data_path": str(data),
            "default_dry_run": False,
            "default_max_iters": 1,
            "timeout_seconds": 10,
        },
    )
    run_root = tmp_path / "runs" / "r1"
    spec = JobSpec(
        run_id="r1",
        experiment_id="static_a",
        project="pimc",
        config={},
        run_root=run_root,
    )

    result = await run_paper_static_simulation(spec, steps=1)

    assert result.status == "completed"
    assert result.is_mock is False
    assert result.metrics["paper_RES_db"] == 16.53
    assert result.metrics["paper_APE_db"] == 8.74
    assert result.metrics["RES"] == -8.74
    assert result.metrics["loss"] == pytest.approx(10 ** (-8.74 / 10))
    assert result.loss_curve == [pytest.approx(10 ** (-8.74 / 10))]
    assert (run_root / "execution" / "logs" / "static_a_paper_static.log").is_file()
    assert (run_root / "execution" / "paper_static" / "static_a_manifest.json").is_file()
