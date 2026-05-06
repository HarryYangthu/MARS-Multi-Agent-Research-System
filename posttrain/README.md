# `posttrain/` — V0 placeholder, V1 home

This directory is intentionally empty in V0. V1 will host:

- `grpo_trainer/` — the GRPO training loop (preference optimization)
- `preference_pairs/` — pair construction toolkit (mining accepted/rejected pairs from runs/<id>/hitl/)
- `reward_design/` — composite reward heads (schema validity, baseline preservation, downstream metrics)
- `live_checkpoint/` — checkpoint loader for the Coding Agent's `live_checkpoint` backend

## V0 boundary (CLAUDE.md hard constraint #5)

> V0 only supports **loading** post-trained models (vLLM serve / LoRA adapter / remote endpoint).
> V0 does NOT implement GRPO training, preference pair construction, or reward design — those are V1.

## V0 hooks already in place

- `harness/llm/post_training_loader.py` — accepts `(adapter | endpoint | fine_tuned_id)` modes from `configs/agents.yaml::coding.post_training`
- `harness/llm/local_vllm_provider.py` — talks to a local vLLM serve endpoint (LoRA adapter loaded server-side)
- `configs/agents.yaml::coding.post_training` — schema documents the load-only fields V0 honours and the `live_checkpoint_path` field reserved for V1
