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
