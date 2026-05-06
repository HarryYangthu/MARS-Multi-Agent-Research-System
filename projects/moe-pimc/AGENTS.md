# projects/moe-pimc/AGENTS.md — MoE-PIMC project rules

This file is the single source of truth for **project-level baseline protection**.
Gate 5 (`harness/gates/baseline_compatibility.py`) reads it on every tool dispatch
that could mutate the workspace and **blocks** any change that violates the rules
below.

## 1. Baseline class is frozen

The class `Paper_Total_0327` in `libs/Model.py` is the production baseline.
Any modification to its **method bodies** or **constructor signature** is
forbidden without explicit human approval.

Pattern: `libs/Model.py:Paper_Total_0327`

## 2. `forward(x, stream_label)` interface is frozen

All routing modules under `libs/` must keep the `forward(x, stream_label)`
signature. Adding new parameters is allowed only via keyword-only args with
defaults; positional reordering is forbidden.

Forbidden tokens (regex): `def forward\(\s*self\s*,\s*x\s*,\s*[^,]+,\s*[a-zA-Z_]+\s*[,)]`
when the third positional arg is **not** named `stream_label`.

## 3. `production_interface/` is read-only from MARS

No tool dispatched from MARS may write to `production_interface/`. This
includes `code.patch_generator`, `code.write_file`, and `code.delete_file`.

Pattern: `production_interface/**`

## 4. Baseline directory is frozen

Any change to files under `baseline/` is blocked. Use `baseline/` as a
read-only reference for ablations.

Pattern: `baseline/**`

## 5. Tensor shape comments

(Style rule, not Gate-enforced.) Every tensor op in research code must have
a shape comment immediately before/after, e.g.:

```python
# x: (B, T, D)
y = self.proj(x)            # (B, T, D')
```

This rule is documented in `templates/code_rules/pimc_python.md`.
