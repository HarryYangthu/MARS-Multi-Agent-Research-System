# Real coding and execution admission regressions

During a real product journey, ZCode successfully read and wrote project files,
but two host defects caused unnecessary repair loops:

- Host-configured `python` check commands could not start in a non-login PATH.
  Explicit executables remain unchanged. An unresolved `python` placeholder now
  resolves to the running Python runtime after the original command allowlist
  check; receipts record the actual argv.
- Experiment artifacts use `budget_steps`; Coding's schema offers an explicit
  `budget_unit` plus `max_iters`. Equal integer counts with explicit `steps` are
  now accepted as aliases. Different values or epoch/step unit conflicts remain
  blocked. Step-budget admission does not demand unrelated legacy epoch fields.

A baseline data configuration can now supply its actual named data-description
source. This is a configuration receipt, not a dataset-reading claim; execution
confirmation still verifies availability and actual overrides before jobs start.

Confirmed stops of Coding now expose the existing guarded explicit retry from
recovery UI. Unknown writes, mismatched task identity, missing branch ownership,
and unresolved model requests remain blocked. No automatic effect replay or
budget reset was introduced.

69 existing and new targeted checks passed with the real Git binary and real
local files, plus one real branch-preparation regression for stopped Coding.
One earlier run failed solely because its shell selected the Xcode license-gated
Git wrapper; rerunning with the same Command Line Tools PATH as the backend passed.
No model, tool, or external service success was substituted.

The fresh journey restarted Coding through the actual UI. Full-flow simulation
and report acceptance remain separate evidence, recorded in the final journey report.
