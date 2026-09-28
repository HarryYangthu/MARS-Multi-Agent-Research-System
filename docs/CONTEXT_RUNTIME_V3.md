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
window value the configured input ceiling remains in force; no provider window is guessed.

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

Native act/repair/review and debate use the common packer. New Commander sessions retain the original
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
