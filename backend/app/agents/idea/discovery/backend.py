"""Role backends for Co-Scientist hypothesis discovery.

The deterministic backend powers zero-dependency runs and replay tests.  The
LLM backend uses the same typed role surface, so provider-specific behavior
does not leak into the workflow or its persistence format.
"""
from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Literal, Protocol, cast

from app.agents.idea.discovery.models import (
    DiscoveryContext,
    EvolutionRequest,
    HypothesisDraft,
    MetaReviewDraft,
    PairwiseDecision,
    ReflectionDraft,
    stable_hash,
)
from app.harness.discovery import HypothesisRecord, ReflectionRecord


class DiscoveryRoleBackend(Protocol):
    mode: str

    async def generate(
        self, context: DiscoveryContext, *, count: int
    ) -> tuple[HypothesisDraft, ...]: ...

    async def reflect(
        self,
        context: DiscoveryContext,
        hypotheses: Sequence[HypothesisRecord],
    ) -> dict[str, ReflectionDraft]: ...

    async def judge(
        self,
        context: DiscoveryContext,
        pairs: Sequence[tuple[HypothesisRecord, HypothesisRecord]],
    ) -> tuple[PairwiseDecision, ...]: ...

    async def evolve(
        self,
        context: DiscoveryContext,
        requests: Sequence[EvolutionRequest],
    ) -> tuple[HypothesisDraft, ...]: ...

    async def meta_review(
        self,
        context: DiscoveryContext,
        *,
        round_index: int,
        hypotheses: Sequence[HypothesisRecord],
        reflections: Sequence[ReflectionRecord],
    ) -> MetaReviewDraft: ...


class DiscoveryProtocolError(RuntimeError):
    """A real role backend returned a malformed structured response."""


class DeterministicRoleBackend:
    mode = "deterministic_mock"

    _MECHANISMS: tuple[tuple[str, str, str], ...] = (
        (
            "sparse_routing",
            "以受限 top-k 稀疏路由减少无效计算，并用切换边界约束保持主指标稳定",
            "激活分支数下降且主质量指标相对 baseline 的退化不超过预设容差",
        ),
        (
            "low_rank_factorization",
            "把高维交互项分解为共享低秩基与小型条件系数，压缩冗余自由度",
            "参数量和计算量下降，同时验证集主指标保持在公平比较区间内",
        ),
        (
            "adaptive_memory",
            "按输入状态自适应选择有效记忆深度，避免所有样本使用最深计算路径",
            "困难区段获得更深记忆，稳定区段的平均计算量下降且尾部误差不恶化",
        ),
        (
            "shared_residual",
            "让公共主干拟合稳定成分，仅由轻量残差分支处理条件相关偏差",
            "公共主干复用率提高，并且残差分支带来可重复的增益而非训练噪声",
        ),
        (
            "hierarchical_composition",
            "先用低成本粗模型覆盖常见模式，再把高成本专家限制在难例区域",
            "相同预算下难例指标改善，普通样本的时延与资源消耗不增加",
        ),
        (
            "uncertainty_gate",
            "用可校准不确定性触发复杂路径，替代固定阈值或无条件专家调用",
            "触发率与误差风险单调相关，且校准失败时可以安全回退到 baseline",
        ),
        (
            "temporal_cache",
            "复用相邻状态的稳定中间量，仅在变化检测通过时重新计算昂贵特征",
            "缓存命中时结果与完整计算一致，变化点附近的误差受显式保护",
        ),
        (
            "bounded_quantization",
            "对低敏感度路径采用受限精度，并保留关键累加与输出层的高精度",
            "模型大小或访存下降，量化误差在多 seed 与边界输入上均不越界",
        ),
        (
            "structured_pruning",
            "根据组级贡献剪除稳定低价值结构，并在固定训练预算下做短程恢复",
            "结构化稀疏可转化为真实加速，恢复后主指标满足冻结门限",
        ),
        (
            "robust_ensemble",
            "只组合机制互补且接口兼容的轻量子模型，以小型仲裁器处理分布变化",
            "跨条件最差指标改善，同时组合成本低于单个高容量 baseline",
        ),
        (
            "curriculum_search",
            "按可验证难度逐级引入数据条件，避免早期搜索被极端样本主导",
            "相同训练预算下收敛方差下降，并保持最终全量协议上的公平性",
        ),
        (
            "invariant_features",
            "显式分离任务不变量与条件特异特征，减少切换条件造成的重复建模",
            "未见条件上的主指标更稳定，且不变量消融会显著削弱该收益",
        ),
    )

    async def generate(
        self, context: DiscoveryContext, *, count: int
    ) -> tuple[HypothesisDraft, ...]:
        anchor = _question_anchor(context.research_question)
        refs = context.evidence_refs or ("research/evidence_index.v1.json",)
        constraints = context.constraints or (
            "保持冻结 baseline、评测协议与接口不变",
        )
        offset = int(stable_hash(context.run_id, context.context_hash)[:8], 16)
        drafts: list[HypothesisDraft] = []
        for index in range(count):
            mechanism, intervention, prediction = self._MECHANISMS[
                (offset + index) % len(self._MECHANISMS)
            ]
            drafts.append(
                HypothesisDraft(
                    mechanism=mechanism,
                    statement=(
                        f"针对“{anchor}”，若{intervention}，则在冻结 evaluator 与相同预算下，"
                        f"{prediction}。"
                    ),
                    testable_predictions=(
                        prediction,
                        "至少三个固定 seed 的方向一致，并报告失败率与最差结果",
                    ),
                    evidence_refs=tuple(refs[:3]),
                    constraints=tuple(constraints[:4]),
                    uncertainty="机制收益依赖数据条件，必须通过消融和失败案例验证。",
                )
            )
        return tuple(drafts)

    async def reflect(
        self,
        context: DiscoveryContext,
        hypotheses: Sequence[HypothesisRecord],
    ) -> dict[str, ReflectionDraft]:
        del context
        output: dict[str, ReflectionDraft] = {}
        for item in hypotheses:
            blockers: list[str] = []
            if item.blocked:
                blockers.append("preblocked")
            if not item.testable_predictions:
                blockers.append("missing_testable_predictions")
            if not item.evidence_refs:
                blockers.append("missing_evidence_refs")
            if len(item.statement.strip()) < 12:
                blockers.append("statement_too_short")
            lowered = item.statement.lower()
            if any(token in lowered for token in ("不可验证", "无法测试", "guaranteed")):
                blockers.append("not_falsifiable")
            output[item.hypothesis_id] = ReflectionDraft(
                correctness=(
                    "机制与预测之间存在可检查的因果链。"
                    if not blockers
                    else "存在阻断项，不能作为最终科学假设。"
                ),
                novelty=f"相对于当前池，机制标签为 {item.mechanism}。",
                falsifiability=(
                    "可以通过冻结指标、同预算 baseline、消融和多 seed 证伪。"
                    if item.testable_predictions
                    else "缺少可证伪预测。"
                ),
                assumptions=("数据与 evaluator 版本冻结", "候选不能修改 baseline"),
                failure_modes=(
                    "增益只出现在单一 seed",
                    "复杂度下降未转化为实际时延收益",
                ),
                evidence_refs=item.evidence_refs,
                blockers=tuple(blockers),
            )
        return output

    async def judge(
        self,
        context: DiscoveryContext,
        pairs: Sequence[tuple[HypothesisRecord, HypothesisRecord]],
    ) -> tuple[PairwiseDecision, ...]:
        del context
        decisions: list[PairwiseDecision] = []
        for left, right in pairs:
            left_score = _hypothesis_score(left)
            right_score = _hypothesis_score(right)
            outcome: Literal["left", "right", "draw"]
            if abs(left_score - right_score) < 0.15:
                outcome = "draw"
                reason = "两者的可证伪性、证据覆盖和约束完整度接近。"
            elif left_score > right_score:
                outcome = "left"
                reason = "左侧假设具有更完整的预测、证据或约束覆盖。"
            else:
                outcome = "right"
                reason = "右侧假设具有更完整的预测、证据或约束覆盖。"
            refs = tuple(dict.fromkeys((*left.evidence_refs, *right.evidence_refs)))[:4]
            decisions.append(
                PairwiseDecision(outcome=outcome, reason=reason, evidence_refs=refs)
            )
        return tuple(decisions)

    async def evolve(
        self,
        context: DiscoveryContext,
        requests: Sequence[EvolutionRequest],
    ) -> tuple[HypothesisDraft, ...]:
        drafts: list[HypothesisDraft] = []
        for request in requests:
            primary = request.parents[0]
            secondary = request.parents[-1]
            operator_text = {
                "strengthen": "补充边界条件、多 seed 和失败判据以增强可证伪性",
                "combine": f"与 {secondary.mechanism} 的互补机制组合，但保持接口兼容",
                "simplify": "删除非必要自由度，仅保留能被最小消融识别的核心机制",
                "diverge": "探索当前相似度簇之外的反事实机制，并设置安全回退",
            }.get(request.operator, "在不改变冻结协议的前提下细化机制")
            statement = (
                f"第 {request.round_index} 轮从 {primary.mechanism} 演化："
                f"{operator_text}。若该修改有效，则相同预算下至少一个主指标改善，"
                "同时硬约束、最差 seed 与 baseline 公平性全部通过。"
            )
            refs = tuple(
                dict.fromkeys(
                    ref for parent in request.parents for ref in parent.evidence_refs
                )
            ) or context.evidence_refs
            constraints = tuple(
                dict.fromkeys(
                    value for parent in request.parents for value in parent.constraints
                )
            ) or context.constraints
            drafts.append(
                HypothesisDraft(
                    mechanism=f"{primary.mechanism}.{request.operator}",
                    statement=statement,
                    testable_predictions=(
                        "同预算下至少一个主指标相对父代改善",
                        "硬约束、最差 seed 和 baseline 公平性均通过",
                    ),
                    evidence_refs=tuple(refs[:4]),
                    constraints=tuple(constraints[:4]),
                    uncertainty="演化子代可能只提高新颖性而不提高客观指标。",
                )
            )
        return tuple(drafts)

    async def meta_review(
        self,
        context: DiscoveryContext,
        *,
        round_index: int,
        hypotheses: Sequence[HypothesisRecord],
        reflections: Sequence[ReflectionRecord],
    ) -> MetaReviewDraft:
        del context
        blocker_counts: dict[str, int] = {}
        for reflection in reflections:
            for blocker in reflection.blockers:
                blocker_counts[blocker] = blocker_counts.get(blocker, 0) + 1
        recurring = tuple(
            key for key, count in sorted(blocker_counts.items()) if count >= 1
        ) or ("当前轮未发现重复 blocker",)
        legal = sorted(
            (item for item in hypotheses if not item.blocked),
            key=lambda item: (-item.elo, item.hypothesis_id),
        )
        successful = tuple(item.mechanism for item in legal[:3]) or (
            "尚无合法机制",
        )
        explored = {item.mechanism.split(".", maxsplit=1)[0] for item in hypotheses}
        unexplored = tuple(
            mechanism
            for mechanism, _intervention, _prediction in self._MECHANISMS
            if mechanism not in explored
        )[:3] or ("需要从现有机制的反事实条件继续发散",)
        return MetaReviewDraft(
            recurring_errors=recurring,
            successful_patterns=successful,
            evidence_gaps=("外部证据和真实执行结果仍需在后续阶段补齐",),
            unexplored_regions=unexplored,
            next_round_guidance=(
                f"第 {round_index + 1} 轮优先保留合法高 Elo 父代",
                "至少分配一个子代给低覆盖簇，并避免只改写措辞",
            ),
        )


RoleCompleter = Callable[[str, str], Awaitable[str]]


class LLMRoleBackend:
    """Structured LLM implementation; malformed output fails closed."""

    mode = "llm_roles"

    def __init__(self, complete: RoleCompleter) -> None:
        self._complete = complete

    async def generate(
        self, context: DiscoveryContext, *, count: int
    ) -> tuple[HypothesisDraft, ...]:
        raw = await self._json_call(
            "generation",
            {
                "task": context.research_question,
                "project": context.project,
                "evidence_refs": context.evidence_refs,
                "constraints": context.constraints,
                "count": count,
                "required_output": {"hypotheses": "array of hypothesis objects"},
            },
        )
        rows = _mapping_list(raw.get("hypotheses"))
        drafts = tuple(_hypothesis_draft(row) for row in rows)
        if len(drafts) != count:
            raise DiscoveryProtocolError(
                f"generation returned {len(drafts)} hypotheses; expected {count}"
            )
        for draft in drafts:
            _require_known_evidence(
                draft.evidence_refs,
                allowed=context.evidence_refs,
                role="generation",
            )
        return drafts

    async def reflect(
        self,
        context: DiscoveryContext,
        hypotheses: Sequence[HypothesisRecord],
    ) -> dict[str, ReflectionDraft]:
        raw = await self._json_call(
            "reflection",
            {
                "task": context.research_question,
                "hypotheses": [item.model_dump(mode="json") for item in hypotheses],
                "required_output": {"reflections": "array, one per hypothesis_id"},
            },
        )
        output: dict[str, ReflectionDraft] = {}
        for row in _mapping_list(raw.get("reflections")):
            hypothesis_id = _required_text(row, "hypothesis_id")
            output[hypothesis_id] = _reflection_draft(row)
        missing = {item.hypothesis_id for item in hypotheses} - set(output)
        if missing:
            raise DiscoveryProtocolError(
                "reflection omitted hypotheses: " + ", ".join(sorted(missing))
            )
        allowed_by_id = {
            item.hypothesis_id: tuple(
                dict.fromkeys((*context.evidence_refs, *item.evidence_refs))
            )
            for item in hypotheses
        }
        for hypothesis_id, draft in output.items():
            _require_known_evidence(
                draft.evidence_refs,
                allowed=allowed_by_id[hypothesis_id],
                role="reflection",
            )
        return output

    async def judge(
        self,
        context: DiscoveryContext,
        pairs: Sequence[tuple[HypothesisRecord, HypothesisRecord]],
    ) -> tuple[PairwiseDecision, ...]:
        raw = await self._json_call(
            "pairwise_judge",
            {
                "task": context.research_question,
                "pairs": [
                    {
                        "left": left.model_dump(mode="json"),
                        "right": right.model_dump(mode="json"),
                    }
                    for left, right in pairs
                ],
                "required_output": {
                    "decisions": "ordered array with outcome left|right|draw"
                },
            },
        )
        decisions = tuple(_pairwise_decision(row) for row in _mapping_list(raw.get("decisions")))
        if len(decisions) != len(pairs):
            raise DiscoveryProtocolError("pairwise judge returned the wrong decision count")
        for decision, (left, right) in zip(decisions, pairs, strict=True):
            _require_known_evidence(
                decision.evidence_refs,
                allowed=tuple(
                    dict.fromkeys(
                        (*context.evidence_refs, *left.evidence_refs, *right.evidence_refs)
                    )
                ),
                role="pairwise_judge",
            )
        return decisions

    async def evolve(
        self,
        context: DiscoveryContext,
        requests: Sequence[EvolutionRequest],
    ) -> tuple[HypothesisDraft, ...]:
        raw = await self._json_call(
            "evolution",
            {
                "task": context.research_question,
                "requests": [
                    {
                        "round_index": item.round_index,
                        "operator": item.operator,
                        "parents": [
                            parent.model_dump(mode="json") for parent in item.parents
                        ],
                    }
                    for item in requests
                ],
                "required_output": {"children": "ordered array of hypothesis objects"},
            },
        )
        children = tuple(_hypothesis_draft(row) for row in _mapping_list(raw.get("children")))
        if len(children) != len(requests):
            raise DiscoveryProtocolError("evolution returned the wrong child count")
        for child, request in zip(children, requests, strict=True):
            parent_refs = tuple(
                dict.fromkeys(
                    ref for parent in request.parents for ref in parent.evidence_refs
                )
            )
            _require_known_evidence(
                child.evidence_refs,
                allowed=tuple(dict.fromkeys((*context.evidence_refs, *parent_refs))),
                role="evolution",
            )
        return children

    async def meta_review(
        self,
        context: DiscoveryContext,
        *,
        round_index: int,
        hypotheses: Sequence[HypothesisRecord],
        reflections: Sequence[ReflectionRecord],
    ) -> MetaReviewDraft:
        raw = await self._json_call(
            "meta_review",
            {
                "task": context.research_question,
                "round_index": round_index,
                "hypotheses": [item.model_dump(mode="json") for item in hypotheses],
                "reflections": [item.model_dump(mode="json") for item in reflections],
            },
        )
        return MetaReviewDraft(
            recurring_errors=_texts(raw.get("recurring_errors")),
            successful_patterns=_texts(raw.get("successful_patterns")),
            evidence_gaps=_texts(raw.get("evidence_gaps")),
            unexplored_regions=_texts(raw.get("unexplored_regions")),
            next_round_guidance=_texts(raw.get("next_round_guidance")),
        )

    async def _json_call(self, role: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        prompt = (
            "You are an Idea Agent internal Co-Scientist role. Return JSON only. "
            "Do not invent evidence refs and do not make scientific truth claims.\n"
            + json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        )
        text = await self._complete(role, prompt)
        parsed = _extract_json(text)
        if not isinstance(parsed, Mapping):
            raise DiscoveryProtocolError(f"{role} must return a JSON object")
        return parsed


def _extract_json(text: str) -> object:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", stripped, flags=re.IGNORECASE)
    candidate = fenced.group(1).strip() if fenced else stripped
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(candidate[start : end + 1])
            except json.JSONDecodeError:
                pass
        raise DiscoveryProtocolError("role response is not valid JSON") from exc


def _hypothesis_draft(row: Mapping[str, Any]) -> HypothesisDraft:
    return HypothesisDraft(
        mechanism=_required_text(row, "mechanism"),
        statement=_required_text(row, "statement"),
        testable_predictions=_texts(row.get("testable_predictions")),
        evidence_refs=_texts(row.get("evidence_refs")),
        constraints=_texts(row.get("constraints")),
        uncertainty=str(row.get("uncertainty") or "").strip(),
    )


def _reflection_draft(row: Mapping[str, Any]) -> ReflectionDraft:
    return ReflectionDraft(
        correctness=str(row.get("correctness") or "").strip(),
        novelty=str(row.get("novelty") or "").strip(),
        falsifiability=str(row.get("falsifiability") or "").strip(),
        assumptions=_texts(row.get("assumptions")),
        failure_modes=_texts(row.get("failure_modes")),
        evidence_refs=_texts(row.get("evidence_refs")),
        blockers=_texts(row.get("blockers")),
    )


def _pairwise_decision(row: Mapping[str, Any]) -> PairwiseDecision:
    outcome = str(row.get("outcome") or "").lower()
    if outcome not in {"left", "right", "draw"}:
        raise DiscoveryProtocolError("pairwise outcome must be left, right, or draw")
    return PairwiseDecision(
        outcome=cast(Literal["left", "right", "draw"], outcome),
        reason=str(row.get("reason") or "").strip(),
        evidence_refs=_texts(row.get("evidence_refs")),
    )


def _mapping_list(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        raise DiscoveryProtocolError("expected an array")
    output: list[Mapping[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise DiscoveryProtocolError("array items must be objects")
        output.append(item)
    return output


def _texts(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(text for item in value if (text := str(item).strip()))


def _required_text(row: Mapping[str, Any], key: str) -> str:
    value = str(row.get(key) or "").strip()
    if not value:
        raise DiscoveryProtocolError(f"missing required field {key}")
    return value


def _require_known_evidence(
    refs: Sequence[str], *, allowed: Sequence[str], role: str
) -> None:
    unknown = set(refs) - set(allowed)
    if unknown:
        raise DiscoveryProtocolError(
            f"{role} invented evidence refs: {', '.join(sorted(unknown))}"
        )


def _question_anchor(question: str) -> str:
    compact = " ".join(question.split())
    return compact[:96] if compact else "当前研究问题"


def _hypothesis_score(item: HypothesisRecord) -> float:
    score = 0.7 * min(len(item.testable_predictions), 3)
    score += 0.45 * min(len(item.evidence_refs), 4)
    score += 0.25 * min(len(item.constraints), 4)
    score += min(len(item.statement), 240) / 400.0
    if item.uncertainty:
        score += 0.2
    if item.blocked:
        score -= 10.0
    stable_bias = int(stable_hash(item.mechanism)[:4], 16) / 65535.0
    return score + stable_bias * 0.1
