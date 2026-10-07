# MARS real product user journey

Status: **completed on 2026-10-08 for the bounded real user journey below**.
Commander, Idea, Experiment, ZCode Coding, Execution and Writing all ran through
the product, including manual review, final report approval and real results.
This is one exercised journey, not a repository-wide or commercial-readiness
certificate.

The journey started through the actual project chat with a new research task and
manual review. A bounded CPU learning-rate study was selected so real results can
be inspected without a long GPU training budget. Model/metrics/data and baseline
interfaces remain frozen. All model requests, tools, downloads and tests are real.

## Observed issues and verified corrections

| Issue | Correction |
| --- | --- |
| Historical metric/entrypoint assumptions contaminated Idea prompts | Require current source, metric units/direction/aggregation and actual CLI evidence |
| Revisions repeated successful research or lost protected requirements | Same-task provenance-bound source reuse; retain revision context without inherited acceptance |
| Confirmed stops lacked usable guarded retry | Explicit retry only after owned cleanup and no unknown effects |
| Human edits lacked evaluations or stale versions could be approved | Atomic new versions, exact-text evaluations, durable edit audit, stale-version rejection |
| Stage clocks and approval descriptions were misleading | Stage-specific timestamps and action-specific audit labels |
| Connected baseline/data config was not admitted as handoff context | Resolve actual allowed files and hashes; distinguish availability from content consumption |
| Backend PATH could not find configured Python | Resolve unresolved host placeholder after original allowlist check; retain actual argv |
| Equal step counts in different artifact fields were rejected | Canonical explicit-step aliases; conflicting units/counts still blocked |
| Test command IDs were guessed and failure output hidden by warnings | List configured IDs, actionable unknown-ID errors, retain output beginning and final summary |
| Diagnostic/config tests leaked shared configuration | Isolate and restore real config state; archive private diagnostics; 86 actual project tests passed |
| Candidate arrays had no incremental append | Explicit model-authored atomic append, preserving hash and whole-document validation |
| Quota failure was generic and hidden by stale recovery notice | Receipt-based 1113 explanation and fresh-status notice clearing |
| UI offered quota recovery but the loop treated explicit rejection as unknown | Match native rejection and settled budget receipts on explicit resume; preserve draft, phase and counters |
| Confirmed quota rejection retry discarded the unapproved draft | Bind reuse to the native rejection receipt; retain candidate/source index without inheriting review or reading acceptance |
| Coding handoff missed the approved experiment's baseline config | Admit its actual same-project pure path and SHA as availability evidence; still require real reading |
| Main-chat clocks counted recharge and review waits | Use each processing group's own start and first terminal/review boundary |
| Execution confirmation showed missing data and an inert epoch budget | Show effective group data, CPU/threads, update count, seed, config and host output parent |
| Coding wrote a full command into a pure entrypoint field | Specify script/config/budget fields in both prompt and rejection; keep invalid-input rejection |
| Writing lacked actual job directories and step evidence | Bounded read-only handoff of current-run/project job receipts, hashed files, real LR rows and loss ranges |
| Report citations could name nonexistent files | Validate current-run references and require actual metrics/batch sources when present |
| GLM JSON response mode stripped literal json/jsonl suffixes | For GLM-5.3 family use strict JSON text instructions without the faulty provider filter; retain parsing, schema, budgets and independent review |
| Writing retry lost prior human correction instructions | Bind retained feedback to the exact unapproved report hash, version, run and project; invalidate it on edit or approval |

## Evidence boundary

The first task reached approved Idea/Experiment and real ZCode file reads/writes,
then failed at its cumulative 100-call limit. Its failure and ledger remain intact.
A separate fresh UI task was created after fixes. It downloaded and read three
core papers and retrieved additional candidates. At 34/100 calls the provider
rejected independent review for insufficient account resources. After the user
restored resources, the same task continued with guarded retry; no budget reset,
approval injection or success substitute was used.

The completed task is `2026-10-07T1221_static_pimc_lr_acceptance`. Its final
product state is `completed`, all five stages are `done`, and cumulative charged
model requests are **86/100**, including failed/rejected attempts. Three separate
minimal SDK diagnostics investigated the provider's filename corruption and are
not represented as pipeline calls.

| Stage | Actual evidence |
| --- | --- |
| Commander | New task submitted through project chat; actual pipeline creation and status updates |
| Idea | Three core papers read; proposal validated and independently reviewed; human approval |
| Experiment | Approved two-group study, frozen interfaces and explicit 50-update budget |
| Coding | Actual ZCode reading/writing in a Git research branch; two new configs and one test file; 92 real tests passed (6 new, 86 existing) |
| Execution | Two fresh job IDs and UUID output directories, attempt=1, is_mock=false, dry_run=0, returncode=0; 50 actual updates each, seed=2026, CPU/threads=2, parameters=19264 |
| Lab | Actual TensorBoard event files and RES/APE/loss curves displayed; close/return navigation exercised |
| Writing | Actual job evidence supplied, bad citations rejected, corrections reviewed, final edited report schema/evaluations passed and approved in main chat |

| Group | RES dB (lower better) | APE dB (higher better) | Final training loss | Seconds |
| --- | ---: | ---: | ---: | ---: |
| accept_baseline50 | 23.76 | 1.43 | 0.007228 | 30.66 |
| accept_const6e4 | 23.19 | 2.00 | 0.006896 | 26.49 |

These are new jobs, not imported historical results. Raw summaries, 50-row
steps.jsonl files, progress histories, effective config snapshots, complete argv
receipts, logs and event files remain under the actual run. The final report's
11 structured chain references all resolve to that run. Its last human edit
corrected receipt-directory wording and distinguished approved core commands
from actual host-overridden argv; measured results were unchanged.

Baseline's 50 used LR values are 4e-4; StepLR sets the *next* LR to 2e-4 after
update 50, which lies outside this budget. The other group uses 6e-4 throughout.
This is a single-seed, short-budget, legacy-split descriptive comparison; it does
not establish statistical significance, generalization or a scientific target.

The continued fixes passed targeted checks: 30 quota-draft checks, 56 handoff
checks, 16 execution-confirmation checks, 5 adapter checks, 28 reporting/context
checks and 46 provider/protocol checks. These are separate targeted runs, not one
full-suite total. Pure frontend projection smokes and type checking also passed.

Delivered fixes have targeted regressions with real files, Git, runtime and archived
receipts; no model/tool success substitute was used. These checks do not prove that
the entire repository has no other bugs. The private journey journal records exact
run identities, local provenance, checkpoints, test evidence and screenshots.
Private data, project source, credentials and raw model prompts are not published
with this validation record.
