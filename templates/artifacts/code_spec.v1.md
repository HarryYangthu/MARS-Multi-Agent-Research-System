---
schema: code_spec.v1
project: moe-pimc
agent: coding
upstream_artifact: experiment_plan.approved.md
target_lang: python
baseline_compat:
  preserved: true
  rationale: "forward(x, stream_label) signature unchanged; new Paper_Router_v2 added alongside existing Paper_Total_0327."
files_changed:
  - path: "libs/Model.py"
    type: modified
    risk: medium
  - path: "tests/test_router_v2.py"
    type: added
    risk: low
new_dependencies: []
test_coverage:
  unit_tests_added: 3
  baseline_smoke_test: pass
---

# Code spec

Describes the patch.
