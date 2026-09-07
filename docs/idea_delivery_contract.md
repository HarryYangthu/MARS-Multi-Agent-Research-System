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

## Two audiences, one canonical proposal

`human_summary` is one paragraph, 1-2 sentences and at most 240 characters. It explains the concrete change and intended benefit without claiming an experiment that did not happen. It is not sufficient input to the Experiment agent by itself.

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

Native Idea runs submit their candidate through `mars_submit_document(metadata, body)`. This control function consumes no research tool budget and cannot be batched with research calls. It serializes the model's exact JSON fields into YAML frontmatter before the existing validators and review run; it cannot add defaults, fix missing definitions or invent evidence. The raw call and resulting candidate hash remain in trace. This avoids treating free-form preambles or YAML punctuation errors as research revisions. Other BaseAgent subclasses can opt into the same framework-neutral protocol.

## Progress

Idea writes concise messages for research actions, candidate generation, validation, review and stopping to `idea/progress.jsonl`. Bridge persists them in `agent_events` and publishes `agent.progress` on the existing `agent_state` channel. Progress carries no state-transition command; it cannot approve a node. The workbench EventLog displays the human message. Native visible model explanations are used when concise and Chinese; otherwise a factual Chinese action label is emitted. This label describes actual activity and is not a fabricated model rationale.

## Acceptance and artifacts

The generic ReAct action loop is retained. Optional Reflection now uses separate reviewer instructions with the task, actual source context, candidate and real observations. This is a separate model call using the configured provider, not an independent expert guarantee.

Successful runs write `proposal.md`, `proposal.json`, `summary.txt`, and `acceptance.json` in a fresh `idea/deliveries/<invocation>/<export-id>/` directory. Resuming an invocation preserves earlier exports. The JSON metadata and Markdown represent the same model-written proposal. The original trace and candidate digest remain authoritative. `scientific_validated` and `simulation_executed` stay false until a real downstream process supplies the corresponding evidence. Method-only proposals explicitly require real baseline and data before project execution.

Run the public research evaluation with:

```bash
PYTHONPATH=.:backend:posttrain/src:projects/synthetic_regression/src \
python scripts/run_idea_lut_live.py --scenario configs/evaluation/idea_delivery_real.yaml
```

This scenario uses real DeepSeek calls, fresh memory, public paper retrieval, a human summary, typed handoff and isolated reviewer context. It has two public URL hints, so it is not an unseeded paper-discovery benchmark. The API key is read from environment or ignored local configuration and is never part of the proposal or source commit. No mock execution is permitted.
