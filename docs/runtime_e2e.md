# Actual five-stage runtime verification

`scripts/run_runtime_e2e.py` drives the normal `POST /api/runs` → start → status path for Idea, Experiment, Coding, Execution and Writing. It does not use seed artifacts, inject model answers, approve gates, or replace failed tools with examples. Run it beside the backend with the same checkout, project registry and environment; its final evidence verification reads that backend's local `runs/<run_id>`.

First inspect actual configuration without starting a model or a simulation:

```bash
PYTHONPATH=.:backend python scripts/run_runtime_e2e.py --output /tmp/mars-runtime-preflight.json
```

With no task file, this intentionally reports missing task, baseline and data. Provider checks disclose credential variable names and configured model names, never secret values. Configuration presence does not prove that a remote endpoint is reachable; the actual run verifies that.

For a reproducible small task with all local inputs prepared, use:

```bash
PYTHONPATH=.:backend:projects/synthetic_regression/src python scripts/run_runtime_e2e.py --prepare-regression workspace/runtime_e2e_regression_demo --output /tmp/regression-preflight.json
```

This creates and registers a folder project, copies the public pack's real numeric samples, preserves `baseline/baseline.py`, and initializes `candidate.py` with the same degree and regularization. No improved answer is supplied to any Agent. The actual execution command in this same script independently fits both models with the pack's ridge solver and measures held-out errors. A zero improvement for the initial identical models is a real expected measurement, not an Agent success. The generated task constrains edits to the candidate's two parameters and prohibits editing the baseline, data, split or evaluator.

The preparer declares `improvement >= 1e-12` in the folder project's `.mars/diagnostics.yaml` before execution, with one experiment iteration. Folder projects load diagnostics from this metadata directory, not from the research repository root. Inspect the resulting `diagnosis/diagnosis.v1.md`: both `metrics_evaluated` and `passed` must be true to claim this numerical criterion passed. This engineering criterion does not establish scientific novelty or blind generalization: Agents can inspect all 11 samples. The host's code checker verifies syntax, allowed parameters and protected inputs, and explicitly does not execute the experiment.

The preparer also creates `workspace/runtime_e2e_regression_demo-host/` outside the candidate repository. It contains real execution/tool configurations with the exact command allowlisted and an `environment.json` with only host configuration paths, the execution backend and the explicitly scoped research network settings. These generated files are local runtime inputs and are ignored by Git; the preparer and evaluator code are versioned here. Existing fixture directories are never overwritten. The generated `runtime_e2e.yaml` is the reusable task configuration.

Start the backend with the same host configuration (and actual model credentials in its environment):

```python
from pathlib import Path
import yaml
import uvicorn
from scripts.run_runtime_e2e import E2EConfig, configure_host_environment

task_file = Path("workspace/runtime_e2e_regression_demo/runtime_e2e.yaml").resolve()
config = E2EConfig.model_validate(yaml.safe_load(task_file.read_text()))
configure_host_environment(config, task_file.parent)
uvicorn.run("app.main:app", host="127.0.0.1", port=8000)
```

Then use the generated task with `--execute`. Without a configured model endpoint or credential it returns `required_dependency_missing` with `failure_code: model_configuration_missing` before creating any run. Baseline/data preparation is therefore not an external blocker for this engineering task.

Create a task YAML using real files and preserve the original research constraints:

```yaml
schema_id: runtime.e2e.v1
project: pimc
task: my-real-research-task
user_request: "Write the actual research request and all constraints here."
api_url: http://127.0.0.1:8000
baseline_file: /absolute/path/to/actual/baseline.py
data_path: /absolute/path/to/actual/data.npy
data_description: "Describe the actual shape, units, signal roles, sampling frequency and train/test split."
context_files:
  background: /absolute/path/to/domain-background.md
approval_mode: human
idea_scope: project_proposal
require_network: true
network_allowed_domains: [arxiv.org, proceedings.neurips.cc]
timeout_seconds: 1800
```

The project `repo_link.yaml` must already point to the actual editable research repository. Configure the legitimate model endpoints/credentials, execution adapter, network enablement and host allowlist in the backend. The driver compares requested research hosts against the existing allowlist; it does not expand access. `data_source_id` can name an already uploaded dataset; its checksum must match `data_path`. Otherwise `--execute` uploads that exact configured file through the normal data-source API.

```bash
PYTHONPATH=.:backend python scripts/run_runtime_e2e.py --config /path/to/task.yaml --output /tmp/mars-runtime-preflight.json
PYTHONPATH=.:backend python scripts/run_runtime_e2e.py --config /path/to/task.yaml --output /tmp/mars-runtime-e2e.json --execute
```

Human approval remains the default. Review and approve through the running MARS UI/API while the driver waits. Only an explicitly configured `approval_mode: auto` requests the existing development auto-approval policy; production rejects it. Tool and system gates still apply. A waiting gate or deadline is `blocked`, and the backend retains ownership of the run after the driver exits. Reattach through normal UI/status/resume operations; do not rerun the driver and confuse a new run with recovery.

Optional `idea_requirements`, `evaluation_policy` and `selected_skills_by_agent` are forwarded as task policy without relaxing them. No evaluation result means no scientific success claim. The driver distinguishes:

| Status | Meaning |
| --- | --- |
| `required_dependency_missing` | Actual model, task, code/data, execution, network or API prerequisite is absent. |
| `ready` | Offline configuration checks passed; no model/tool/experiment was executed. |
| `blocked` | The live run awaits approval/feedback or exceeds the driver's waiting deadline. |
| `failed` | A real stage/API failed, or a reported completion lacks the required execution evidence. |
| `completed` | All five stages completed with actual model responses, approved schema-valid artifacts, tool observations and actual experiment records. |

`completed` verifies the execution and artifact contracts. The report keeps `scientific_validated: false`: measured improvement against the real research acceptance criterion still requires the experiment's own controlled comparison and metric evaluation.

The public synthetic-regression pack normally belongs to the separate Discovery path. The preparer above connects its numeric data and real solver to the five-stage pipeline through the request-bound local-command protocol; it does not claim PIMC validation. The `pim_cpu` example generates its own signals, so this driver rejects it for a task requiring a configured external dataset. Neither verifies the user's real PIMC capture.

Resource limits come from `configs/resources.yaml`: parent and child loops share request, token, elapsed-time and model-concurrency budgets. A configured `max_cost` requires actual per-million-token prices for the selected provider/model; missing prices reject the call. A stale in-flight request is not silently reissued. After checking what actually happened, call `RunModelBudget(run_root).reconcile_abandoned(request_id, actor=..., reason=..., evidence_refs=...)` with the human reconciliation evidence before continuing.

The default Coding backend is `native_llm`; Writing uses the native Reflection loop so both stages share tool validation, checkpoints, budgets and trace evidence. OpenCode's own model requests cannot enforce these host limits, so its adapter is rejected inside a governed run before touching the workspace. It remains available for explicit standalone diagnostics. Optional debate preserves complete supplied evidence and rejects context-budget overflow instead of silently shortening handoffs.

New loop checkpoints use context format 9 and bind author/reviewer generation settings and endpoint identities. Older checkpoints require their original runtime for recovery; this release rejects them rather than automatically migrating or replaying unknown work.
