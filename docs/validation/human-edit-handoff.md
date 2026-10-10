# Human editing and experiment handoff validation

A real product journey exposed three failures after an Experiment draft reached review:
configuration paths included prose, saving a human edit left the new version without
evaluation reports, and the stage clock included all upstream research time.
Review audit projection also mislabeled approvals, edits and comments as rejections.

The edit path now allocates a new version under the artifact lock, writes atomically,
and evaluates that exact version. Invalid drafts remain visible with failing reports;
they cannot advance the approved pointer. Edits without an in-memory review session
still produce audit and evaluation events. Approval after a backend restart rejects
a stale numbered version. The schema is read from parsed frontmatter, independently
of YAML field ordering.

Experiment configuration file fields contain plain paths, with matching `cfg` and
`config_path` aliases when both are present. Descriptions belong in `role`. Registered
command handoffs remain supported. Prompts require verbatim output filenames from
the actual entrypoint. Agent worklogs use that Agent's first valid timestamp; a stage
that has not started does not borrow the whole run's clock. Review actions retain
their distinct meaning and next action.

Validation: 41 tests passed across human editing, stage timing, existing timeline and
experiment handoff checks. They use authored documents, real temporary files,
concurrent file writes and API admission, without model or tool substitutes.
The actual rejected draft's three malformed paths are rejected by the new check;
its revised path bindings pass. Actual UI saving produced a new version with all
three evaluation reports, and the stage duration changed from upstream-inflated
230 minutes to its own observed stage span. The full research journey remains in
progress; this record does not claim simulation or report completion.

Run checks with `PYTHONPATH=backend .venv/bin/python -m pytest -q` on:

- `backend/tests/unit/test_human_edit_evaluation.py`
- `backend/tests/unit/test_worklog_stage_timing.py`
- `backend/tests/unit/test_timeline_v2.py`
- `backend/tests/unit/test_experiment_handoff_consistency.py`
