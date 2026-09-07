# Real-only migration: restoration and live-test follow-up

## Safety correction (2026-09-07)

The first cleanup removed too much existing test coverage. Its GitHub commit was
blocked and was never merged into main. With user approval, all 50 test files
changed by local commits 92fa9cc/e8b2250 have been restored to their exact contents
at 784fdf1. This restores 9,449 lines, including security, permissions, context,
provider and runtime test cases. Runtime Mock implementations remain removed.

Restoration is not a passing test result. Legacy tests using removed Mock modules,
service replacements or outdated success assumptions need individual migration.
Do not execute them as a real-agent evaluation, blanket-delete them, or claim
their prior results verify the rebuilt code. Preserve each security invariant
when replacing its dependencies with actual filesystem/process/service checks.

## Evidenced failures and changes

| Observed issue | Change | Verification boundary |
| --- | --- | --- |
| Live GLM repeatedly produced YAML that failed frontmatter parsing | Final action now carries native JSON metadata and Markdown body; host serializes YAML without filling fields | Pure round-trip tests, then separate real API evaluation |
| Multiple requested sources were silently limited to one | Download response reports requested, selected, skipped and unattempted sources | Pure batch accounting; live receipts remain authoritative |
| Ambiguous JSON keys or nonfinite numbers could pass JSON parsing | Reject duplicate keys, NaN/Infinity and overflowing floats | Negative parser tests |
| Interrupted pending model request could imply complete usage | Mark usage incomplete on cancellation | Pending-request charges cannot be reconstructed locally |
| Reproduction source could be an uncommitted runtime | Live CLI requires committed runtime/config files and records commit/tree | No secret is stored in source or reports |

Use `scripts/run_idea_lut_live.py` for the actual Zhipu evaluation. It requires
`ZHIPU_API_KEY` or hidden `--prompt-key` input. No fallback provider is used.
The initial failed run is `idea_lut_20260907T061411_399228`; preserve its original
trace. Source YAML/schema success does not establish scientific correctness or
real PIMC performance. The 1.2 parameter ratio is an evaluation assumption.

## Current focused checks

Run the real-loop contract and structured-final contract tests explicitly while
legacy tests are being migrated. These checks use actual pure functions and
temporary files, not mocked model or tool execution. They are not a substitute
for running the full suite after migration or for a successful live API run.

Do not merge main until relevant regressions and live validation are complete.

## Follow-up checkpoint

- Seven restored test modules have now been migrated individually. Gate 5 uses
  the actual registry, release checks use the actual filesystem/Git and real
  gitleaks when installed, and missing execution/model dependencies must fail.
- Selected schema, storage, loop and migrated tests: 320 passed, 3 skipped for
  missing gitleaks, zero failures (323 cases in the JUnit report). A separate
  three-case CPU/removed-simulator check also passed. This is not the full suite.
- `scripts/audit_real_test_migration.py` flags 15 remaining test files (24 known
  patterns) for manual migration. The heuristic does not prove other files are
  free of doubles and never deletes or automatically skips tests.
- Live run `idea_lut_20260907T073746_8b0556` was stopped by external-payload
  approval review before completion. Its last checkpoint records 11 logical
  model requests, 10 responses, 12 SDK attempts, 9 dispatched tools/observations,
  and one protocol repair. There is no final accepted proposal. The saved
  `running`/pending-model checkpoint is interrupted evidence, not an active run.
- Review also found artificial ripple applied to the legacy CPU loss curve in
  `pim_cancellation.py`. Removal and numerical regression are still pending;
  the new numerical test uses a full batch so it does not exercise that ripple.

## Resumed live evaluation fixes

- SDK attempt failure now leaves usage incomplete even when a subsequent retry
  succeeds. The durable ledger records both attempts; totals remain a lower bound.
- BaseAgent and IdeaAgent now agree on JSON metadata plus Markdown final output.
- Search schemas reject unknown/ambiguous query arguments before network access;
  PDF tool descriptions expose the one-source default and cached page-window reads.
- Research guidance asks the agent to draft after satisfying evidence requirements,
  and to justify further searches with a concrete missing definition or comparison.
- Zhipu explicitly honors disabled thinking for supported models and rejects it
  for GLM-5.3, whose official API requires thinking. Effort remains low in this
  test; per-request timeout is now 300 seconds for long proposal generations.
  Reference: https://docs.bigmodel.cn/cn/guide/capabilities/thinking (2026-09-07).
- 53 loop/parser/ledger/configuration contract checks passed; two changed
  provider/trace modules passed mypy in the Python 3.12 environment. These are
  preparation checks, not a successful live-agent evaluation.
