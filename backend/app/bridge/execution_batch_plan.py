"""Shared preparation for execution preview and the actual simulation batch."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.bridge.node_key import parse_node_key
from app.execution.simulation_runner import JobSpec
from app.harness.execution_intent import requested_experiment_count, wants_execution_sweep
from app.harness.schema.frontmatter_parser import parse as parse_fm
from app.harness.tools.config import load_execution_config
from app.settings import get_settings
from app.storage.run_store import RunHandle


@dataclass
class PreparedExecution:
    specs: list[JobSpec]
    plan_source: str
    planned_before_intent: int
    intent_count: int | None
    intent_wants_sweep: bool
    configured_backend: str
    runtime_backend: str
    max_concurrency: int
    configured_max_concurrency: int
    batch_steps: int
    selected_data_source: dict[str, Any]


def prepare_execution(run: RunHandle, node_key: str) -> PreparedExecution:
    # The adapter owns these existing intake rules. No tools or jobs are run.
    from app.bridge.agent_runner import (
        _execution_intent_text, _load_selected_data_source, _planned_seed, _positive_int,
    )
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

    return PreparedExecution(specs, plan_source, planned_before_intent, intent_count,
        intent_wants_sweep, backend, runtime_backend, max_concurrency,
        configured_max_concurrency, batch_steps, selected_data_source or {})
