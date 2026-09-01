"""Run all twenty public synthetic candidates through the Core process adapter."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from app.bridge.extension_runtime import build_extension_runtime
from app.execution.adapters.base import AdapterAction, AdapterRequest
from app.execution.adapters.process import ProcessAdapter
from synthetic_regression_adapter import candidate_configs


async def run() -> dict[str, object]:
    root = Path(__file__).resolve().parents[2]
    pack_root = root / "projects" / "synthetic_regression"
    runtime = build_extension_runtime(
        distribution="v30-core",
        pack_roots=(pack_root,),
    )
    declaration = runtime.project_packs.get(
        "synthetic_regression"
    ).manifest.adapters["evaluator"]
    adapter = ProcessAdapter(
        name="synthetic_regression:evaluator",
        argv=tuple(
            sys.executable if token == "{python}" else token
            for token in declaration.argv
        ),
        timeout_seconds=declaration.timeout_seconds,
    )
    candidates = candidate_configs()
    envelopes: list[dict[str, object]] = []
    for index, candidate in enumerate(candidates):
        response = await adapter.invoke(
            AdapterRequest(
                action=AdapterAction.EVALUATE,
                request_id=f"release-smoke-{index:02d}",
                project="synthetic_regression",
                run_id="release-smoke",
                candidate_id=f"core-release-{index:02d}",
                seed=index,
                config={
                    "model_genome": {
                        "schema_id": "model_genome.v1",
                        "family": candidate.config["family"],
                        "structure": {},
                        "hyperparameters": candidate.config["hyperparameters"],
                        "recipe": {},
                        "mutable_zones": (
                            "hyperparameters.degree",
                            "hyperparameters.regularization",
                        ),
                    },
                    "mode": "mock",
                    "candidate_count": 20,
                    "seed": index,
                    "fidelity": "F0",
                },
            )
        )
        if response.status != "ok":
            raise RuntimeError(
                f"candidate {candidate.candidate_id} failed: {response.error_code} {response.error}"
            )
        envelope = response.raw_metrics
        if envelope.get("schema_id") != "metric_envelope.v1":
            raise RuntimeError("adapter did not return metric_envelope.v1")
        envelopes.append(envelope)
    return {
        "schema_id": "synthetic_release_smoke.v1",
        "distribution": runtime.profile.name,
        "pack_version": runtime.project_packs.get(
            "synthetic_regression"
        ).manifest.pack_version,
        "candidate_count": len(candidates),
        "unique_candidate_ids": len({candidate.candidate_id for candidate in candidates}),
        "unique_envelopes": len(
            {str(envelope.get("envelope_hash")) for envelope in envelopes}
        ),
        "status": "passed",
    }


def main() -> int:
    try:
        result = asyncio.run(run())
    except (RuntimeError, ValueError, KeyError) as exc:
        sys.stderr.write(f"synthetic smoke failed: {exc}\n")
        return 1
    sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
