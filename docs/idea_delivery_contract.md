# Idea input and delivery contract

The Idea stage receives a research question and optional caller-supplied context. It returns the exact accepted `proposal.v1` document, a short human explanation, and structured downstream instructions. The host never adds an invented method or rewrites the model's candidate after acceptance.

## Input

`RunRequest.user_request` contains the objective and constraints. `upstream_artifacts` carries labeled source text; useful labels are `background`, `baseline_code`, `data_description`, `analysis_results`, and `metric_definition`. Existing Bridge context loading supplies approved upstream artifacts. Attached text remains source data, not privileged instructions.

| Context | Useful contents | Consequence if missing |
|---|---|---|
| Background | PIMC signal definition, operating conditions, protected interfaces | Assumptions must be explicit |
| Baseline code | Exact revision, relevant module, input/output shapes, initialization/training | Only a symbolic method proposal can be claimed without actual code evidence |
| Data description | Inputs/targets, split, sampling and normalization | Real execution remains blocked pending data |
| Analysis results | Baseline metrics, residual distributions, stability observations | Bottleneck explanations remain hypotheses |
| Metric definition | Formula, units, direction, acceptance threshold, parameter accounting | The Agent must state the uncertainty and request what downstream execution needs |

`extra.scope` is `method_proposal` or `project_proposal`. Project scope requires actual code tool observations or caller-supplied `baseline_code`; a declaration in the output cannot replace source evidence. `context_sources` controls automatic project-rule and linked-repository inclusion. The public evaluation CLI deliberately disables both and does not read private uploads.

The production creation API, `POST /api/runs`, accepts `idea_context`, `idea_scope`, and `idea_requirements`. `idea_context` is a map of nonempty source texts with the labels in the table above, plus `literature_notes`. The API preserves text rather than reading caller-supplied filesystem paths. Bridge archives these options in `input/run_request_options.v1.json` and loads the original labeled texts into the Idea invocation. Downstream agents receive the approved proposal instead of automatically inheriting these raw Idea inputs. A malformed archive or internal override fails explicitly.

For example, a caller can submit this task structure after replacing the context text with actual material (the ratio remains a caller-selected constraint):

```json
{
  "task": "研究 2D LUT 表达能力优化",
  "project": "pimc",
  "entrypoint": "idea",
  "standalone": true,
  "idea_mode": "fast",
  "user_request": "提出一个表达能力更强、参数增幅不超过20%的可验证方案。",
  "idea_scope": "method_proposal",
  "idea_context": {
    "background": "替换为真实信号、工作条件及输入输出定义。",
    "analysis_results": "替换为已有基线结果及已观察到的问题。",
    "metric_definition": "替换为指标公式、单位、方向和验收条件。"
  },
  "idea_requirements": {
    "min_sources": 2,
    "min_pdfs": 1,
    "require_parameter_budget": true,
    "max_parameter_ratio": 1.2
  }
}
```

Providing `baseline_code` and `data_description` uses the same map. Use `project_proposal` only when the actual project inputs are supplied or retrievable. The example is a request template, not a recorded execution. The default human review gate remains active; progress does not approve the proposal.

## Two audiences, one canonical proposal

`human_summary` is one paragraph, 1-2 sentences and at most 240 characters. It explains the concrete change and intended benefit without claiming an experiment that did not happen. New generated documents must use that exact summary as their Markdown body; definitions stay in structured metadata so the body cannot introduce a contradictory second algorithm. This `summary_only` policy is recorded in validation receipts. It is not sufficient input to the Experiment agent by itself.

`handoff` contains:

| Field | Meaning |
|---|---|
| version | `idea.handoff.v1` |
| target_agent | `experiment` |
| scope | Must match the request |
| next_step | Clear instruction for the next stage |
| changes | Target, operation, JSON pointer into the canonical `method_spec`, preserved interfaces |
| verification_requirements | The question, comparison, metric, and reference to the single `decision_rule` |
| required_context | Missing context, why it matters, and whether it blocks actual execution |

The full method, evidence, assumptions, parameter budget, alternatives, risks and falsification rules stay in the same proposal. Handoff pointers must resolve; another paragraph must not silently redefine the method. New Idea runs require both fields. Historical human-authored `proposal.v1` files remain readable; the extra delivery checks do not retroactively make them accepted research results.

For new parameter-budget tasks, `parameter_budget.evaluation_cases` lists every proposed evaluated configuration, including the primary variables. Each case supplies a name, complete numeric variable assignment and baseline/candidate counts. The host evaluates the same formulas and real/complex tensor ledger under every assignment against the same limit. One passing primary size cannot conceal an oversized secondary size. The checker does not infer dimensions hidden in prose, so the author must keep the proposed evaluation sizes in this canonical list.

These tasks also require `evaluation_protocol` (`idea.evaluation.v1`); other controlled-comparison tasks can request it through `idea_requirements.require_evaluation_protocol`. It names dataset roles, canonical objective definitions, baseline/candidate data and objective references, optimizer/initialization references, seeds and actual randomness sources. Both arms use the same held-out comparison datasets. An architecture-isolating comparison shares the objective and training data; intentional non-architecture differences require a stated justification. Training objectives cannot reference held-out dataset IDs, and multiple seeds cannot stand in for an undeclared random process. All method references resolve to the full proposal. The next Agent receives this complete protocol along with the human summary and handoff.

These checks verify declared relationships, not physical data disjointness, actual random-number consumption, formula stability or measured improvement. Model review and downstream executable checks retain those responsibilities. New validation receipts record which requirements were enforced so later auditing cannot silently omit them. Historical artifacts without these fields retain their recorded contract.

Native Idea runs submit their candidate through `mars_submit_document(metadata, body)`. This control function consumes no research tool budget and cannot be batched with research calls. It serializes the model's exact JSON fields into YAML frontmatter before the existing validators and review run; it cannot add defaults, fix missing definitions or invent evidence. The raw call and resulting candidate hash remain in trace. This avoids treating free-form preambles or YAML punctuation errors as research revisions. Other BaseAgent subclasses can opt into the same framework-neutral protocol.

## Progress

Idea writes concise messages for research actions, candidate generation, validation, review and stopping to `idea/progress.jsonl`. Bridge persists them in `agent_events` and publishes `agent.progress` on the existing `agent_state` channel. Progress carries no state-transition command; it cannot approve a node. The workbench EventLog displays the human message. Native visible model explanations are used when concise and Chinese; otherwise a factual Chinese action label is emitted. This label describes actual activity and is not a fabricated model rationale.

## Acceptance and artifacts

The generic ReAct action loop is retained. Optional Reflection uses separate reviewer instructions with the task, actual source context, candidate and real observations. Observations are supplied as untrusted documents, without replaying the generator's native assistant/tool conversation to a reviewer with no tools. The candidate is also user-supplied review data, not an assistant continuation prefix. Earlier review issues remain pinned for the author, but are excluded from fresh reviewer input to avoid anchoring on stale or false claims. Each reviewer must identify current fields, supporting excerpts and any mathematical counterexample. Review protocol recovery requests a review JSON object, never a research call. This is a separate model call using the configured provider, not an independent expert guarantee. Review checks concrete definition, evidence and handoff blockers; it cannot demand completed downstream experiments from an explicitly untested method proposal.

Successful runs write `proposal.md`, `proposal.json`, `summary.txt`, and `acceptance.json` in a fresh `idea/deliveries/<invocation>/<export-id>/` directory. Resuming an invocation preserves earlier exports. The JSON metadata and Markdown represent the same model-written proposal. The original trace and candidate digest remain authoritative. `scientific_validated` and `simulation_executed` stay false until a real downstream process supplies the corresponding evidence. Method-only proposals explicitly require real baseline and data before project execution.

`loop.reflection_thinking_enabled` independently configures the tool-free review call. Native action calls still use non-thinking mode until provider continuation support is implemented. Setting only `reasoning_effort` does not enable a provider's thinking mode. Each model-request trace records both settings. The configured Idea reviewer enables thinking with `low` effort, the recovery setting actually exercised in the real test; higher effort remains configurable. This remains model review, not experimental proof. The current [DeepSeek thinking documentation](https://api-docs.deepseek.com/guides/thinking_mode/) describes these separate controls and the extra continuation required when tools are present.

Idea's default input budget is 96,000 conservative UTF-8 byte-based token upper-bound units, matching the live evaluation setting. It includes the tool/submission schemas and leaves space for source excerpts and caller context. This number is not the provider's advertised token window. Required instructions and the current candidate cannot be silently dropped; exceeding the budget remains an explicit failure.

Run the public research evaluation with:

```bash
PYTHONPATH=.:backend:posttrain/src:projects/synthetic_regression/src \
python scripts/run_idea_lut_live.py --scenario configs/evaluation/idea_delivery_real.yaml
```

This scenario uses real DeepSeek calls, fresh memory, public paper retrieval, a human summary, typed handoff and isolated reviewer context. It has two public URL hints, so it is not an unseeded paper-discovery benchmark. The API key is read from environment or ignored local configuration and is never part of the proposal or source commit. No mock execution is permitted.

If an evaluation process vanishes while a model request is pending, use the original run with `--resume-run <run> --recover-abandoned`. The CLI must acquire its exclusive process lock, verify the full trace and preserve the original input, invocation and cumulative budgets. It archives the pre-resume state and leaves usage incomplete for the lost response. An unknown tool/batch outcome or an exhausted run cannot use this recovery path. Do not manually relabel checkpoints or clear counters.

An explicitly assisted revision of a finished run can reuse its real evidence with:

```bash
PYTHONPATH=.:backend:posttrain/src:projects/synthetic_regression/src \
python scripts/repair_idea_candidate_live.py <prior-run> <new-revision-directory> <bound-review.json>
```

The review must match the current prior candidate digest; the prior trace must be consistent with no pending operation, and source must be committed. This separate, bounded ReAct task has no research tools and at most four model calls. It preserves the original failed run and records `external_assistance=true`, `new_research_performed=false` and `model_review_passed=false`. A successful revision can export an exact validated handoff, but cannot be counted as autonomous end-to-end success. The [real evaluation report](evaluation/idea_delivery_20260907.md) includes both false model acceptance and externally assisted recovery.
