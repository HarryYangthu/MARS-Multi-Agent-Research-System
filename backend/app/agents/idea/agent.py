"""IdeaAgent uses the shared native loop; host checks evidence without authoring answers."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from app.agents.base import Artifact, BaseAgent, ContextPack, RunRequest
from app.agents.idea.research import material_errors, write_evidence
from app.harness.agent_loop.trace import atomic_json, digest
from app.harness.schema.frontmatter_parser import parse
from app.storage.artifact_store import ArtifactRef
from app.storage.run_store import RunHandle


class IdeaAgent(BaseAgent):
    name = "idea"
    output_schema = "proposal.v1"
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

    async def build_context(self, request: RunRequest) -> ContextPack:
        context = await super().build_context(request)
        requirements = request.extra.get("idea_requirements", {})
        context.task += "\n\nHost evaluation requirements (not experimental facts):\n" + json.dumps(requirements, ensure_ascii=False)
        context.task += (
            "\nIf require_parameter_budget is true, frontmatter must include: "
            "method_spec (precise baseline and proposed equations, interpolation basis, boundary, initialization, "
            "all trainables and frozen values); signal_contract (input/output shapes, real/complex semantics, "
            "phase handling, target metric formula and direction); alternatives (>=2 feasible methods with "
            "selection/rejection reasons); ablation_plan (>=3 comparisons, changed factor, held-fixed budget, "
            "metrics and rejection criteria). parameter_budget must contain unit: real_scalar, variables: numeric map, "
            "baseline_formula and candidate_formula: arithmetic strings, baseline_parameters and candidate_parameters: integers, "
            "baseline_components and candidate_components: lists of {name,formula,dtype,shape}. "
            "dtype must be real or complex; shape is a list of positive integer dimensions or arithmetic strings "
            "using variables (empty [] for one scalar). Formula counts real scalars and must match shape times "
            "the dtype multiplier (1 for real, 2 for complex). Count every trainable scalar; "
            "complex coefficient=2 real scalars, real knots count once, and normalization/gates are not free. "
            "Formulas only allow variable names, numbers, +,-,*,/,integer powers. "
            "Explain expressivity without assuming smoother functions contain all piecewise-linear functions. "
            "Specify knot multiplicities, degree/order, control point counts if using splines; prove any inclusion "
            "claim constructively or withdraw it. Gains and approximation rates require stated assumptions. "
            "Every formula must be directly implementable: no undefined corrective factors, ellipses or competing definitions. "
            "Define how ordered nodes remain inside fixed endpoints for every trainable state; check local-cell corner "
            "values and continuity across shared cell boundaries using the declared axis/index convention. "
            "C0 continuity does not imply complex phase equivariance or preserve a PIMC phase contract. "
            "Compare at least two distinct methods feasible under the budget; check every proposed grid size. "
            "Each alternative must include feasible (boolean), parameters (integer real-scalar count), "
            "and components (the same {name,formula,dtype,shape} format using parameter_budget.variables). "
            "Include at least two alternatives with feasible=true; the selected method may be one. "
            "Distinguish acceptance, rejection and inconclusive thresholds consistently. "
            "Put the primary comparison in one decision_rule object: define signed improvement, resampling "
            "unit, confidence interval, and exhaustive mutually exclusive accept/reject/inconclusive rules. "
            "Ablations refer to that rule rather than paraphrasing it with reversed inequalities or new thresholds. "
            "Use one output dtype and one parameter ledger throughout the proposal; do not switch between "
            "real-output and complex-output budgets. Every alternative must use that same baseline unit. "
            "State a single reproducible initialization procedure, including its sample locations, solver, "
            "and what happens if approximation error is unacceptable. Linear coefficients alone imply neither "
            "well-conditioning nor monotonic optimizer loss. A fit into a non-nested function space cannot "
            "guarantee no-worse initialization. Any complex-valued regularizer must be real and nonnegative. "
            "Do not repeat the full method in body: put definitions once in metadata and use a short body "
            "(at most 600 Chinese characters) explaining the selection and remaining experimental prerequisites. "
            "A method comparison is not an executed debate: omit debate_summary or set rounds=0. "
            "Describe only Memory tools actually invoked and PDF excerpts actually returned, including truncation."
            " Numerical contracts must hold for extreme finite logits, including floating-point softmax underflow; "
            "an additive minimum interval may be necessary. Distinguish number of nodes from number of cells. "
            "Do not call two representable function sets identical and then exhibit a function in only one. "
            "Use a single decision_rule reference in ablation rejection_criteria; put stability diagnostics "
            "in a separate field without silently changing statistical acceptance. Specify angular conversion "
            "and handling of zero predicted as well as zero reference magnitude. If clipping one coordinate, "
            "interpolate along the remaining coordinate on the boundary edge."
        )
        if request.extra.get("scope", "method_proposal") == "method_proposal":
            context.task += (
                "\nThis is a baseline-independent method proposal. No real project repository, dataset, "
                "historical experiments or GPU results have been supplied. Define a symbolic reference LUT explicitly. "
                "Do not claim production readiness, residual 2dB achievement, measured gain or globally novel work. "
                "Parameter ratio limit is an evaluation assumption, not a user-confirmed business specification."
            )
        return context

    async def draft(self, request: RunRequest, context: ContextPack) -> Artifact:
        mode = str(request.extra.get("idea_mode", "fast"))
        if mode != "fast":
            raise ValueError("deep discovery is not wired to the audited loop yet; use fast with reflection mode")
        try:
            return await self._draft_via_llm(request, context)
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
        errors.extend(material_errors(
            metadata, observations,
            min_sources=int(requirements.get("min_sources", 1)),
            min_pdfs=int(requirements.get("min_pdfs", 1)),
            require_budget=bool(requirements.get("require_parameter_budget", False)),
            max_ratio=float(requirements.get("max_parameter_ratio", 1.2)),
        ))
        if request.extra.get("scope", "method_proposal") == "project_proposal":
            if not any(o.get("ok") and o.get("tool") == "code.repo_reader" for o in observations):
                errors.append("/scope: project proposal requires actual baseline code evidence")
        root = Path(str(request.extra["run_root"])) / "idea" / "validation"
        atomic_json(root / (uuid.uuid4().hex + ".json"), {
            "schema_valid": True, "material_ready": not errors, "errors": errors,
            "candidate_sha256": digest(text), "requirements": requirements,
            "scope": request.extra.get("scope", "method_proposal"),
            "project_ready": False, "scientific_validated": False,
            "note": "Host structural/evidence/arithmetic checks are not independent scientific review.",
        })
        return errors

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
