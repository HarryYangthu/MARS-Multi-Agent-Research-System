# Task Context: PIMC Static Algorithm Research

This context profile is for the current end-to-end test only. The current MARS
instance is a PIMC research workbench, and this file narrows the run to the
static PIMC algorithm study. Future users can swap project context to adapt the
same workbench pattern to other research domains.

## Active Scope

- Project: `pimc`
- Research mode: static PIMC algorithm research
- Execution backend: `paper_static`
- External code entry: `train_static.py --cfg configs/static.yaml`
- Data/config source: `projects/pimc/repo_link.yaml` and `configs/execution.yaml`
- Primary goal: inspect whether the current static PIM cancellation setup can
  improve residual metrics under the configured real static capture.

## Required Interpretation

- Treat `projects/pimc/context/public_context.md` as the PIMC project context
  for this workbench, while this file is the task-specific static-study slice.
- Preserve original paper metrics from the external code:
  `PIM`, `paper_RES_db`, and `paper_APE_db`.
- When using MARS compatibility metrics, state the mapping explicitly:
  `RES = -paper_APE_db` and `loss = 10 ** (-paper_APE_db / 10)`.
- Do not describe `paper_APE_db` as an angle, degree value, or phase error.
- Do not narrow the task to router/MoE/expert-count changes unless the active
  static code/config actually consumes those knobs and the user asks for them.

## This Test Should Produce

- Schema-valid artifacts for Idea, Experiment, Coding, Execution, and Writing.
- A visible run directory under `runs/<timestamp>_<task>/`.
- Context manifests showing that this task profile and the PIMC project context
  were loaded for the run.
- Real execution logs and metrics from `paper_static` when the external code,
  data, Python interpreter, and API keys are available.
