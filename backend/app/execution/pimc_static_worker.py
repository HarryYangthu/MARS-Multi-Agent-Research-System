"""CPU adapter for an external static PIMC checkout; never contains research models.

The upstream metric/predict/count functions are authoritative. All methods use
one matched training implementation; test samples are not loaded into a training
split and are evaluated only by the separately invoked finalize operation.
This process isolation is not an OS security sandbox for hostile Python code.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import importlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import time
from typing import Any

from app.harness.agent_loop.trace import atomic_json
from app.harness.research_trial import file_sha256, read_record


def dependencies(repo: Path) -> tuple[Any, Any, Any, Any]:
    sys.path.insert(0, str(repo))
    torch = importlib.import_module("torch")
    np = importlib.import_module("numpy")
    evaluator = importlib.import_module("tools.train_static_compression")
    models = importlib.import_module("libs.model")
    # Guard against accidentally importing a different installed `tools` package.
    if not evaluator.__file__ or not Path(evaluator.__file__).resolve().is_relative_to(repo.resolve()):
        raise ValueError("Evaluator imported outside the frozen source repository")
    return torch, np, evaluator, models


def load_model(torch: Any, models: Any, cfg: dict[str, Any], candidate: Path | None) -> Any:
    torch.manual_seed(cfg["seed"])
    if candidate is None:
        return models.StaticPIMC(channels=16, cfg=cfg["baseline"])
    spec = importlib.util.spec_from_file_location("mars_research_candidate", candidate)
    if spec is None or spec.loader is None:
        raise ValueError("Cannot load candidate module")
    module = importlib.util.module_from_spec(spec)
    # Dataclasses and postponed annotations resolve their defining module here.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    model = module.build_model({"channels": 16, "baseline": deepcopy(cfg["baseline"]), "context": cfg["context"]})
    if not isinstance(model, torch.nn.Module):
        raise TypeError("build_model(config) must return torch.nn.Module")
    return model


def check_model(torch: Any, evaluator: Any, model: Any, cfg: dict[str, Any]) -> dict[str, Any]:
    # [time, channels] complex input/output, covering two lengths to detect fixed sizing.
    for length in (cfg["fft_length"] + 2 * cfg["context"], cfg["fft_length"] + 2 * cfg["context"] + 17):
        x = torch.randn(length, 16, dtype=torch.complex64)
        model.zero_grad(set_to_none=True)
        y = model(x)
        if y.shape != x.shape or y.dtype != torch.complex64 or not torch.isfinite(y).all():
            raise ValueError("Candidate must preserve finite complex64 [time,16] tensors")
        y.abs().square().mean().backward()
        gradients = [p.grad for p in model.parameters() if p.requires_grad]
        if not gradients or any(g is None or not torch.isfinite(g).all() for g in gradients):
            raise ValueError("Candidate contains disconnected or nonfinite trainable gradients")
    if getattr(model, "context", 0) > cfg["context"]:
        raise ValueError("Candidate receptive field exceeds the frozen context")
    return dict(evaluator.parameter_counts(model))


def load_splits(torch: Any, cfg: dict[str, Any], *, final: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    path = Path(cfg["data_path"])
    if file_sha256(path) != cfg["data_sha256"]:
        raise ValueError("Dataset changed since the protocol was frozen")
    raw = torch.load(path, map_location="cpu", weights_only=False)
    # Stored [channels,time] -> model [time,channels], scaled identically for x/y/nf.
    arrays = {k: torch.as_tensor(raw[k]).T.to(torch.complex64).contiguous() / cfg["scale"]
              for k in ("x", "y", "nf")}
    shape = arrays["x"].shape
    if len(shape) != 2 or shape[1] != 16 or any(v.shape != shape or not torch.isfinite(v).all() for v in arrays.values()):
        raise ValueError("Dataset requires equal finite x/y/nf arrays stored as [16,time]")
    n, guard, context = shape[0], cfg["split_guard"], cfg["context"]
    a = int(n * cfg["train_fraction"])
    b = int(n * (cfg["train_fraction"] + cfg["validation_fraction"]))
    bounds = {"train": [0, a-guard], "validation": [a+guard, b-guard], "test": [b+guard, n]}
    if guard < context or any(hi-lo < 1024+2*context for lo, hi in bounds.values()):
        raise ValueError("Insufficient guarded train/validation/test samples")
    wanted = ("test",) if final else ("train", "validation")
    return {s: {k: v[bounds[s][0]:bounds[s][1]] for k, v in arrays.items()} for s in wanted}, bounds


def score(evaluator: Any, model: Any, split: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    model.eval()
    context = cfg["context"]
    pred = evaluator.predict(model, split["x"], cfg["batch_samples"], context)
    result: dict[str, Any] = evaluator.metric(split["y"][context:-context].numpy()-pred,
                                            split["nf"][context:-context].numpy(), cfg)
    if not math.isfinite(float(result["RES_db"])):
        raise ValueError("Nonfinite evaluation metric")
    return result


def train(torch: Any, np: Any, evaluator: Any, model: Any, splits: dict[str, Any],
          cfg: dict[str, Any], out: Path) -> dict[str, Any]:
    length, context, batch = cfg["fft_length"], cfg["context"], cfg["batch_samples"]
    window = torch.kaiser_window(length, periodic=False, beta=10)
    frequency = torch.fft.fftfreq(length, d=1/cfg["fs"])
    mask = (frequency > cfg["band"][0]) & (frequency < cfg["band"][1])
    if not mask.any():
        raise ValueError("Empty objective frequency band")

    def power(x: Any) -> Any:
        # [time,channels] -> [frames,channels,frequency].
        frames = x.unfold(0, length, length//2)
        return torch.fft.fft(frames * window, dim=-1)[..., mask].abs().square().mean(dim=(0, 2))

    data = splits["train"]
    nf_power = power(data["nf"]).detach().clamp_min(1e-20)
    broadband = data["y"].abs().square().mean().detach().clamp_min(1e-20)
    starts = list(range(context, len(data["x"])-context-batch+1, batch))
    if not starts:
        raise ValueError("Training segment has no full batch")
    optimizer = torch.optim.Adam(model.parameters(), lr=cfg["learning_rate"])
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, cfg["epochs"], eta_min=cfg["min_learning_rate"])
    best, selected, completed, best_epoch = float("inf"), {}, 0, 0
    training_steps: list[dict[str, Any]] = []
    started = time.monotonic()
    history = out / "history.jsonl"
    for epoch in range(cfg["epochs"] + 1):
        if epoch:
            model.train()
            order = np.random.default_rng(cfg["seed"] + epoch).permutation(starts)
            updates = evaluator.step_limited_order(order, completed, cfg["max_steps"])
            for start in updates:
                # Padded [batch+2*context,16] -> scored center [batch,16].
                x = data["x"][start-context:start+batch+context]
                error = model(x)[context:context+batch] - data["y"][start:start+batch]
                loss = (power(error)/nf_power).mean().sqrt() + cfg["broadband_weight"] * error.abs().square().mean()/broadband
                if not torch.isfinite(loss):
                    raise FloatingPointError("Nonfinite training loss")
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                gradient_norm = torch.nn.utils.clip_grad_norm_(model.parameters(), 10, error_if_nonfinite=True)
                optimizer.step()
                completed += 1
                update = {"optimizer_step": completed, "epoch": epoch, "train_start_sample": int(start),
                          "training_loss": float(loss.detach()), "gradient_norm": float(gradient_norm),
                          "learning_rate": optimizer.param_groups[0]["lr"], "elapsed_seconds": time.monotonic()-started}
                training_steps.append(update)
                with (out / "steps.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(update, allow_nan=False) + "\n")
            if len(updates) == len(order):
                scheduler.step()
        validation = score(evaluator, model, splits["validation"], cfg)
        row = {"epoch": epoch, "optimizer_steps": completed, "validation": validation}
        with history.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, allow_nan=False) + "\n")
        if validation["RES_db"] < best:
            best, best_epoch = validation["RES_db"], epoch
            selected = {**row}
            temporary = out / "best.tmp.pt"
            torch.save({"model": model.state_dict()}, temporary)
            temporary.replace(out / "best.pt")
        if completed >= cfg["max_steps"] or epoch-best_epoch >= cfg["patience"]:
            break
    if completed < 1:
        raise ValueError("Experiment performed no optimizer updates")
    reason = "max_steps" if completed >= cfg["max_steps"] else "early_stopping" if epoch-best_epoch >= cfg["patience"] else "epochs_exhausted"
    return {"validation": selected["validation"], "selected_epoch": selected["epoch"],
            "selected_optimizer_steps": selected["optimizer_steps"], "optimizer_steps": completed,
            "stop_reason": reason, "epochs_run": epoch, "requested_max_steps": cfg["max_steps"],
            "training_diagnostics": {"first_update_loss": training_steps[0]["training_loss"],
                "last_update_loss": training_steps[-1]["training_loss"],
                "min_gradient_norm": min(s["gradient_norm"] for s in training_steps),
                "max_gradient_norm": max(s["gradient_norm"] for s in training_steps)},
            "elapsed_seconds": time.monotonic()-started, "checkpoint_sha256": file_sha256(out / "best.pt")}


def execute(job: dict[str, Any]) -> dict[str, Any]:
    repo, out = Path(job["repo"]), Path(job["output"])
    if job["operation"] not in {"preflight", "train", "finalize"}:
        raise ValueError("Unsupported worker operation")
    cfg = read_record(Path(job["protocol"]))
    torch, np, evaluator, models = dependencies(repo)
    torch.set_num_threads(cfg["threads"])
    torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)
    np.random.seed(cfg["seed"])
    candidate = Path(job["candidate"]) if job.get("candidate") else None
    model = load_model(torch, models, cfg, candidate)
    counts = check_model(torch, evaluator, model, cfg)
    baseline_count = evaluator.parameter_counts(models.StaticPIMC(channels=16, cfg=cfg["baseline"]))["real_parameters"]
    if candidate and counts["real_parameters"] > baseline_count * (1-cfg["reduction"]):
        raise ValueError(f"Parameter budget exceeded: {counts['real_parameters']} > {baseline_count * (1-cfg['reduction'])}")
    result = {"status": "completed", "operation": job["operation"], **counts, "baseline_parameters": baseline_count,
              "torch": str(torch.__version__), "numpy": str(np.__version__),
              "scipy": str(importlib.import_module("scipy").__version__),
              "device": "cpu", "protocol_sha256": file_sha256(Path(job["protocol"])),
              "candidate_sha256": file_sha256(candidate) if candidate else None}
    if job["operation"] == "preflight":
        if cfg["data_sha256"] is not None:
            _, bounds = load_splits(torch, cfg, final=False)
            result["data_verified"] = True
            result["split_samples"] = bounds
        return result
    final = job["operation"] == "finalize"
    splits, bounds = load_splits(torch, cfg, final=final)
    # Reconstruct after preflight so its forwards/backwards cannot change initialization.
    model = load_model(torch, models, cfg, candidate)
    if final:
        checkpoint = Path(job["checkpoint"])
        if file_sha256(checkpoint) != job["checkpoint_sha256"]:
            raise ValueError("Selected checkpoint was changed")
        model.load_state_dict(torch.load(checkpoint, map_location="cpu", weights_only=True)["model"], strict=True)
        result["test"] = score(evaluator, model, splits["test"], cfg)
    else:
        result.update(train(torch, np, evaluator, model, splits, cfg, out))
    result["split_samples"] = bounds
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("job", type=Path)
    options = parser.parse_args()
    job = read_record(options.job)
    out = Path(job["output"])
    out.mkdir(parents=True, exist_ok=True)
    try:
        result = execute(job)
    except Exception as exc:
        atomic_json(out / "result.json", {"status": "failed", "operation": job["operation"],
                                          "error_type": type(exc).__name__, "error": str(exc)})
        raise
    atomic_json(out / "result.json", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
