"""IdeaAgent uses the shared native loop; host checks evidence without authoring answers."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from app.agents.base import Artifact, BaseAgent, ContextPack, RunRequest
from app.agents.idea.research import material_errors, write_evidence
from app.agents.idea.delivery import delivery_errors, progress_sink, write_delivery
from app.agents.idea.acceptance import archive_baseline_input
from app.harness.agent_loop.executor import ProgressSink
from app.harness.llm.provider_base import Message
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
        "检索应有明确的信息缺口；当已取得要求数量的相关来源并读到方法页段后，应形成候选方案。"
        "只有具体定义、证据或比较仍缺失时再补检索，不为增加篇数反复扩展检索。"
        "最终输出遵循宿主指定的协议，方案必须包含完整元数据和中文正文。"
        "不要填补虚构 baseline、投票或实验结果。"
    )

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
        if request.extra.get("idea_requirements", {}).get("require_parameter_budget"):
            schema["required"] += ["parameter_budget", "signal_contract", "alternatives", "ablation_plan"]
            component = {"type": "object", "required": ["name", "formula", "dtype", "shape"],
                         "properties": {"name": {"type": "string", "minLength": 1},
                                        "formula": {"type": "string", "minLength": 1},
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
                budget_properties[prefix + "_formula"] = {"type": "string", "minLength": 1}
                budget_properties[prefix + "_parameters"] = {"type": "integer", "minimum": 1}
                budget_properties[prefix + "_components"] = components
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
            "never present a hypothesis as a measured gain. The body should repeat that summary only. "
            "Put the full method in method_spec and refer to its fields through handoff.changes[].spec_ref. "
            "handoff must follow idea.handoff.v1, target experiment, match the task scope, define next_step, "
            "changes, verification_requirements and required_context. Use blocks_execution for actual "
            "missing prerequisites, not hypothetical bureaucracy. Do not invent paths or data. "
            "Before important actions, give a short visible Chinese explanation of the information gap "
            "you are resolving. After finding enough relevant method evidence, draft rather than repeating searches. "
            "Define baseline and candidate equations, input/output and phase semantics, all trainable/fixed "
            "quantities, initialization, boundary handling, training objective and limitations. "
            "All quantities must have a single definition and the equations must be implementable. "
            "If require_parameter_budget is true: parameter_budget uses unit real_scalar, variables, "
            "baseline_formula, candidate_formula, integer baseline_parameters/candidate_parameters, "
            "and baseline_components/candidate_components lists of {name,formula,dtype,shape}; "
            "dtype is real or complex (count twice), shape=[] means one scalar. Arithmetic formulas "
            "use only declared numeric variables and +,-,*,/,integer powers. Compare at least two "
            "feasible alternatives, each with name, feasible, parameters, components (same component format), "
            "and selection/rejection reasons. Provide at least three meaningful ablations. "
            "Define one decision_rule with metric direction, comparison, resampling unit and disjoint "
            "accept/reject/inconclusive cases. Both ablations and handoff refer to it. "
            "Distinguish paper findings, your inference and untested hypotheses. Global novelty, "
            "approximation-rate, stability and function-inclusion claims require supporting assumptions "
            "and evidence; otherwise withdraw the guarantee. A numerical example is not a universal proof. "
            "Report only actual memory, tools, PDF page excerpts and review/debate activity."
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
        return context

    async def draft(self, request: RunRequest, context: ContextPack) -> Artifact:
        mode = str(request.extra.get("idea_mode", "fast"))
        if mode != "fast":
            raise ValueError("deep discovery is not wired to the audited loop yet; use fast with reflection mode")
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
                write_evidence(Path(str(request.extra["run_root"])), state["history"])

    async def validate_candidate(self, request: RunRequest, text: str,
                                 observations: list[dict[str, Any]]) -> list[str]:
        errors = await super().validate_candidate(request, text, observations)
        if errors:
            return errors
        requirements = request.extra.get("idea_requirements", {})
        metadata = parse(text).metadata
        candidate_sha = digest(text)
        input_receipt = archive_baseline_input(
            run_root=Path(str(request.extra["run_root"])), project=request.project,
            content=request.upstream_artifacts.get("baseline_code", ""), candidate_sha256=candidate_sha,
        )
        errors.extend(delivery_errors(metadata, str(request.extra.get("scope", "method_proposal"))))
        errors.extend(material_errors(
            metadata, observations,
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
                            "Do not invent extra acceptance requirements. Prior review issues are claims to recheck, "
                            "not authoritative facts; explicitly explain in rationale any withdrawn false positive "
                            "or issue outside scope. Only actual unresolved blockers belong in issues. "
                            "For each blocker name the exact current field and missing or contradictory definition; "
                            "reread the current candidate rather than copying a previous issue list."),
                    Message("system", "Idea acceptance scope: " + str(request.extra.get("scope", "method_proposal"))
                            + ". This stage delivers a falsifiable research proposal for downstream experiments. "
                            "It does not perform those experiments. Missing measured improvement, novelty proof, "
                            "hardware verification, or an equivalence theorem is not itself a blocker when the "
                            "proposal explicitly treats the gain as a hypothesis and the transfer as an inference. "
                            "For method_proposal, a fully defined symbolic I/O contract is allowed; real-project "
                            "mapping may be an explicit required_context prerequisite. Reject asserted guarantees "
                            "without support, and still require executable definitions and fair falsification criteria."),
                    Message("user", request.user_request),
                    Message("user", "Project constraints:\n" + context.project)]
        messages.extend(Message("user", "[untrusted supplied context:" + key + "]\n" + value)
                        for key, value in context.upstream.items())
        return messages

    def reflection_rubric(self) -> str:
        return (
            "Independently reconsider the candidate's logic using ONLY the supplied evidence. "
            "Check baseline/candidate function-class claims (smoothness does not imply strict inclusion), "
            "basis/knots/degree/control-point definitions, every real vs complex trainable count, boundary stability, "
            "input/output/phase contract, PIMC vs DPD metric transfer, fair equal-budget ablations, and "
            "whether any gain/novelty/resource claim exceeds actual evidence. "
            "Validate any gradient or error-order assertion and list assumptions. "
            "Actively try to falsify the algorithm: evaluate interpolation corner values and one-sided cell-boundary "
            "limits; test whether extreme finite node parameters violate ordering or fixed endpoints. "
            "An initialization that is valid does not prove all trained states remain valid. "
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
