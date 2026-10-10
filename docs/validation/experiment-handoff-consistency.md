# Experiment, coding and execution consistency

The failure came from accepting independently valid documents without checking
their shared executable meaning. An experiment could leave its seed unresolved;
coding could describe commands in prose, omit runtime bindings, or turn optimizer
steps into epochs. Runtime settings and the execution YAML could select different
adapters. The batch runner also forwarded a global default instead of each job's
approved budget.

## Admission rules

- Experiment delivery requires a concrete nonnegative integer seed, explicit
  budget unit and count, unique output names and an exact matrix count.
- Coding delivery binds the full approved experiment document by a host-computed
  SHA-256, supplies one executable binding per experiment, and preserves approved
  parameters. Updating an approved document invalidates older coding delivery.
- The installed adapter checks the actual entrypoint and configuration file.
  Paper-static validates the actual YAML/overrides seed and epoch capacity;
  optimizer steps cannot be silently interpreted as epochs. The CPU step adapter
  likewise rejects an epoch protocol.
- Candidate submission, durable human/automatic approval and downstream admission
  share the same checks. Incomplete historical artifacts remain readable but do
  not bypass new admission rules. Semantic approval failures return HTTP 422 with
  actionable issues; stale review requests return HTTP 409 before approval.
- Explicit runtime environment configuration takes precedence over the execution
  YAML; otherwise the YAML supplies the backend. Every consumer sees the same
  effective backend and its source. Overridden declarations produce a warning.
- Managed job fingerprints and actual command requests use each job's explicit
  budget. Changing an unrelated global default reuses verified completed jobs.
  The direct command tool applies the same rule.
- The actual execution list must equal the merged approved matrix and coding
  bindings. Human edits and legacy execution plans cannot change parameters or
  command bindings while presenting an otherwise valid upstream handoff.

## Verification

The regression scope covers handoff consistency, deterministic execution intake,
configuration confirmation, actual numerical local-command measurements, persisted
receipts, replay protection, stopping and recovery, code approval, artifact storage,
research handoffs, settings and read-only history. Strict Python typing and frontend
TypeScript checking are required. Tests use real documents, files, host environment
configuration and real subprocesses; no provider or execution success is fabricated.

The isolated publishable source passed **160 tests, with 1 environment-dependent
skip**, strict mypy for all **15 changed Python source files**, and frontend
TypeScript checking. Actual numerical commands produced measured MSE values and
hash-bound receipts. Per-job budgets differed from the global default, and changing
that global default reused the existing verified receipts without a second launch.
The confirmation tests also rejected a changed approved document before creating
any command directory, confirmation receipt or batch result.

Reproduce the focused regression with:

```sh
PYTHONPATH=backend python -m pytest -q -o addopts='' \
  backend/tests/unit/test_experiment_handoff_consistency.py \
  backend/tests/unit/test_execution_plan_contract.py \
  backend/tests/unit/test_execution_confirmation.py \
  backend/tests/unit/test_local_command_pipeline.py \
  backend/tests/unit/test_paper_static_adapter.py \
  backend/tests/unit/test_run_recovery.py \
  backend/tests/unit/test_coding_approval.py \
  backend/tests/unit/test_owned_run_cancellation.py \
  backend/tests/unit/test_run_state_store.py \
  backend/tests/unit/bridge/test_orchestrator_readonly_history.py \
  backend/tests/unit/test_artifact_store.py \
  backend/tests/unit/test_research_handoff.py \
  backend/tests/unit/test_settings_env.py \
  backend/tests/unit/test_execution_tools_v2.py
```

An additional existing Idea submission suite has failures related to missing
literature-coverage fields. The unchanged parent source reproduced the same
**16 failures and 24 passes** in `test_submission_schema_contracts.py`; this is a
pre-existing test-fixture/schema inconsistency, not evidence of a passing full
repository suite. It is outside this execution-handoff fix.

## Limits and existing research

This change prevents the reproduced inconsistency classes from being accepted and
advanced. It does not establish scientific effectiveness or guarantee that every
future defect is impossible. A registered arbitrary external command still needs
project-specific implementation/measurement checks to establish that it implements
the approved method.

The existing StaticPIMC task's approved 50-step protocol is preserved. Its 50-epoch
coding configurations are not silently substituted for that protocol; the legacy
handoff must be repaired before training. No new PIMC training, model API call or
2 dB improvement is claimed by these validation tests.
