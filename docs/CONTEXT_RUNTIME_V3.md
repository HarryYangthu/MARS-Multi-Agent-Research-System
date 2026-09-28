# Runtime context v3

New runs freeze `configs/context.yaml:runtime_context` under `context/runtime_policy.v3.json`.
Runs with pre-existing native checkpoints use v2; an explicit resume never upgrades a checkpoint.
The frozen policy and source classification enter the native execution fingerprint.

## Assembly and reading

System/role/schema and effective AGENTS rules remain instructions. README and project knowledge
are reference messages. The original user request, bound task contract, and parent artifacts retain
separate identities. Role-specific code reading guidance and a bounded filename-only index are supplied;
source remains on demand through `code.repo_reader`, with hash and paging. The index does not claim
complete coverage and does not read datasets, weights or whole repositories. Read permissions,
project scopes and baseline protection remain enforced by the registry.

Profile allocations are soft targets, not quotas. Mandatory content wins over percentages. Unknown
upstream prose stays protected. Only explicitly configured background section headings may be
structurally offloaded; every other upstream section and all frontmatter remain exact. No model
is asked to guess which experimental parameters are safe to discard.

## Budget and compression

Count the complete messages and tool definitions once. The current estimator is a conservative
UTF-8 serialized byte upper bound, **not an exact tokenizer count**. Provider-reported input usage
is recorded separately when available. Effective input limit is the smaller of the configured input
budget and a supplied model context window minus output reserve and safety margin. Without a verified
window value (`agents.<role>.model.context_window`) the configured input ceiling remains in force; no provider window is guessed.

At 80% compact toward 65%. Restore already-compacted units first so resumed calls do not repeatedly
rehydrate or summarize old inputs. Priority: exact duplicates, old tool observations, completed history,
unneeded code/background, explicitly optional upstream background. Structural extracts are labelled
incomplete, never described as verified summaries. Protected material can exceed the target; exceeding
the hard ceiling fails before any model request. Tool exchanges remain paired, latest observation and
failures remain visible, and reviewer-required evidence cannot be replaced with a prefix or reference.

Originals live in hash-addressed `context/materials`. `context.read_material` validates the hash,
rejects path escapes and symbolic links, and reads bounded character windows in the same run. Native
full traces persist compaction state at the model boundary. Metadata/off traces do not offload originals.
Semantic abstractive summarization is deliberately not used: structural extraction does not invent
conclusions, and active upstream requirements remain lossless.

## Runtime records and UI

Native act/repair/review, delegated literature research and debate use the common packer.
Delegated authors and reviewers receive the project reference material as data, separately from project rules.
Additional runtime read tools are authorized by a host-created read scope, validated against the effective
tool configuration; this does not grant writes or bypass project boundaries. New Commander sessions retain the original
dialogue instead of destructive message-count rollups, and use the same packer before each call.
Legacy sessions keep their recorded version. Actual outgoing messages and tool schemas are hashed
and archived; manifests include component counts, selection/compaction decisions and provider usage.
The context workbench shows these records. Planning previews reuse the assembler and packer but are
explicitly labelled **not sent**: they lack future observations and runtime-specific role/schema extensions.
They do not invent downloadable references or claim an actual provider request.

## Validation

Pure contracts and real temporary-file tests cover trigger boundaries, protected overflow, tool-pair
integrity, failure/review evidence, paging, tamper/path checks, exact payload hashes, and snapshot restore.
Live provider/tool checks are a separate acceptance layer; they do not prove scientific research quality
or successful PIMC simulation. Research baselines, credentials and run originals are never committed.

### Verification recorded on 2026-09-29

- Backend + synthetic-project full regression: 3,526 passed, 181 skipped (external archives,
  network, target-platform conditions or optional binaries were unavailable); no failures.
- Follow-up targeted checks cover delegated background carriage, effective readback authorization,
  debate accounting and explicit model capacity. No provider/tool substitutes were used.
- Strict mypy: 656 source files; frontend typecheck, context smoke and production build passed.
- Browser preview at port 3012 displays material rows, component costs and “planning preview / not sent”; no page errors.
- Real-provider diagnostics are run by `scripts/verify_context_v3_live.py`. The script creates local
  diagnostic inputs, invokes the configured Idea model and registered tools, interrupts after the
  actual file read and resumes the same checkpoint. Its receipt distinguishes mechanism validation
  from scientific acceptance. Failures remain failures; they are not converted to example outputs.
- Final targeted regression after the latest-observation fix: 213 passed, 16 skipped.
- Real GLM-5.3 mechanism check passed: 4 model requests, 2 successful real tool dispatches,
  1 schema correction, 3 compaction events and 4 hash-verified outgoing manifests. The run was
  interrupted after the second tool and resumed with the same counters; total tool dispatches
  remained 2. The final nonce matched the actual local source file. All response model identities
  were consistent. Reported usage: 18,133 input + 1,027 output = 19,160 tokens.
- Initial conservative input upper bound shrank from 31,427 to 18,113 against a 28,000 input
  limit (65% target: 18,200). A later restored call at 18,755 stayed below the 80% trigger and
  correctly avoided another compaction. These byte-based bounds are not provider token counts.
- The live diagnostic exposed and fixed a receipt-index ordering bug: the latest tool observation
  must stay protected independently of the latest history/receipt index. This prevents an immediate
  readback loop caused by compacting the observation before the model can use it.
- Earlier diagnostic attempts encountered denied readback (fixed), provider timeout, invalid final
  format and the ordering bug above. They were not counted as successful validation. The successful
  diagnostic uses shared BaseAgent assembly with the Idea identity, not full scientific Idea acceptance.
  Local receipts, source inputs, actual traces and browser capture are retained under
  `local/verification/context-runtime-v3/2026-09-29/` (ignored by Git).
