# MARS Project — Interview Script (English)

> MARS — Multi-Agent Research System.
> For interview delivery. Suggested: 2-min "what + why" opener, then go deep on whatever the interviewer probes.

---

## 0. Elevator pitch (memorize)

> I built **MARS, a multi-agent research system** that turns the scientific workflow — *research question → literature → hypothesis → experiment design → coding → simulation → paper* — into a pipeline of **5 domain agents plus 1 conversational master agent (Commander)**, governed by a **schema-as-spine + human-in-the-loop + self-healing feedback** substrate. A researcher drives the whole loop, from idea to paper, with a single natural-language sentence. Full-stack, ~17k lines of code, backend passes `mypy --strict` with zero errors, dependency direction enforced in CI, 200+ tests. The first real domain is my own research area — **Passive Intermodulation (PIM) cancellation for FDD Massive MIMO**.

---

## 1. Background: why I built it

**Pain point**: I'm an algorithm researcher in wireless signal processing (PIM cancellation). A research cycle (survey → design experiments → modify baseline code → run ablations → analyze → write paper) takes weeks to months, with heavy repetitive work.

**My thesis**: LLM agents can already handle many of these steps, but off-the-shelf general agents (AutoGPT-style) have three fatal flaws that research scenarios can't tolerate:
1. **Uncontrollable** — they run a long black-box chain with no way to inspect/intervene on intermediate artifacts;
2. **Untrustworthy** — output format drifts, so downstream can't consume it;
3. **Non-reproducible** — nothing is fully captured, so you can't replay or attribute failures.

**My goal**: not "a smarter agent," but a **substrate (harness)** that makes agents **controllable, trustworthy, and reproducible** for research. North-star metric: compress a research cycle from months to weeks.

---

## 2. What I built (overview)

I implemented the entire system **solo, full-stack, from scratch**, in a 5-tier architecture:

| Tier | What | Key files |
|---|---|---|
| **1 Web workbench** | Next.js 15 three-column console: Commander chat + 5-tier pipeline viz + event log/KB | `frontend/src/` (4.7k lines) |
| **2 API + Bridge** | FastAPI + WebSocket; Bridge is the product-orchestration layer that knows the agents | `api/`, `bridge/` |
| **3 Five domain agents + master** | Idea/Experiment/Coding/Execution/Writing + **Commander** | `agents/`, `bridge/commander*.py` |
| **4 Harness (trust mechanisms)** | Schema / Tools / Gates / Context / LLM / KB / Sedimentation / Runtime | `harness/` (the core) |
| **5 Storage & projects** | Full run sedimentation + 4-zone KB + project metadata | `storage/`, `knowledge/`, `projects/` |

**Scale**: backend 125 Python files / 12.7k lines; frontend 21 TS/TSX / 4.7k lines; 44 test files, 200+ tests.

**Real data**: ingested my own real PIMC research code (139 chunks) + 20 PIM-domain papers (517 chunks) into the KB — 852 chunks total.

---

## 3. Core problems I solved (interview centerpiece — each is a "story")

### Problem 1: Making LLM output machine-consumable — Schema as the spine

**Problem**: LLM output is free text; when 5 agents form a pipeline, downstream can't reliably parse upstream artifacts.

**My solution**: a uniform artifact format — **"Markdown body + YAML frontmatter"** — where the frontmatter must validate against a **JSON Schema** (`proposal.v1` / `experiment_plan.v1` / `code_spec.v1` / `run_log.v1` / `report.v1` / `diagnosis.v1` — 6 types). **Core principle: a human-written md and an agent-written md are fully equivalent downstream** as long as the schema passes.

**Technical details**:
- `validator.py` returns a **structured error list** `[{path, message}]` so the UI can pinpoint missing fields;
- handled the gotcha where YAML auto-parses ISO timestamps into datetime objects but JSON Schema expects strings (wrote a `_to_jsonable` normalizer);
- DeepSeek often wraps the doc in ```markdown fences or adds a preamble — I wrote `_unwrap_llm_text` to strip both;
- **embedded the schema's reference template into the system prompt** as a few-shot exemplar → first-pass compliance ≥95%.

**Why it's notable**: this brings the software-engineering idea of a **contract** into an LLM pipeline — schema as the ABI between agents, decoupling "who produces" from "who consumes."

### Problem 2: Agents auto-advancing with no review — two-layer HITL

**Problem (a bug I actually hit)**: when LLM output failed schema, it was silently dropped; the orchestrator saw "no artifact" and skipped to the next node — so it looked like "the agent finished and jumped straight to the next, with nothing to review."

**My solution**: **two layers of HITL**:
1. Every agent output enters a **review session** (high-frequency): edit / approve / reject, with versioned artifacts `v1/v2/approved`;
2. **5 system Gates** (sparse) hard-block critical points.

**The three fixes for that bug** (being able to narrate the debug is a plus):
- agent_runner: **persist v1 even when schema fails** (append errors as a comment) so HITL can see it;
- orchestrator: on "no artifact," transition to **FAILED (halt)** instead of silently skipping;
- made approval prominent — purple banner + big Approve button + pulsing `🔔 N waiting` badge in the top bar.

### Problem 3: Gate 5 can't be a flow node — hook it onto the tool-dispatch path

**Problem**: one gate (baseline_compatibility) must fire "whenever any agent calls a tool that would break the baseline" — it's not a checkpoint in the flow, it's cross-cutting across all tool calls.

**My solution**: hook Gate 5 **into `harness/tools/registry.py`'s `dispatch()` path** — every tool call is screened first, reading the project's `AGENTS.md` protected_paths + regex (e.g. protecting the `forward(x, stream_label)` signature, the `baseline/` dir, production classes); violations block. This is an **aspect-oriented (AOP)** approach rather than scattering checks across flow nodes.

### Problem 4: What if results miss the target — self-healing feedback loop

**This is the most "research-system" part.**

**Problem**: the RES metric misses target; a human has to judge "did coding break it, or are the experiment params wrong," then fix and rerun.

**My solution**: a **closed self-healing loop**:
- Execution finishes → **diagnosis node**: a rule engine (`diagnostics.py`) does **automatic attribution** via three analyzers: `metrics_gap`, `config_sanity` (config issue → suspect experiment), `code_change_risk` (code risk → suspect coding);
- `BridgeAgent` decides which stage to pull back to + budget control (`max_iterations`);
- **orchestrator dynamically appends a node chain to the RunGraph**: `coding#2 → execution#2 → diagnosis#2 → writing`, attempt+1, forming a loop until target is met or budget runs out.

**RunGraph is a generic DAG with no hard-coded linear topology** — the linear order lives in `workflow_service.py`, and the graph can be mutated at runtime; that's what makes the self-healing loop possible.

### Problem 5: Drive everything with one sentence — Commander + dual-layer FSM

**My most recent work, and the best showcase of orchestration skill.**

**Problem**: the 5 agents are a pipeline, but the user still had to pick an entry and fill a form. I wanted a conversational "commander."

**My solution**: a **Commander master agent** (DeepSeek-driven) in the Bridge layer:
- **Two interlocking state machines**: an upper **7-state conversation FSM** (idle→clarifying→planning→awaiting_confirm→executing→awaiting_review→reporting), and the lower pipeline node FSM (reused);
- **Intent routing**: user says "I already have an idea, just validate it" → Commander **skips Idea Agent and enters at Experiment**;
- **Function-calling via JSON-ReAct**: instead of provider-native tool-use, the LLM emits a **strict JSON decision** `{reply, next_state, actions}` and I run a **ReAct loop** (execute tools → feed results back → decide again). Benefits: provider-agnostic, works under mock, more controllable;
- **Doesn't rewrite the engine**: Commander is the "brain"; it reuses the existing orchestrator + self-healing engine as "hands" via tools — clean separation.

**Live result**: one sentence — "run an ablation on PIMC router simplification comparing expert_count vs RES, all the way to a paper" — and Commander **autonomously completes in ~4 minutes**: idea (debate) → experiment (auto-designs a memory×lr ablation grid) → coding → execution (real PIM sim) → writing (debate), all 5 stages done.

### Problem 6: Demoable with zero dependencies — triple mock fallback

**Problem**: it must run a full demo on an interview/CI machine with no GPU and no API key.

**My solution**: three auto-degradations:
- `mock_provider`: when no API key, returns **schema-compliant placeholders** (not random text — passes validation);
- `mock_simulation`: synthesizes a loss curve when no GPU;
- `mock_debate`: degrades when providers are insufficient.
- **Auto-degrade logic**: debate switches among `real_multi_model / single_model_simulated / mock_debate` based on available providers.

This guarantees the **full 11-step demo runs with zero external dependencies** — a hard V0 acceptance criterion.

---

## 4. Real physics simulation (domain depth — what separates this from a "wrapper")

An interviewer may challenge: "isn't this just wrapping an API?" My answer: **the Execution Agent runs a real physics simulation.**

**What I did**: I wrote a **physics-faithful but CPU-second-scale** dual-carrier PIM cancellation simulation (`execution/pim_cancellation.py`):
1. **Dual-carrier signal**: `x = A1·e^{j2πf1n} + A2·e^{j2πf2n}`, 30k complex baseband points;
2. **Odd-order intermod PIM with memory effects**: memory-polynomial nonlinearity; 3rd-order intermod lands at 2f1−f2;
3. **Canceller**: memory-polynomial, fit by gradient descent;
4. **Key engineering pitfall**: dual-carrier memory-polynomial basis functions are highly collinear → ill-conditioned Gram matrix → plain gradient descent **diverges (loss explodes to 1e86)**. I used **QR orthogonalization** to orthonormalize the basis and do gradient descent in the orthogonal space — guaranteeing well-conditioned, monotone convergence;
5. **Physical correctness**: RES differentiates by canceller memory depth — memory=2 → −15dB (can't cancel cleanly), memory=8 → −30dB (hits the noise floor). **Insufficient memory depth → incomplete cancellation — exactly the real physics of PIM cancellation.**

**Why it matters**: it proves the system isn't "running experiments in words" — there's real domain physics inside, and the Experiment Agent can **autonomously design** a memory×lr ablation grid that Execution runs to produce differentiated RES. Hooks are in place: on real 4×L40S + real `.mat` data, swap this branch for a subprocess that trains the real 7-layer model.

---

## 5. Cutting-edge tech / my own innovations

### Cutting-edge (industry-standard)
- **Multi-agent orchestration** (akin to Anthropic's multi-agent research, AutoGen/CrewAI);
- **RAG / KB**: 4 independent zones (literature/methodology/code/run-archive), semantic retrieval + sedimentation loop;
- **Multi-model debate**: 3-model × multi-round + critic synthesis (DeepSeek-V3 chat as proposer, DeepSeek-R1 reasoner as critic — cross-model-version);
- **Function-calling / ReAct**: the Commander loop;
- **HITL**: per-node review + system gates;
- **Multi-backend LLM abstraction**: Anthropic/OpenAI/Qwen/Gemini/DeepSeek/local-vLLM unified interface.

### My own innovations (lead with these)
1. **Schema-as-spine**: contracts in an agent pipeline — "human md = agent md" — fully decoupling production from consumption;
2. **Aspect-style Gate 5**: not a flow node, but hooked onto the tool-dispatch path, cross-cutting all tool calls;
3. **Rule-based attribution + dynamic pull-back self-healing loop**: Execution misses → auto-diagnose the culprit → mutate the DAG to pull back & rerun → budget-controlled;
4. **Dual-layer FSM + conversational master**: conversation FSM interlocks with pipeline FSM; Commander reuses (not rewrites) the engine via JSON-ReAct;
5. **Triple mock fallback with schema-compliant mocks**: zero-dependency demoable; even mock artifacts pass schema — downstream can't tell real from fake;
6. **QR orthogonalization to fix the ill-conditioned PIM sim**: numerical linear algebra applied in the agent system's execution backend.

---

## 6. Engineering capability

- **Architectural discipline**: 5-tier **one-way** dependencies, enforced by `import-linter` with **4 CI contracts** (harness must not import upward; bridge must not import concrete agents — dependency inversion via a registry; agents must not import bridge/api; overall layering). Turning architectural constraints into executable CI checks is something many overlook;
- **Type safety**: `mypy --strict` clean across all 125 files;
- **Tests**: 200+ covering schema compliance (≥95%), all 5 gate triggers, baseline-match recall/precision, multi-experiment concurrency, end-to-end demo;
- **Observability**: every run fully sedimented into `runs/<id>/` (9 subdirs), fully replayable/auditable/attributable, plus a TraceRecorder for spans;
- **Dependency inversion**: Bridge looks agents up via `agent_registry` (structural Protocol typing), never importing implementations;
- **Graceful degradation**: API retries + mock fallback without corrupting run state;
- **E2E verification**: `acceptance.sh` one-click acceptance + `run_demo.py` 11-step main script.

---

## 7. Industry understanding

- **Why agents are hard to ship**: not model capability — it's controllability, trust, and reproducibility. MARS targets exactly those (schema/HITL/sedimentation).
- **Why vertical beats general for shipping**: general agents lack domain constraints (baseline protection, domain metrics, reusable assets) and drift; vertical scenarios can encode domain knowledge into gates and schemas.
- **Value of decoupling harness from agents**: the harness knows no specific agent — swap agents/projects/deployments without touching it. That's platform thinking — the "AI Infra" value proposition.
- **HITL isn't a step back, it's required**: in high-value, low-tolerance research/production settings, full autonomy is dangerous; human-AI teaming is the realistic form.
- **Comparables**: conceptually near Google's "AI Co-scientist," Stanford's research agents, Sakana's AI Scientist — but I emphasize **engineering controllability** over "fully autonomous paper generation."

---

## 8. Likely questions + model answers

**Q1: Isn't this just an LLM API wrapper?**
A: Three distinctions: (1) a real physics simulation (QR-orthogonalized dual-carrier PIM cancellation, not fake data); (2) mechanism innovations like schema governance and the self-healing loop live in the system, not the model; (3) engineering rigor (import-linter / mypy strict / 200 tests). A wrapper can't do a self-healing loop or zero-dependency reproducibility.

**Q2: The 7-layer model isn't actually trained — is that faking it?**
A: I'm upfront: the full 7-layer model needs real GPU + real `.mat` data; CPU can't run it. So Execution runs a **physics-faithful lightweight stand-in** (dual-carrier PIM + memory-polynomial cancellation); RES varies correctly with memory depth. Hooks are in place to swap in a subprocess for the real model on hardware. **Separating "demoable-real" from "needs-hardware-real" is itself an engineering judgment.**

**Q3: Why multi-agent over single-agent? Over-engineered?**
A: The research workflow is naturally staged; each stage needs different tools/models/review granularity (Coding needs baseline checks; Execution needs concurrent scheduling). Splitting gives each agent a single responsibility — independently reviewable/reusable/sedimentable — and debate needs multiple model perspectives. But I stay restrained: the harness is agent-agnostic, topology isn't hard-coded, and single-agent standalone is supported.

**Q4: How do you keep LLM output stable?**
A: Schema validation + template few-shot in the system prompt + fence stripping + persist-on-fail for human fixing → ≥95% first-pass compliance; failures go to HITL. **I don't assume the LLM is right — I use mechanisms to contain its uncertainty.**

**Q5: Why two state machines?**
A: Conversation cadence (clarifying? awaiting confirm? reporting?) and pipeline execution (node running/done) are orthogonal concerns. The conversation FSM governs human-AI interaction; the pipeline FSM governs agent execution; Commander bridges them. Merging them would couple the two.

**Q6: Concurrency / scalability?**
A: V0 uses asyncio + semaphore (execution pool concurrency cap 6), an in-process event bus (swappable to Redis pub/sub), and per-run/per-experiment WebSocket channel isolation. V0 is single-user single-host; multi-user/distributed is V2 (a `project_isolation` interface is already reserved).

**Q7: What would you redo?**
A: (1) Deepen Commander's self-healing coupling (attribution is currently rule-engine + LLM explanation; could become LLM-led); (2) the KB embedding is a deterministic hash (for zero-dependency) — swap to sentence-transformers in production; (3) several frontend components poll independently — consolidate into a single state subscription.

**Q8: How long did this take? Solo?**
A: Solo, full-stack, from scratch. Architecture first (5 top-level design docs), then strictly 7 phases end-to-end, each phase keeping the system runnable (end-to-end first — avoiding the classic "build everything horizontally then fail to integrate").

---

## 9. Honest self-assessment of the project's level

**Strengths**:
- **Full-stack depth**: ~17k lines, front+back + agent orchestration + physics simulation — breadth and depth;
- **Mechanism innovation**: schema-spine, aspect gates, self-healing loop, dual-layer FSM — **original at the system-design level**, not library glue;
- **Engineering discipline**: import-linter / mypy strict / 200 tests / full sedimentation — near-production quality;
- **Domain grounding**: real PIM physics + real research code/papers ingested — not a toy demo.

**Candid limitations**:
- V0 single-user; no real GPU training yet (physics stand-in);
- posttrain is a placeholder (V2);
- some capabilities are mock-backed;
- solo dev, so some modules trade depth for breadth.

**Positioning**: a **near-production MVP of a research-agent platform**, demonstrating combined skill in **system architecture + LLM engineering + agent orchestration + domain physics + full-stack**. Target roles: **AI Agent / LLM application engineering, AI Infra, Research Engineer**. If the team works on agent platforms / research agents / AI-for-Science, this project is a strong fit.

**One line**: I didn't build "yet another agent" — I built a **substrate that makes agents controllable, trustworthy, and reproducible for research**, validated end-to-end on my own PIM research domain.
