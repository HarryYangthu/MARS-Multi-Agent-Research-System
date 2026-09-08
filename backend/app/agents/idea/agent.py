"""IdeaAgent uses the shared native loop; host checks evidence without authoring answers."""
from __future__ import annotations

import json
import uuid
from functools import partial
from pathlib import Path
from typing import Any

from app.agents.base import Artifact, BaseAgent, ContextPack, RunRequest
from app.agents.idea.research import material_errors, write_evidence
from app.agents.idea.delivery import delivery_errors, progress_sink, write_delivery
from app.agents.idea.acceptance import archive_baseline_input
from app.agents.idea.protocol import protocol_schema
from app.agents.idea.research_links import research_link_errors, research_links_schema
from app.agents.idea.research_assessment import assessment_errors, assessment_schema
from app.harness.agent_loop.executor import ProgressSink
from app.harness.agent_loop.stop import StopCondition
from app.harness.llm.provider_base import Message
from app.harness.tools.registry import ToolRegistry
from app.harness.agent_loop.trace import atomic_json, digest
from app.harness.schema.frontmatter_parser import parse
from app.storage.artifact_store import ArtifactRef
from app.storage.run_store import RunHandle


class IdeaAgent(BaseAgent):
    name = "idea"
    output_schema = "proposal.v1"
    native_structured_delivery = True
    agent_brief = (
        "将研究问题转化为有证据、可证伪、可实现的方案。自主决定检索词、来源、下载与页窗口，"
        "每次工具行动说明理由。优先查真实 Memory，再查用户指定或允许网站的论文。"
        "必须通过工具读取实际资料，不能凭模型记忆填充引用。PDF下载不等于读过全文；"
        "如方法未出现在返回节选中，应选择新页窗口。不要改写工具错误或把空历史当成新颖性证明。"
        "方法迁移必须区分论文原结论、你的推断、尚待实验验证的假设。"
        "用户未定义的领域缩写、物理信号和硬件架构不得凭字母猜测；将它们列为需要确认的背景。"
        "检索应有明确的信息缺口；当核心设计已有相关原文支持、满足材料要求且未决问题已明确时，形成候选方案。"
        "只有具体定义、证据或比较仍缺失时再补检索，不为增加篇数反复扩展检索。"
        "最终输出遵循宿主指定的协议，方案必须包含完整元数据和中文正文。"
        "不要填补虚构 baseline、投票或实验结果。"
    )

    def requires_research_dossier(self, request: RunRequest) -> bool:
        return bool(request.extra.get("idea_requirements", {}).get("require_research_dossier")) or (
            "idea.research_delegate" in self.config.tools
        )

    def loop_stop_condition(self, request: RunRequest) -> StopCondition | None:
        if not self.requires_research_dossier(request):
            return None
        from app.agents.idea.research_stop import lead_evidence_stop
        from app.harness.tools.config import tool_config
        return partial(lead_evidence_stop, run_root=Path(str(request.extra["run_root"])),
                       min_sources=int(request.extra.get("idea_requirements", {}).get("min_sources", 1)),
                       max_delegations=int(self.config.raw.get("research", {}).get("max_delegations", 2)),
                       max_tool_steps=self.loop_policy.max_tool_steps,
                       can_delegate=("idea.research_delegate" in self.config.tools
                                     and tool_config("idea.research_delegate").enabled))

    def loop_stop_contract_id(self, request: RunRequest) -> str | None:
        if not self.requires_research_dossier(request):
            return None
        from app.agents.idea.research_stop import LEAD_STOP_CONTRACT
        return LEAD_STOP_CONTRACT

    def load_approved_research_context(self, *, run_root: Path, proposal_text: str,
                                       project: str) -> dict[str, Any] | None:
        from app.agents.idea.research_handoff import load_research_handoff
        return load_research_handoff(run_root, proposal_text, project=project)

    def loop_registry(self, request: RunRequest, context: ContextPack) -> ToolRegistry:
        if "idea.research_delegate" not in self.config.tools:
            return super().loop_registry(request, context)
        from app.agents.idea.research_delegate import make_research_registry
        return make_research_registry(self.config, request, context)

    def required_review_tools(self, request: RunRequest) -> tuple[str, ...]:
        return ("idea.research_delegate",) if self.requires_research_dossier(request) else ()

    def submission_schema(self, request: RunRequest) -> dict[str, Any] | None:
        schema = super().submission_schema(request)
        if schema is None:
            return None
        # New submissions require the full delivery contract, while historical
        # proposal.v1 documents retain their original compatibility contract.
        schema["required"] += ["human_summary", "handoff", "method_spec", "decision_rule",
                               "related_literature", "testable_predictions", "risk_register"]
        for field in ("method_spec", "decision_rule"):
            schema["properties"][field] = {"type": "object", "minProperties": 1,
                "description": "Canonical complete structured definition; never put this only in body."}
        requirements = request.extra.get("idea_requirements", {})
        if self.requires_research_dossier(request):
            schema["required"].append("research_links")
            schema["properties"]["research_links"] = research_links_schema()
            schema["required"].append("research_assessment")
            schema["properties"]["research_assessment"] = assessment_schema()
        if requirements.get("require_parameter_budget") or requirements.get("require_evaluation_protocol"):
            schema["required"].append("evaluation_protocol")
            schema["properties"]["evaluation_protocol"] = protocol_schema()
        if request.extra.get("idea_requirements", {}).get("require_parameter_budget"):
            schema["required"] += ["parameter_budget", "signal_contract", "alternatives", "ablation_plan"]
            component = {"type": "object", "required": ["name", "formula", "dtype", "shape"],
                         "properties": {"name": {"type": "string", "minLength": 1},
                                        "formula": {"type": "string", "minLength": 1,
                                                    "description": "Arithmetic expression for the real-scalar count, not tensor notation or prose. No equals sign; for example n*m."},
                                        "dtype": {"enum": ["real", "complex"]},
                                        "shape": {"type": "array", "maxItems": 8,
                                                  "items": {"anyOf": [{"type": "integer", "minimum": 1},
                                                                       {"type": "string", "minLength": 1}]}}}}
            components = {"type": "array", "minItems": 1, "items": component}
            budget_properties: dict[str, Any] = {
                "unit": {"const": "real_scalar"},
                "variables": {"type": "object", "minProperties": 1, "additionalProperties": {"type": "number"},
                              "description": "Numeric values only. Put variable explanations in a different field."},
            }
            for prefix in ("baseline", "candidate"):
                budget_properties[prefix + "_formula"] = {"type": "string", "minLength": 1,
                    "description": "Executable arithmetic expression only; no equals sign or appended result. Use variables declared in variables."}
                budget_properties[prefix + "_parameters"] = {"type": "integer", "minimum": 1}
                budget_properties[prefix + "_components"] = components
            budget_properties["evaluation_cases"] = {
                "type": "array", "minItems": 1, "maxItems": 32,
                "description": "Include the primary configuration unchanged. Other sizes inherit its tensor ledger; different candidate architectures may explicitly override BOTH candidate_formula and candidate_components. All cases retain the baseline ledger and host budget limit.",
                "items": {"type": "object", "additionalProperties": False,
                          "required": ["name", "variables", "baseline_parameters", "candidate_parameters"],
                          "properties": {"name": {"type": "string", "minLength": 1, "maxLength": 120},
                                         "variables": {"type": "object", "additionalProperties": {"type": "number"}},
                                         "baseline_parameters": {"type": "integer", "minimum": 1},
                                         "candidate_parameters": {"type": "integer", "minimum": 1},
                                         "candidate_formula": budget_properties["candidate_formula"],
                                         "candidate_components": components},
                          "dependentRequired": {"candidate_formula": ["candidate_components"],
                                                "candidate_components": ["candidate_formula"]}}}
            schema["properties"]["parameter_budget"] = {"type": "object", "required": list(budget_properties),
                                                          "properties": budget_properties}
            schema["properties"]["signal_contract"] = {"type": "object", "minProperties": 1}
            for field, minimum in (("alternatives", 2), ("ablation_plan", 3)):
                schema["properties"][field] = {"type": "array", "minItems": minimum,
                                                "items": {"type": "object"}}
            schema["properties"]["alternatives"]["items"] = {"type": "object",
                "required": ["name", "feasible", "parameters", "components"],
                "properties": {"name": {"type": "string"}, "feasible": {"type": "boolean"},
                               "parameters": {"type": "integer", "minimum": 1}, "components": components}}
        return schema

    async def build_context(self, request: RunRequest) -> ContextPack:
        context = await super().build_context(request)
        requirements = request.extra.get("idea_requirements", {})
        context.task += "\n\nHost evaluation requirements (not experimental facts):\n" + json.dumps(requirements, ensure_ascii=False)
        context.task += (
            "\nDeliver one coherent proposal for the next Experiment agent. Include human_summary: "
            "one or two short Chinese sentences describing exactly what changes and why it may help; "
            "never present a hypothesis as a measured gain. The body must equal human_summary exactly; "
            "no headings, duplicated equations or extra sections in body. "
            "Put the full method in method_spec and refer to its fields through handoff.changes[].spec_ref. "
            "handoff must follow idea.handoff.v1, target experiment, match the task scope, define next_step, "
            "changes, verification_requirements and required_context. Use blocks_execution for actual "
            "missing prerequisites, not hypothetical bureaucracy. Do not invent paths or data. "
            "Before important actions, give a short visible Chinese explanation of the information gap "
            "you are resolving. After finding enough relevant method evidence, draft rather than repeating searches. "
            "Keep assistant commentary brief; spend the output allowance on one complete native submission. "
            "Conciseness must not remove equations, concrete data definitions, sample counts, stopping rules "
            "or the statistical decision procedure. A placeholder such as 'until convergence' is incomplete. "
            "Define baseline and candidate equations, input/output and phase semantics, all trainable/fixed "
            "quantities, initialization, boundary handling, training objective and limitations. "
            "All quantities must have a single definition and the equations must be implementable. "
            "Prefer one minimal change to the baseline; add a second change only with a concrete justification "
            "and a distinct ablation. An ablation must actually change behavior on the specified evaluation domain. "
            "For each ablation explain how every retained trainable parameter affects the loss under its "
            "specified optimizer. A hard index alone does not provide a gradient to its selection parameters. "
            "For automatic differentiation, define the canonical forward operation and scalar loss once, "
            "then explain gradient connectivity; redundant hand-expanded derivatives are unnecessary. If "
            "you supply an explicit derivative, verify its sign and summation endpoints against that loss. "
            "For constrained updates, define a single feasible set and an explicit parameterization or "
            "well-defined joint projection onto it. Sorting and clipping separately need not preserve "
            "minimum spacing and endpoints together. State behavior at ties and the final domain endpoint. "
            "An iid uniform sample count alone does not guarantee that every cell receives a sample; "
            "a coverage-enforcing construction may provide such a guarantee for its specified cells. "
            "For factorized parameters define each factor's initialization and check that it permits a training signal. "
            "Node formulas must produce exactly the declared number of distinct nodes; count endpoints and intervals once. "
            "If require_parameter_budget is true: parameter_budget uses unit real_scalar, variables, "
            "baseline_formula, candidate_formula, integer baseline_parameters/candidate_parameters, "
            "and baseline_components/candidate_components lists of {name,formula,dtype,shape}; "
            "Declare parameter_budget.evaluation_cases for every configuration proposed for evaluation, "
            "including the primary variables, with unique name, full variables map, baseline_parameters "
            "and candidate_parameters. By default each case uses the primary formulas and tensor definitions. "
            "For a different candidate architecture (e.g. frozen knots or low-rank factors), supply BOTH "
            "candidate_formula and candidate_components in that case, with its complete actual tensor ledger; "
            "assign all primary variables plus any additional numeric variables needed by the override. "
            "The baseline ledger and host budget still apply. Include the primary configuration without overrides. "
            "Do not force different architectures into one formula or introduce evaluated dimensions only in prose. "
            "dtype is real or complex (count twice), shape=[] means one scalar. Arithmetic formulas "
            "use only declared numeric variables and +,-,*,/,integer powers. Compare at least two "
            "feasible alternatives, each with name, feasible, parameters, components (same component format), "
            "and selection/rejection reasons. Provide at least three meaningful ablations. "
            "Ablations that freeze or remove parameters may have fewer actual trainables. Count them "
            "honestly; never add dummy, disconnected or ineffective trainables to equalize a count. Such "
            "an ablation measures the total effect of enabling that parameter block, without separating "
            "the mechanism from the additional degrees of freedom. All arms must obey the task budget. "
            "Define one decision_rule with metric direction, comparison, resampling unit and disjoint "
            "accept/reject/inconclusive cases. Both ablations and handoff refer to it. "
            "Distinguish paper findings, your inference and untested hypotheses. Global novelty, "
            "approximation-rate, stability and function-inclusion claims require supporting assumptions "
            "and evidence; otherwise withdraw the guarantee. A numerical example is not a universal proof. "
            "Report only actual memory, tools, PDF page excerpts and review/debate activity."
        )
        if requirements.get("require_parameter_budget") or requirements.get("require_evaluation_protocol"):
            context.task += (
                "\nUse evaluation_protocol (idea.evaluation.v1) as the canonical controlled-comparison "
                "contract. Declare datasets with IDs, train/validation/test roles and method_spec refs; "
                "define objectives once with train-only data_refs and method_spec refs. Both arms reference "
                "their training and held-out assessment IDs, objective, optimizer and initialization. "
                "Keep arms.baseline and arms.candidate for the primary comparison. For other target cases or "
                "ablations, declare additional named arms with method_spec_ref and named comparisons containing "
                "baseline_arm, candidate_arm, decision_rule_ref, isolates_architecture and differences_justification. "
                "Every extra arm must participate in a comparison. Each arm is trained independently for each seed; "
                "do not pool different target cases into one training objective. Every dataset definition must bind "
                "its target/labels and split, and every comparison must bind an explicit statistical decision rule. "
                "For an architecture-isolating comparison share the training data and objective; any intentional "
                "non-architecture differences require a justification. If an arm changes the loss or adds "
                "a regularizer, define its own canonical objective including the term and coefficient, point "
                "that arm to it, and set isolates_architecture=false for that comparison. A description in "
                "ablation prose cannot add a term absent from the referenced objective. Declare actual seeds and the random "
                "source(s) that change across seeds, with executable definitions under method_spec. "
                "A seed label does not make deterministic repetitions independent. Put split construction, "
                "training objective, optimizer, initialization and randomness definitions at distinct method_spec "
                "references; other prose must refer to this protocol instead of redefining it. "
                "The Experiment handoff and decision_rule must use this same protocol. These declarations "
                "are not evidence of physically disjoint data, an implemented random source or scientific validity."
            )
        scope = request.extra.get("scope", "method_proposal")
        if scope not in {"method_proposal", "project_proposal"}:
            raise ValueError("Idea scope must be method_proposal or project_proposal")
        context.task += "\nRequested scope: " + str(scope)
        if scope == "method_proposal":
            context.task += (
                "\nNo real project repository is assumed in this method-only scope. Define any symbolic "
                "baseline explicitly; do not claim project readiness or measured performance. "
                "handoff.required_context must list baseline_code and data_description as prerequisites "
                "for actual project execution. Evaluation ratio limits are assumptions, not business facts."
            )
        if request.upstream_artifacts:
            context.task += "\nCaller-supplied context is available under these exact references: " + ", ".join(request.upstream_artifacts)
        if self.requires_research_dossier(request):
            context.task += (
                "\nDelegate literature research through idea.research_delegate. Specify a concrete "
                "information gap and selection criteria derived from this task; the researcher has its own "
                "context and actual search/PDF tools. You choose when to delegate and whether to request "
                "further research. Each delegation has its own min_sources (default 1), separate from the "
                "final task's global distinct-publication minimum. You can assign different gaps to different "
                "research delegations and combine their verified reports. Once a report is accepted, reuse its "
                "verified findings; do not repeat its papers merely to make each child meet the global total. "
                "Mention already-covered paper titles/URLs in a new gap to guide complementary research; "
                "this does not count as the new child having read them. "
                "Search results alone are not evidence of reading. Use the returned "
                "verified research_report.v1, which distinguishes paper_finding from transfer_idea and "
                "limitations. Include research_links entries with the exact delegation_id and insight_id, "
                "a resolving method_spec_ref under /method_spec/, and adaptation_reason explaining how "
                "that paper insight informs your chosen method and under what conditions. Cite its original "
                "title and URL in related_literature. Do not copy source claims into universal guarantees. "
                "The researcher supplies evidence and possible transfers; you remain responsible for one "
                "complete implementable proposal. Rejected candidates can be revised in this same loop. "
                "Do not request new research simply to repeat an already answered question."
                "\nProvide research_assessment (idea.research_assessment.v1) in concise Chinese: "
                "state the task_question, selection_principles with id/criterion/task_basis, stopping_reason "
                "and remaining_gaps (an empty list is allowed). For every used source in each verified report, "
                "record a source_decision with delegation_id, source_id, decision (adopt/exclude/defer), "
                "reason, task_relevance, criterion_ids, insight_ids and transfer_assumptions. "
                "Explain which current design decision the paper can inform, relevant differences in input, "
                "budget or assumptions, and when that transfer could fail. A matching keyword, high citation "
                "count, available PDF or minimum paper quota is insufficient as a scientific selection reason. "
                "For adopt, insight_ids must exactly match the insights from that source used in research_links; "
                "exclude/defer use empty insight_ids and no method links. A useful negative result may inform "
                "an explicit design constraint or rejected alternative under method_spec. Unrelated papers "
                "should be excluded, never attached to a dummy method field to meet a count. "
                "Only distinct papers linked to actual method definitions count toward min_sources. "
                "Explain why the research is sufficient for this decision and list unresolved gaps honestly. "
                "Every claim in the human_summary must agree with the canonical method_spec and handoff. "
                "Give each delegation a bounded information gap; the overall minimum publication count is "
                "aggregated across reports, not automatically the minimum for every child. Separate gaps may "
                "each require one publication. Do not repeat an entire failed research assignment unchanged: "
                "use its concrete failure to narrow the missing evidence or switch available search channels. "
                "A relevant source need only inform a specific defensible method decision, not already solve "
                "the complete target task with its exact budget. The final adaptation must meet all task constraints. "
                "If an insight supports an alternative or a rejected approach, define the actual alternative "
                "or design constraint precisely enough to justify that decision. Merely naming a discarded "
                "approach does not make its paper an adopted source."
            )
        return context

    async def draft(self, request: RunRequest, context: ContextPack) -> Artifact:
        mode = str(request.extra.get("idea_mode", "fast"))
        if mode != "fast":
            raise ValueError("deep discovery is not wired to the audited loop yet; use fast with reflection mode")
        if self.requires_research_dossier(request) and self.loop_policy.mode != "reflection":
            raise ValueError("research dossier delivery requires reflection mode to review relevance and transfer before publication")
        try:
            artifact = await self._draft_via_llm(request, context)
            trace_root = Path(str(context.metadata["loop_trace_root"]))
            reviewed = bool(context.metadata.get("reflection_accepted"))
            delivery_root = write_delivery(artifact, request, invocation=trace_root.name, reviewed=reviewed)
            context.metadata["idea_delivery_root"] = str(delivery_root)
            return artifact
        finally:
            trace_root = Path(str(context.metadata.get("loop_trace_root", "")))
            checkpoint = trace_root / "checkpoint.json"
            if checkpoint.is_file():
                state = json.loads(checkpoint.read_text())
                from app.agents.idea.research_delegate import load_delegated_research
                run_root = Path(str(request.extra["run_root"]))
                try:
                    _, child_observations = load_delegated_research(run_root, state["history"])
                except (OSError, ValueError, KeyError) as exc:
                    child_observations = []
                    atomic_json(trace_root / "research_evidence_error.json", {"error": str(exc)})
                write_evidence(run_root, [*state["history"], *child_observations])

    async def validate_candidate(self, request: RunRequest, text: str,
                                 observations: list[dict[str, Any]]) -> list[str]:
        errors = await super().validate_candidate(request, text, observations)
        if errors:
            return errors
        requirements = request.extra.get("idea_requirements", {})
        parsed = parse(text)
        metadata = parsed.metadata
        from app.agents.idea.research_delegate import load_delegated_research
        try:
            reports, child_observations = load_delegated_research(
                Path(str(request.extra["run_root"])), observations,
            )
        except (OSError, ValueError, KeyError) as exc:
            return ["/research_evidence: delegated evidence could not be verified: " + str(exc)]
        material_observations = [*observations, *child_observations]
        if self.requires_research_dossier(request):
            errors.extend(research_link_errors(metadata, reports, min_sources=int(requirements.get("min_sources", 1)),
                                               require_linked_sources=True))
            errors.extend(assessment_errors(metadata, reports, required=True))
        candidate_sha = digest(text)
        input_receipt = archive_baseline_input(
            run_root=Path(str(request.extra["run_root"])), project=request.project,
            content=request.upstream_artifacts.get("baseline_code", ""), candidate_sha256=candidate_sha,
        )
        errors.extend(delivery_errors(metadata, str(request.extra.get("scope", "method_proposal")), body=parsed.body))
        errors.extend(material_errors(
            metadata, material_observations,
            min_sources=int(requirements.get("min_sources", 1)),
            min_pdfs=int(requirements.get("min_pdfs", 1)),
            require_budget=bool(requirements.get("require_parameter_budget", False)),
            max_ratio=float(requirements.get("max_parameter_ratio", 1.2)),
        ))
        if request.extra.get("scope", "method_proposal") == "project_proposal":
            if input_receipt is None and not any(o.get("ok") and o.get("tool") == "code.repo_reader" for o in observations):
                errors.append("/scope: project proposal requires actual baseline code evidence")
        root = Path(str(request.extra["run_root"])) / "idea" / "validation"
        atomic_json(root / (uuid.uuid4().hex + ".json"), {
            "schema_valid": True, "material_ready": not errors, "errors": errors,
            "candidate_sha256": candidate_sha, "requirements": requirements,
            "delivery_contract_version": "idea.handoff.v1",
            "body_policy": "summary_only",
            "evaluation_protocol_required": bool(requirements.get("require_parameter_budget") or requirements.get("require_evaluation_protocol")),
            "parameter_cases_required": bool(requirements.get("require_parameter_budget")),
            "research_dossier_required": self.requires_research_dossier(request),
            "research_assessment_required": self.requires_research_dossier(request),
            "verified_research_delegations": [r["delegation_id"] for r in reports],
            "input_evidence": [input_receipt] if input_receipt is not None else [],
            "scope": request.extra.get("scope", "method_proposal"),
            "project_ready": False, "scientific_validated": False,
            "note": "Host structural/evidence/arithmetic checks are not independent scientific review.",
        })
        return errors

    def loop_progress_sink(self, request: RunRequest, invocation: str) -> ProgressSink:
        return progress_sink(request, invocation)

    def review_messages(self, request: RunRequest, context: ContextPack) -> list[Message]:
        messages = [Message("system", "You are a critical scientific methods reviewer. Assess the "
                            "candidate and actual evidence. Do not author a new proposal or tools. "
                            "Return only the review JSON requested below; write rationale and issues in concise Chinese. "
                            "A schema pass is not scientific proof. Report only concrete blockers to this stage: "
                            "contradictory or unimplementable definitions, missing essential decisions, incorrect "
                            "arithmetic, unobserved evidence claims, or claims stronger than their stated support. "
                            "Do not invent extra acceptance requirements. Judge this document independently; "
                            "only actual current blockers belong in issues. "
                            "For each blocker name the exact current field and missing or contradictory definition; "
                            "quote supporting text and calculate any claimed counterexample."),
                    Message("system", "Idea acceptance scope: " + str(request.extra.get("scope", "method_proposal"))
                            + ". This stage delivers a falsifiable research proposal for downstream experiments. "
                            "It does not perform those experiments. Missing measured improvement, novelty proof, "
                            "hardware verification, or an equivalence theorem is not itself a blocker when the "
                            "proposal explicitly treats the gain as a hypothesis and the transfer as an inference. "
                            "For method_proposal, a fully defined symbolic I/O contract is allowed; real-project "
                            "mapping may be an explicit required_context prerequisite. Reject asserted guarantees "
                            "without support, and still require executable definitions and fair falsification criteria."),
                    Message("user", "Complete task and host requirements used to draft and validate this proposal "
                            "(evaluate these requirements; your output is still review JSON, not a proposal):\n"
                            + context.task),
                    Message("user", "Project constraints:\n" + context.project)]
        messages.extend(Message("user", "[untrusted supplied context:" + key + "]\n" + value)
                        for key, value in context.upstream.items())
        return messages

    def reflection_rubric(self) -> str:
        return (
            "Independently reconsider the candidate's logic using ONLY the supplied evidence. "
            "For delegated research, check the original visible page excerpts against each paper_finding "
            "and the proposal's research_links. Exact quote matching proves provenance only, not that the "
            "paper supports the interpretation. Check transfer assumptions and explicitly untested claims. "
            "Evaluate research_assessment against the original task, verified reports and method_spec: "
            "do the selection principles resolve concrete task gaps, are adopted papers genuinely relevant "
            "to the stated design decisions, and do the extracted findings support the proposed transfers "
            "under the declared assumptions? A source count, citation, keyword match or nonempty field "
            "does not demonstrate usefulness. Reject decorative links, unsupported inference, copied "
            "paper guarantees, contradictions between source_decisions and actual use, and a stopping_reason "
            "that ignores an essential unresolved design gap. An excluded paper needs no positive result. "
            "Check the short Chinese human_summary explains the actual change and its plausible mechanism "
            "without claiming measured gains, and agrees with the complete machine-readable handoff. "
            "Check every research-linked alternative is actually defined and supports the declared comparison "
            "or rejection; a named but undefined discarded approach is a decorative link. "
            "Compare ablations pairwise for identical behavior, then test whether each claimed change can "
            "affect any input in the stated domain. Check every ablation size against evaluation_cases. "
            "Include initialization target queries in the shared training-label budget, rather than silently "
            "giving each architecture its own extra labels. Check the entire task contract before accepting; "
            "fixing one numerical defect does not establish the remaining requirements. "
            "Check baseline/candidate function-class claims (smoothness does not imply strict inclusion), "
            "basis/knots/degree/control-point definitions, every real vs complex trainable count, boundary stability, "
            "input/output/phase contract, PIMC vs DPD metric transfer, fair comparisons under the task budget, and "
            "whether any gain/novelty/resource claim exceeds actual evidence. "
            "Freezing or removing a parameter block is a valid ablation with fewer trainables; its total "
            "effect includes the changed degrees of freedom. Do not require equal counts or dummy/ineffective "
            "trainables. Reject only stronger causal claims that the actual comparison cannot support. "
            "Trace each arm's objective_ref to the complete scalar loss, including all regularization "
            "terms and coefficients. A changed-loss comparison must declare isolates_architecture=false. "
            "Check any explicit derivative against that canonical loss, including sign and boundary indices; "
            "automatic differentiation needs an executable loss, not a duplicate hand-written gradient. "
            "Validate any gradient or error-order assertion and list assumptions. "
            "Trace the loss-to-parameter path separately for every ablation. Distinguish hard selection "
            "indices from gathered or sorted values: a discontinuous index does not make the gradient of "
            "sorted values zero away from ties. Identify the exact operation that blocks a claimed update, "
            "and test a concrete perturbation before asserting a zero derivative. "
            "Actively try to falsify the algorithm: evaluate interpolation corner values and one-sided cell-boundary "
            "limits; test whether extreme finite node parameters violate ordering or fixed endpoints. "
            "An initialization that is valid does not prove all trained states remain valid. "
            "For projected updates identify the complete feasible set and check that the final operation "
            "preserves all constraints jointly; clipping after a spacing correction can create ties again. "
            "An iid sample count alone does not guarantee every cell is occupied; distinguish it from "
            "a construction that explicitly enforces coverage of its specified cells. "
            "Check that every warp/map and indexing convention has one executable definition; vague corrections "
            "are material defects. Continuity is not phase equivariance. Check all grid-size budgets, consistent "
            "success/rejection thresholds, distinct feasible alternatives, and reported Memory/debate/PDF actions "
            "against the actual observations. Do not defer missing definitions to downstream agents. "
            "Recompute dtype/shape counts, including real knots with complex coefficients; compare against "
            "the same real-scalar baseline everywhere. Check each stated initialization or optimizer guarantee "
            "rather than inferring it from linearity. Verify regularizers return nonnegative real numbers. "
            "Acceptance/rejection/inconclusive cases must be exhaustive and mutually exclusive, with one "
            "statistical definition across the proposal. Independent cell coefficients do not guarantee C0. "
            "Reject vague algorithms, missing trainables, contradictory equations and hidden optional parameters. "
            "Check every occurrence, especially theoretical_basis and ablation rejection_criteria, against "
            "the canonical method/decision definition; a correction in an addendum does not remove a contradiction. "
            "A sound, fully specified falsifiable method proposal may pass without measured performance; "
            "lack of actual baseline/data must remain an explicit downstream prerequisite."
        )

    def write_acceptance_report(self, *, run: RunHandle, artifact_ref: ArtifactRef | None = None,
                                node_key: str = "idea") -> Path:
        from app.agents.idea.acceptance import write_idea_acceptance_report
        return write_idea_acceptance_report(run=run, artifact_ref=artifact_ref, node_key=node_key)
