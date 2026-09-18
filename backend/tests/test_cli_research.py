"""Pure contract checks and actual filesystem/subprocess checks; no provider doubles."""
from __future__ import annotations

import asyncio
import importlib
import os
from pathlib import Path
from typing import Any

import pytest

from app.agents.research_cli import candidate_errors
from app.bridge.cli_research_service import initialize, materialize, prepare_protocol, verify_run
from app.cli import dispatch, parser
from app.execution.research_process import run_worker
from app.execution.pimc_static_worker import load_model
from app.harness.agent_loop.trace import atomic_json
from app.harness.research_trial import ResearchBudget, compare, read_record, select_candidate
from app.harness.schema.frontmatter_parser import dumps


def test_parameter_target_and_strict_performance() -> None:
    baseline = {"real_parameters": 100, "validation": {"RES_db": 4.0}}
    candidate = {"real_parameters": 80, "validation": {"RES_db": 4.0}}
    assert compare(baseline, candidate, ResearchBudget())["passed"]
    candidate["validation"] = {"RES_db": 4.000001}
    assert not compare(baseline, candidate, ResearchBudget())["passed"]
    with pytest.raises(ValueError):
        compare(baseline, {**candidate, "validation": {"RES_db": float("nan")}}, ResearchBudget())


def test_selection_ignores_test_and_rejects_oversize() -> None:
    baseline = {"real_parameters": 100, "validation": {"RES_db": 4.0}}
    a = {"status": "completed", "real_parameters": 70, "validation": {"RES_db": 3.0}, "test": {"RES_db": 100.0}}
    b = {"status": "completed", "real_parameters": 80, "validation": {"RES_db": 3.1}, "test": {"RES_db": -100.0}}
    oversize = {**a, "real_parameters": 81, "validation": {"RES_db": 0.0}}
    assert select_candidate(baseline, [b, oversize, a], ResearchBudget()) is a
    assert select_candidate(baseline, [{"status": "failed"}], ResearchBudget()) is None


def test_source_contract_rejects_metric_edits_and_dynamic_execution() -> None:
    assert candidate_errors("from tools.train_static_compression import metric\ndef build_model(config):\n return metric\n")
    assert candidate_errors("def build_model(config):\n return eval(config['code'])\n")
    assert candidate_errors("async def build_model(config):\n return None\n")
    assert not candidate_errors("from __future__ import annotations\nfrom libs.model_static_compact import StaticCompactPIMC\ndef build_model(config):\n return StaticCompactPIMC(channels=config['channels'])\n")


def static_repo() -> Path:
    raw = os.environ.get("MARS_TEST_STATIC_REPO")
    if not raw:
        pytest.skip("Set MARS_TEST_STATIC_REPO to an actual external static checkout")
    return Path(raw).resolve()


@pytest.mark.asyncio
async def test_actual_static_baseline_preflight(tmp_path: Path) -> None:
    repo = static_repo()
    atomic_json(tmp_path / "protocol.json", prepare_protocol(repo, tmp_path / "absent.pth", ResearchBudget()))
    result = await run_worker({"repo": str(repo), "protocol": str(tmp_path / "protocol.json"), "operation": "preflight"}, tmp_path / "worker", 120)
    assert result["status"] == "completed"
    assert result["real_parameters"] == 2 * result["logical_parameters"]
    assert result["real_parameters"] > 0
    assert "validation" not in result and "test" not in result


@pytest.mark.asyncio
async def test_missing_real_data_blocks_before_agents(tmp_path: Path) -> None:
    repo = static_repo()
    options = parser().parse_args(["research", "--repo", str(repo), "--data", str(tmp_path / "absent.pth"), "--output", str(tmp_path / "run")])
    result = await dispatch(options)
    assert result["status"] == "blocked_data"
    assert not (tmp_path / "run/stages").exists()
    state = read_record(tmp_path / "run/state.json")
    assert state["trials"] == {}
    assert "blocked_data" in (tmp_path / "run/report.md").read_text()


def test_protocol_mutation_blocks_resume(tmp_path: Path) -> None:
    repo = static_repo()
    root = tmp_path / "run"
    initialize(repo, tmp_path / "absent.pth", root, "compress", "deepseek-v4-flash", ResearchBudget())
    manifest, state = read_record(root / "input/manifest.json"), read_record(root / "state.json")
    verify_run(root, manifest, state)
    protocol = read_record(root / "experiment/protocol.json")
    protocol["max_steps"] = 1
    atomic_json(root / "experiment/protocol.json", protocol)
    with pytest.raises(ValueError, match="protocol changed"):
        verify_run(root, manifest, state)


@pytest.mark.asyncio
async def test_actual_worker_rejects_missing_capture(tmp_path: Path) -> None:
    repo = static_repo()
    atomic_json(tmp_path / "protocol.json", prepare_protocol(repo, tmp_path / "absent.pth", ResearchBudget()))
    result = await run_worker({"repo": str(repo), "protocol": str(tmp_path / "protocol.json"), "operation": "train"}, tmp_path / "worker", 120)
    assert result["status"] == "failed"
    assert result["error_type"] == "FileNotFoundError"
    assert "validation" not in result and "test" not in result


@pytest.mark.asyncio
async def test_gate_materialization_and_actual_candidate_gradient(tmp_path: Path) -> None:
    repo = static_repo()
    root = tmp_path / "run"
    initialize(repo, tmp_path / "absent.pth", root, "compress", "deepseek-v4-flash", ResearchBudget())
    manifest, state = read_record(root / "input/manifest.json"), read_record(root / "state.json")
    # Human-authored schema input to the materializer; not a provider or experiment response.
    source = "from libs.model_static_compact import StaticCompactPIMC\ndef build_model(config):\n return StaticCompactPIMC(channels=config['channels'])\n"
    document = dumps({"schema": "code_spec.v1", "project": manifest["project"], "agent": "coding",
                      "target_lang": "python", "baseline_compat": {"preserved": True},
                      "files_changed": [{"path": "libs/research_candidate.py", "type": "added"}], "source_code": source}, "Contract fixture")
    candidate = await materialize(root, manifest, "round_01", document)
    result = await run_worker({"repo": manifest["source_snapshot"], "protocol": str(root / "experiment/protocol.json"),
                               "candidate": str(candidate), "operation": "preflight"}, root / "execution/preflight", 120)
    assert result["status"] == "completed"
    assert result["real_parameters"] <= .8 * result["baseline_parameters"]
    assert not (repo / "libs/research_candidate.py").exists()
    verify_run(root, manifest, state)


@pytest.mark.asyncio
async def test_real_training_and_sealed_finalization_on_tensor_fixture(tmp_path: Path) -> None:
    """Exercise actual optimizer/checkpoint/metric machinery, NOT PIMC scientific acceptance."""
    repo = static_repo()
    torch = importlib.import_module("torch")
    generator = torch.Generator().manual_seed(1234)
    # Explicit analytical tensor fixture, not a substitute for the user's RF capture.
    x = torch.randn(16, 32768, generator=generator, dtype=torch.complex64) * 100
    noise = torch.randn(16, 32768, generator=generator, dtype=torch.complex64)
    capture = tmp_path / "analytical_tensor_fixture.pth"
    torch.save({"x": x, "y": .01 * x + noise, "nf": noise}, capture)
    cfg = prepare_protocol(repo, capture, ResearchBudget(max_steps=2))
    cfg["batch_samples"] = 2048
    protocol = tmp_path / "protocol.json"
    atomic_json(protocol, cfg)
    trained = await run_worker({"repo": str(repo), "protocol": str(protocol), "operation": "train"}, tmp_path / "train", 120)
    assert trained["status"] == "completed", trained
    assert trained["optimizer_steps"] == 2
    assert trained["stop_reason"] == "max_steps"
    assert trained["training_diagnostics"]["first_update_loss"] > 0
    curve = trained["training_curve"]
    assert curve["optimizer_observations"] == 2 and curve["validation_observations"] >= 2
    assert curve["contains_test_data"] is False
    assert Path(curve["path"]).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert len((tmp_path / "train/steps.jsonl").read_text().splitlines()) == 2
    assert trained["selected_optimizer_steps"] <= 2
    assert "test" not in trained
    final = await run_worker({"repo": str(repo), "protocol": str(protocol), "operation": "finalize",
        "checkpoint": str(tmp_path / "train/best.pt"), "checkpoint_sha256": trained["checkpoint_sha256"]}, tmp_path / "final", 120)
    assert final["status"] == "completed", final
    assert "test" in final and "validation" not in final
    assert trained["protocol_sha256"] == final["protocol_sha256"]


@pytest.mark.asyncio
async def test_preflight_rejects_actual_wrong_shape_file(tmp_path: Path) -> None:
    repo = static_repo()
    torch = importlib.import_module("torch")
    capture = tmp_path / "wrong_layout.pth"
    torch.save({name: torch.ones(8, 100, dtype=torch.complex64) for name in ("x", "y", "nf")}, capture)
    atomic_json(tmp_path / "protocol.json", prepare_protocol(repo, capture, ResearchBudget()))
    result = await run_worker({"repo": str(repo), "protocol": str(tmp_path / "protocol.json"), "operation": "preflight"}, tmp_path / "worker", 120)
    assert result["status"] == "failed"
    assert "stored as [16,time]" in result["error"]


def test_factory_cannot_change_trusted_baseline_configuration(tmp_path: Path) -> None:
    torch = pytest.importorskip("torch")
    source = tmp_path / "candidate.py"
    source.write_text("from __future__ import annotations\nfrom dataclasses import dataclass\nimport torch\n@dataclass\nclass Spec:\n width: int\ndef build_model(config):\n config['baseline']['width'] //= 2\n return torch.nn.Linear(Spec(config['baseline']['width']).width, 1)\n")
    config: dict[str, Any] = {"seed": 1, "context": 1, "baseline": {"width": 16}}
    first = load_model(torch, None, config, source)
    second = load_model(torch, None, config, source)
    assert config["baseline"]["width"] == 16
    assert first.in_features == second.in_features == 8
