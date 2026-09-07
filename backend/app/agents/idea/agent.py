"""IdeaAgent uses the shared native loop; host checks evidence without authoring answers."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from app.agents.base import Artifact, BaseAgent, ContextPack, RunRequest
from app.agents.idea.research import material_errors, write_evidence
from app.harness.agent_loop.trace import atomic_json
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
        "最终以 final.metadata（JSON 对象）和 final.body（中文 Markdown）输出完整方案，由宿主序列化 YAML。"
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
            "baseline_components and candidate_components: lists of {name,formula}. Count every trainable scalar; "
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
            "Distinguish acceptance, rejection and inconclusive thresholds consistently. "
            "A method comparison is not an executed debate: omit debate_summary or set rounds=0. "
            "Describe only Memory tools actually invoked and PDF excerpts actually returned, including truncation."
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
            "Reject vague algorithms, missing trainables, contradictory equations and hidden optional parameters. "
            "A sound, fully specified falsifiable method proposal may pass without measured performance; "
            "lack of actual baseline/data must remain an explicit downstream prerequisite."
        )

    def write_acceptance_report(self, *, run: RunHandle, artifact_ref: ArtifactRef | None = None,
                                node_key: str = "idea") -> Path:
        from app.agents.idea.acceptance import write_idea_acceptance_report
        return write_idea_acceptance_report(run=run, artifact_ref=artifact_ref, node_key=node_key)
