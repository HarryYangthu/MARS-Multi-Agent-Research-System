# Real Idea evaluation: a completed loop is not sufficient

## Follow-up run: actual revisions still failed implementation review

Run `idea_lut_20260907T083733_656792` used source `0977032`, published as
`f2f2166` with an identical tree. It stopped honestly at `reflection_rejected`:
1,315.98 seconds, 15 model requests/responses/SDK attempts, 6 real tool calls,
2 protocol repairs, 1 schema/material repair and 3 rejecting Reflection rounds.
Reported usage was 190,841 input + 62,487 output = 253,328 tokens, complete.
Two PDFs were downloaded: PolyLUT and Free-Knots KAN. No final successful
artifact or experiment was reported.

Reflection did drive actual revisions of the cubic-spline ablation: first the
control count, then the explicit knot vector, then a remaining support-index
contradiction. However, independent inspection found additional missed issues:

- The selected output is complex, but the main real-scalar ledger says 289/324
  instead of 578/648. Its free-knot alternative adds 30 real coordinates to
  324 and is rejected; the consistent complex calculation is (648+30)/578,
  about 1.173, within this evaluation's 1.2 ratio.
- The initialization offers two competing procedures and promises a no-worse
  start in a non-nested function space without proof. Linearity in coefficients
  does not by itself establish conditioning or optimization stability.
- Squared complex differences need magnitude squares for a nonnegative real
  regularizer. Independent cell polynomials do not ensure C0 continuity.
- Decision thresholds leave uncovered cases, and one equal-budget comparison
  reverses the inequality used to reject the hypothesis.

Changes prepared from these failures: independently count typed parameter
tensor shapes; require two budget-feasible, typed alternative ledgers; keep
review issues pinned through format/schema repair; preserve an action receipt
index during context compression; request one statistical decision rule and
remove duplicate long method text. The next evaluation uses high effort for
both drafting and Reflection. These changes do not retroactively pass this run.

The native acceptance report now uses the actual invocation, candidate hash,
validation receipt, evidence and trace. It no longer requires an obsolete fixed
research recipe or GUI events for a headless evaluation.

## Earlier completed loop

Run `idea_lut_20260907T082315_8dd75f` used committed source `c7de8d9`,
published as GitHub checkpoint `a095ff6` (identical source tree). It used the
real Zhipu API and actual arXiv/PDF tools. No model, tool or service doubles ran.

Observed: 416.54 seconds; 8 model requests/responses and SDK attempts; 5 tool
calls/observations; 3 distinct arXiv results; one downloaded PolyLUT PDF and
two excerpt windows, with one cached-file reuse. Usage: 65,957 input + 11,511
output = 77,468 reported tokens. Schema and material checks passed. The model's
review first returned acceptance with three issues; protocol repair then
removed the issues and accepted the unchanged document.

Independent inspection rejected that candidate for implementation use:

1. Its node recurrence adds positive softplus increments but does not constrain
   their sum before a fixed final endpoint. With K=16, delta=0.001 and all
   interior logits zero, the last interior node is about 8.718, beyond endpoint
   1. Valid initialization does not guarantee valid training states.
2. Its declared x/y cell indexes and bilinear weights are transposed. With
   a 3-by-3 table containing row-major values 0..8, y at the lower boundary,
   the left and right limits at an x-cell boundary approach 1 and 3.
3. The warp map and the subsequent lookup coordinates have competing/vague
   definitions, including an undefined correction factor.
4. C0 continuity does not preserve complex phase equivariance. The proposal
   overstates its phase contract when describing separate real/imaginary LUTs.
5. It claims two debate rounds and a knowledge query that did not occur.
   Actual tools were local_docs, two arXiv searches and two PDF page reads.
6. The alternative methods are substantially the same over-budget polynomial
   construction, not two distinct feasible choices under the stated limit.
7. The equal-budget ablation labels compare the wrong pair, thresholds are
   inconsistent, and the proposed K=8 variant uses 77/64 > 1.2 parameters.
8. PDF download/extraction windows must not be described as complete page or
   full-document reading when returned excerpts were truncated.

The raw trace and original proposal remain unchanged in the run. The recorded
technical completion is not re-labelled as a scientifically validated result.

Changes made in response:

- A contradictory accepting review is conservatively routed to candidate
  revision, preserving every issue. Re-review keeps prior issues in context;
  re-submitting the identical candidate cannot bypass them.
- The Idea material gate rejects nonzero debate rounds without debate receipts.
- Method requirements and the review rubric now require endpoint/order,
  corner/boundary, phase, all-grid budget, and action-provenance checks.
- Reflection effort can be configured independently; this scenario uses high
  effort for review and low for research/drafting.
- The CPU reference loss reports the actual full residual objective. Artificial
  display ripple and expert/router-to-polynomial surrogates are removed.
- Bridge no longer silently completes an unregistered agent, and patch export
  copies an actual proposed diff instead of generating a fixed router patch.
- Model registry, loop and context tests were migrated individually to real
  configuration, registry, filesystem and serializer checks. Successful model
  repair remains a live-API requirement, not a canned test response.

The next real run must satisfy these checks and an independent implementation
review. Real PIMC data, baseline integration and GPU experiments remain outside
this supplied evaluation; no 2 dB performance claim is supported.

## Interrupted high-effort run and transport repair

Run `idea_lut_20260907T090254_d07f5a` started from clean source `f9623a0`
(published as `101d129`). Before any resumption it made 9 model requests,
received 8 model responses, performed 10 SDK attempts and 8 tool calls, and
failed with `model_error`. The ninth request received HTTP 502 twice, after
approximately 285 and 287 seconds. This is an observed upstream failure;
a non-streaming gateway deadline is a hypothesis, not a proven diagnosis.
Increasing the client timeout alone is not an established fix.

The actual tool history contains two empty memory queries, three arXiv searches
(10 distinct search sources), and three PDF fetch attempts. KAN and SineKAN
exceeded the configured 12 MiB limit. Free-Knots KAN was downloaded successfully
(2,442,172 bytes, SHA-256
`b35a6458203988c442468f0d8b3238dc2e9cf2629b25e8ab6af39ffdcf2a916b`).
Only pages 1–2 and a truncated excerpt of page 3 were visible to the model.
The run lasted 1,252.95 seconds; reported tokens total 69,858, excluding unknown
usage from the two failed SDK attempts. No candidate or Reflection was produced.

The Zhipu completion path now consumes the real SDK stream inside each bounded
retry attempt. A fresh accumulator isolates partial responses between attempts;
only visible output and usage are retained, with no private reasoning content.
Missing terminal markers, truncated output and empty content fail explicitly.
Public progress receipts contain chunk and visible-character counts.
Official interface reference: https://docs.bigmodel.cn/cn/guide/capabilities/streaming .

The live CLI now supports `--resume-run` for interrupted/model-error runs only.
It preserves original inputs, invocation, counters, remaining budgets and all
previous events. Before execution it snapshots the previous checkpoint, facts
and summary, hashes the old event prefix, and records the new source revision.
The audit checks these preserved files and lists every resumption source.
An exclusive filesystem lock rejects concurrent attempts; unknown tool outcomes
cannot be replayed automatically. Resumption is not a new clean trial.

Validation of this checkpoint: 64 targeted tests passed, including actual SDK
connection refusal followed by actual failed-request resumption, public stream
parsing, retry limits, Debate contracts, local trace redaction, real remote-worker
filesystem validation and real OpenSSH connection refusal. No successful remote
GPU or model response was invented. Targeted typing passed on the installed
Python 3.12 environment; Python 3.11 CI remains a separate gate.

The first live resume preflight correctly rejected a prompt fingerprint mismatch:
canonical checkpoint serialization had sorted the requirements mapping, changing
the ordering of its JSON string in the prompt. No API request occurred. Resume
now recovers that order from the preserved original prompt, requires identical
requirement values, and still checks exact messages plus the native fingerprint.
This restores serialization identity without weakening configuration checks.

## Further removal of legacy test substitutes

The old deep-discovery test backend, fixed Idea selection agent, fabricated
Writing debate and fixed remote worker PIDs/GPU metrics were replaced individually:

- Discovery pool tests use explicitly human-authored typed records for duplicate
  exclusion, blocked-candidate ranking, bounded configuration, input identity,
  idempotent snapshots and conflicting selection rejection. Actual SDK refusal
  confirms that failed generation cannot record a completed generation stage.
- Idea selection tests exercise the real coordinator and filesystem with an
  explicit human selection; absent registration/checkpoint fails explicitly.
- Writing tests cover bounded public previews and actual existing references.
- Remote runner tests start the real worker and workload processes. The workload
  computes SHA-256 from its actual input file; cancellation targets its actual
  process group, and a missing program produces a real failure. No GPU is used.
- Readiness tests inspect actual configuration/prerequisites. Deployment checks
  now require the only supported mode, `never`, in development and production.

These 25 focused checks passed; deployment checks passed 10 with 2 explicit
skips because Docker Compose is unavailable. Their coverage does not replace
unperformed full Co-Scientist generation or multi-agent scientific validation.
The remaining legacy Discovery Service/API and pipeline tests still need migration;
this checkpoint does not claim the entire repository's regression suite passes.

## Streamed output-budget failure and automatic recovery

The first resumption used clean source `e86e044` (GitHub `5823691`). The next
request completed, and the agent correctly identified that its previous PDF
excerpt omitted the Free-Knots method, then read a new cached window beginning
on page 4. The following request ended with `finish_reason=length`: reported
completion tokens were 16,384, including 16,354 reasoning tokens, with no usable
visible final answer. Streaming removed the earlier complete-response wait for
this request, but did not solve reasoning consuming the output budget.

The native loop now treats output truncation as a bounded protocol repair.
It lowers that phase's configured reasoning effort to low when applicable,
requests concise complete output, and preserves observations, candidate,
unresolved review issues and cumulative request limits. Every adjustment is
recorded as `completion_recovery`; effective effort and max tokens are recorded
per request. A legacy interrupted checkpoint can recover from its recorded
public truncation error without editing its original evidence or fingerprint.
Other provider errors continue to fail explicitly after bounded transport retries.
65 focused loop/recovery contracts passed; live recovery must still be verified.

Another legacy runtime path was found in the public synthetic adapter: preset
target coefficients, seeded coefficient jitter, a fabricated stability formula,
zero elapsed usage, and a selectable mock mode. These are removed. It now fits
actual data with standard-library QR ridge regression, reports disjoint holdout
MSE and measured elapsed time, and records training/holdout indices. The legacy
stability metric is explicitly a fitted coefficient-norm proxy. F0/F1 use six/eight
training points with the same holdout, and mock/config-only evaluation is rejected.
Seven adapter tests and the real twenty-candidate subprocess smoke passed.
The smoke also now uses the registered adapter, preserving its source-layout
environment instead of reconstructing an adapter without its import path.

### 实际续跑完成与独立复核（10:04 UTC）

同一 invocation 在不重置预算的第二次续跑中完成：累计 16 次模型请求、15 次模型响应、17 次 SDK 尝试、9 次工具调用/Observation、2 次协议修复、2 次 Reflection（先拒绝、再接受）。累计实际运行 2580.29 秒；本次续跑 637.06 秒。已报告 234270 tokens，但最早的 502 请求用量未知，因此不能声称完整费用。完整 trace 审计一致，schema 与文献材料校验通过；这仅是模型自审通过，不是独立科学验收。

独立复核仍拒绝 v1：精确计算原文 softmax 结点式，float64 的 `[1000,-1000,...,-1000]` 产生 14 个零宽区间；另发现函数类论断、离散正则定义、初始化输入、边界/相位契约与公平对照问题。原始输出和自审事件保留。新实现支持带候选 digest 的外部审查反馈，追加审查记录后原地续修，原预算、文献证据和计数不重置；产物递增版本，前稿和审查文件进入不可变续跑快照。49 项相关实际失败/纯契约/文件审核检查通过；新的外部审查续跑尚待执行。

### 移除仍能空跑通过的主控路径与真实回归测试

发现并移除 Orchestrator 的 no-op runner；未注册 Agent 必须失败。主控执行/修订现在使用显式注入的注册表，避免错误调用全局 Agent。人工审核缺 Agent 或 schema 映射时也失败。隔离区人工检索恢复为直接查询隔离记录，仍禁止向 Agent 上下文注入不可信历史。

Discovery 测试不再返回按候选索引预设的 loss/GPU 消耗或伪造安全准备回执，改用公开 CPU 回归 pack 的真实子进程拟合。已实际覆盖 20 个候选、单位不匹配拒绝筛选、三种子配对、F0→F1、F2 明确不支持而失败、持久化重放不重复计费、人工选择与暂停/停止。旧测试的伪成功 F2/模拟暂态失败恢复/中途强制故障统计晋级不再算作覆盖；底层晋级、预算、快照与安全工作区契约仍需纳入最终完整回归，不能用新检查数量冒充等价覆盖。

旧 Mock 全流水线、固定 MCP/论文指标等遗留测试仍在逐项迁移，尚不能称全仓 Mock 清理结束或全套 CI 通过。真实 CPU PIM 批量并发已执行通过；它不等于用户未接入的生产 PIMC 基线或 2 dB 目标验证。

### 外部审查续修与完整回归修复

第三次续跑实际完成（source `114572f`，GitHub `19dfc73`）：同一 invocation 累计 18 次模型请求、17 次响应、19 次 SDK 尝试、9 次工具/Observation、3 次 Reflection；总运行时间 2919.14 秒、已报告 287631 tokens（不完整）。v2 修复最小间隔和复数参数预算，但独立审查仍发现 theoretical_basis 内函数集合矛盾、16 个节点误写为 16 区间，以及正则消融与唯一判定规则不一致。不能因为模型自评通过就宣称可交付。保留 v1/v2 和外部 review，不重置旧 run 的预算。新一轮评测默认低推理起草、高推理 Reflection，保留独立审查。

完整后端回归首次出现 12 项失败。修复中发现真实问题：已批准本地产物没有来源回执而被 Memory 过滤；BaseAgent 丢失共享仓库上下文；数值计划 embedding 相同会误匹配不同配置。现为本地产物和宿主提取保存不可变文件快照与哈希（只证明来源，不证明科学结论），恢复所有子 Agent 的仓库配置上下文，baseline 匹配优先精确签名并过滤项目。未验证 metadata 仍不得作为真实实验记录。

旧 MCP/patch tool/adapter 固定成功替身已逐个换成实际文件操作、真实子进程失败与缺失依赖断言；CPU loss 不再要求人为波动；旧 Mock 模式必须配置失败。新增文件来源防篡改测试。本环境 strict mypy Python 3.12 检查 403 个源文件通过，import 边界通过。完整 pytest 与下一轮真实模型评测尚在进行。SDK 流关闭的异步生成器警告仍可复现，不影响已写入结果，但不能称已修复。
