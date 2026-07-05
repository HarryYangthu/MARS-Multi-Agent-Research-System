# `posttrain/` — V2 dry-run home

This directory contains the V2 CPU/mock post-training dry-run. It does not run
GPU GRPO yet; it validates the data path, preference traceability, reward
families, and output locations required before a real trainer is attached.

## V0 boundary

> V0 only supports **loading** post-trained models (vLLM serve / LoRA adapter / remote endpoint).
> V0 does NOT implement GRPO training, preference pair construction, or reward design — those are V2.

## V0 hooks already in place

- `harness/llm/post_training_loader.py` — accepts `(adapter | endpoint | fine_tuned_id)` modes from `configs/agents.yaml::coding.post_training`
- `harness/llm/local_vllm_provider.py` — talks to a local vLLM serve endpoint (LoRA adapter loaded server-side)
- `configs/agents.yaml::coding.post_training` — schema documents the load-only fields V0 honours and the `live_checkpoint_path` field reserved for V2
- `harness/evaluation/post_training_export.py` — exports approved evaluated artifacts to JSONL candidates with composite labels and evidence refs; it does not build preference pairs or start training

## V2 dry-run

```bash
PYTHONPATH=backend:posttrain/src python -m mars_posttrain \
  --run-root runs/<run_id> \
  --output-root posttrain \
  --include-drafts
```

The dry-run:

- reads or creates `runs/<run_id>/events/post_training_export.jsonl`;
- builds preference candidates from approved artifacts and sibling drafts;
- requires HITL provenance from `runs/<run_id>/hitl/*`;
- scores `schema_validity`, `baseline_preservation`, and `downstream_metric`;
- writes reports under `posttrain/reports/` and mock checkpoints under
  `posttrain/checkpoints/`, both ignored by git;
- runs on CPU/mock data and requires no GPU.
