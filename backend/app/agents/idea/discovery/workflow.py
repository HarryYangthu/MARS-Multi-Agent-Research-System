"""Checkpointed Generate→Reflect→Proximity→Elo→Evolve workflow."""
from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from app.agents.base import Artifact, RunRequest
from app.agents.idea.discovery.backend import DiscoveryRoleBackend
from app.agents.idea.discovery.evolution import evolve_hypotheses
from app.agents.idea.discovery.generation import generate_initial_hypotheses
from app.agents.idea.discovery.meta_review import build_meta_review
from app.agents.idea.discovery.models import (
    DeepDiscoveryConfig,
    DeepDiscoveryState,
    DiscoveryContext,
    HypothesisSelection,
    IdeaMode,
    stable_hash,
    stable_id,
)
from app.agents.idea.discovery.proximity import build_proximity_graph
from app.agents.idea.discovery.ranking import rank_hypotheses, select_top_hypotheses
from app.agents.idea.discovery.reflection import (
    add_reflection_blocker,
    reflect_hypotheses,
)
from app.agents.idea.discovery.storage import RunLocalDiscoveryStore
from app.harness.discovery import HypothesisRecord, ReflectionRecord
from app.harness.schema.frontmatter_parser import dumps as fm_dumps
from app.harness.schema.validator import validate_document


class DeepDiscoveryInsufficientPool(RuntimeError):
    """The deep loop could not produce enough legal, compared hypotheses."""


class CoScientistWorkflow:
    def __init__(
        self,
        *,
        backend: DiscoveryRoleBackend,
        config: DeepDiscoveryConfig,
        store: RunLocalDiscoveryStore,
    ) -> None:
        self.backend = backend
        self.config = config
        self.store = store

    async def run(self, context: DiscoveryContext) -> DeepDiscoveryState:
        input_hash = discovery_input_hash(context=context, config=self.config)
        state = self.store.load_state(input_hash=input_hash)
        if state is None:
            state = DeepDiscoveryState(
                run_id=context.run_id,
                project=context.project,
                input_hash=input_hash,
                config=self.config,
                backend_mode=self.backend.mode,
            )
            self.store.save_state(state)
        if state.status in {"waiting_selection", "selected"}:
            return state

        if "generation" not in state.completed_stages:
            generated = await generate_initial_hypotheses(
                backend=self.backend,
                context=context,
                config=self.config,
            )
            state = self._complete(
                state.model_copy(update={"hypotheses": generated}), "generation"
            )

        for round_index in range(self.config.evolution_rounds + 1):
            if round_index > 0:
                state = await self._evolve_round(
                    state=state,
                    context=context,
                    round_index=round_index,
                )
            state = await self._reflect_round(
                state=state,
                context=context,
                round_index=round_index,
            )
            state = self._proximity_round(state=state, round_index=round_index)
            state = await self._ranking_round(
                state=state,
                context=context,
                round_index=round_index,
            )
            state = await self._meta_review_round(
                state=state,
                context=context,
                round_index=round_index,
            )

        if "finalize" not in state.completed_stages:
            top_ids = select_top_hypotheses(
                state.hypotheses, top_k=self.config.top_k
            )
            legal_count = len([item for item in state.hypotheses if not item.blocked])
            if legal_count < self.config.top_k or len(top_ids) < self.config.top_k:
                failed = state.model_copy(
                    update={
                        "status": "failed",
                        "warnings": (*state.warnings, "insufficient_legal_hypotheses"),
                    }
                )
                self.store.save_state(failed)
                raise DeepDiscoveryInsufficientPool(
                    "deep discovery requires at least top_k legal hypotheses"
                )
            if not state.matches:
                failed = state.model_copy(
                    update={
                        "status": "failed",
                        "warnings": (*state.warnings, "no_pairwise_comparison"),
                    }
                )
                self.store.save_state(failed)
                raise DeepDiscoveryInsufficientPool(
                    "deep discovery requires at least one pairwise comparison"
                )
            state = self._complete(
                state.model_copy(
                    update={
                        "status": "waiting_selection",
                        "top_hypothesis_ids": top_ids,
                    }
                ),
                "finalize",
            )
        return state

    def proposal_for_hitl(
        self,
        state: DeepDiscoveryState,
        *,
        research_question: str,
    ) -> Artifact:
        """Materialize the top recommendation without recording human selection."""
        if not state.top_hypothesis_ids:
            raise DeepDiscoveryInsufficientPool("no legal Top-K recommendation exists")
        hypothesis = _find_hypothesis(state, state.top_hypothesis_ids[0])
        metadata, body = _proposal_content(
            state=state,
            hypothesis=hypothesis,
            research_question=research_question,
            source="recommended_for_hitl",
            actor="",
            reason="",
        )
        return _validated_artifact(metadata, body)

    def select_hypothesis(
        self,
        state: DeepDiscoveryState,
        *,
        hypothesis_id: str,
        research_question: str,
        actor: str,
        reason: str = "",
        source: Literal["human", "auto"] = "human",
    ) -> tuple[DeepDiscoveryState, Artifact]:
        """Persist an explicit HITL selection and synthesize ``proposal.v1``."""
        if not actor.strip():
            raise ValueError("selection actor is required")
        if hypothesis_id not in state.top_hypothesis_ids:
            raise ValueError("selected hypothesis must be a legal Top-K candidate")
        hypothesis = _find_hypothesis(state, hypothesis_id)
        if hypothesis.blocked:
            raise ValueError("blocked hypothesis cannot be selected")
        metadata, body = _proposal_content(
            state=state,
            hypothesis=hypothesis,
            research_question=research_question,
            source=source,
            actor=actor,
            reason=reason,
        )
        selection = HypothesisSelection(
            selection_id=stable_id(
                "selection", state.run_id, hypothesis_id, actor, reason, source
            ),
            run_id=state.run_id,
            hypothesis_id=hypothesis_id,
            actor=actor.strip(),
            reason=reason.strip(),
            source=source,
            proposal_metadata=metadata,
            proposal_body=body,
        )
        self.store.save_selection(selection)
        selected = state.model_copy(
            update={
                "status": "selected",
                "selected_hypothesis_id": hypothesis_id,
            }
        )
        self.store.save_state(selected)
        return selected, _validated_artifact(metadata, body)

    async def _evolve_round(
        self,
        *,
        state: DeepDiscoveryState,
        context: DiscoveryContext,
        round_index: int,
    ) -> DeepDiscoveryState:
        stage = f"round_{round_index}.evolution"
        if stage in state.completed_stages:
            return state
        children = await evolve_hypotheses(
            backend=self.backend,
            context=context,
            hypotheses=state.hypotheses,
            round_index=round_index,
            child_count=self.config.children_per_round,
        )
        return self._complete(
            state.model_copy(update={"hypotheses": (*state.hypotheses, *children)}),
            stage,
        )

    async def _reflect_round(
        self,
        *,
        state: DeepDiscoveryState,
        context: DiscoveryContext,
        round_index: int,
    ) -> DeepDiscoveryState:
        stage = f"round_{round_index}.reflection"
        if stage in state.completed_stages:
            return state
        reflected_ids = {item.hypothesis_id for item in state.reflections}
        pending = tuple(
            item
            for item in state.hypotheses
            if item.round_index <= round_index and item.hypothesis_id not in reflected_ids
        )
        if not pending:
            return self._complete(state, stage)
        updated_pending, new_reflections = await reflect_hypotheses(
            backend=self.backend,
            context=context,
            hypotheses=pending,
        )
        replacements = {item.hypothesis_id: item for item in updated_pending}
        hypotheses = tuple(
            replacements.get(item.hypothesis_id, item) for item in state.hypotheses
        )
        return self._complete(
            state.model_copy(
                update={
                    "hypotheses": hypotheses,
                    "reflections": (*state.reflections, *new_reflections),
                }
            ),
            stage,
        )

    def _proximity_round(
        self, *, state: DeepDiscoveryState, round_index: int
    ) -> DeepDiscoveryState:
        stage = f"round_{round_index}.proximity"
        if stage in state.completed_stages:
            return state
        hypotheses, graph, duplicates = build_proximity_graph(
            state.hypotheses,
            round_index=round_index,
            threshold=self.config.proximity_threshold,
        )
        reflections = state.reflections
        for hypothesis_id in duplicates:
            reflections = add_reflection_blocker(
                reflections,
                hypothesis_id=hypothesis_id,
                blocker="exact_duplicate",
            )
        return self._complete(
            state.model_copy(
                update={
                    "hypotheses": hypotheses,
                    "reflections": reflections,
                    "proximity_graphs": (*state.proximity_graphs, graph),
                }
            ),
            stage,
        )

    async def _ranking_round(
        self,
        *,
        state: DeepDiscoveryState,
        context: DiscoveryContext,
        round_index: int,
    ) -> DeepDiscoveryState:
        stage = f"round_{round_index}.ranking"
        if stage in state.completed_stages:
            return state
        remaining = self.config.max_pairwise_matches - len(state.matches)
        rounds_left = self.config.evolution_rounds - round_index + 1
        quota = 0 if remaining <= 0 else (remaining + rounds_left - 1) // rounds_left
        hypotheses, matches = await rank_hypotheses(
            backend=self.backend,
            context=context,
            hypotheses=state.hypotheses,
            existing_matches=state.matches,
            round_index=round_index,
            max_new_matches=quota,
            elo_k=self.config.elo_k,
        )
        return self._complete(
            state.model_copy(
                update={
                    "hypotheses": hypotheses,
                    "matches": (*state.matches, *matches),
                }
            ),
            stage,
        )

    async def _meta_review_round(
        self,
        *,
        state: DeepDiscoveryState,
        context: DiscoveryContext,
        round_index: int,
    ) -> DeepDiscoveryState:
        stage = f"round_{round_index}.meta_review"
        if stage in state.completed_stages:
            return state
        round_reflections = _reflections_for_round(
            hypotheses=state.hypotheses,
            reflections=state.reflections,
            round_index=round_index,
        )
        review = await build_meta_review(
            backend=self.backend,
            context=context,
            round_index=round_index,
            hypotheses=state.hypotheses,
            reflections=round_reflections,
        )
        return self._complete(
            state.model_copy(update={"meta_reviews": (*state.meta_reviews, review)}),
            stage,
        )

    def _complete(self, state: DeepDiscoveryState, stage: str) -> DeepDiscoveryState:
        if stage in state.completed_stages:
            return state
        completed = state.model_copy(
            update={"completed_stages": (*state.completed_stages, stage)}
        )
        self.store.save_state(completed)
        return completed


def resolve_idea_mode(request: RunRequest) -> IdeaMode:
    """Resolve mode while retaining the V3.0 no-field fast-path contract."""
    raw = request.extra.get("idea_mode")
    if raw is None or str(raw).strip() == "":
        return IdeaMode.FAST
    try:
        selected = IdeaMode(str(raw).strip().lower())
    except ValueError as exc:
        raise ValueError("idea_mode must be auto, fast, or deep") from exc
    if selected is not IdeaMode.AUTO:
        return selected
    return IdeaMode.FAST if _is_revision(request) else IdeaMode.DEEP


def discovery_root_for_request(request: RunRequest) -> Path | None:
    explicit = request.extra.get("idea_discovery_dir")
    if explicit:
        return Path(str(explicit))
    run_root = request.extra.get("run_root")
    if run_root:
        root = Path(str(run_root)) / "idea" / "discovery"
    else:
        research_dir = request.extra.get("idea_research_dir") or request.extra.get(
            "research_dir"
        )
        if not research_dir:
            return None
        root = Path(str(research_dir)).parent / "discovery"
    node_key = str(request.extra.get("node_key") or "idea")
    if node_key != "idea":
        root = root / _safe_name(node_key)
    return root


def build_discovery_context(
    *,
    request: RunRequest,
    evidence_refs: Sequence[str],
    constraints: Sequence[str],
) -> DiscoveryContext:
    run_id = str(request.extra.get("run_id") or "").strip()
    if not run_id:
        run_id = stable_id("standalone", request.project, request.user_request)
    context_hash = stable_hash(
        request.project,
        request.user_request,
        tuple(evidence_refs),
        tuple(constraints),
        request.extra.get("node_key", "idea"),
    )
    return DiscoveryContext(
        run_id=run_id,
        project=request.project,
        research_question=request.user_request,
        evidence_refs=tuple(evidence_refs),
        constraints=tuple(constraints),
        context_hash=context_hash,
    )


def discovery_input_hash(
    *, context: DiscoveryContext, config: DeepDiscoveryConfig
) -> str:
    return stable_hash(
        context.context_hash,
        context.run_id,
        context.project,
        context.research_question,
        context.evidence_refs,
        context.constraints,
        config.model_dump(mode="json"),
    )


def _proposal_content(
    *,
    state: DeepDiscoveryState,
    hypothesis: HypothesisRecord,
    research_question: str,
    source: Literal["human", "auto", "recommended_for_hitl"],
    actor: str,
    reason: str,
) -> tuple[dict[str, object], str]:
    selection_status = "selected" if source in {"human", "auto"} else "waiting_selection"
    metadata: dict[str, object] = {
        "schema": "proposal.v1",
        "project": state.project,
        "agent": "idea",
        "research_question": research_question,
        "hypothesis": hypothesis.statement,
        "novelty": (
            f"Co-Scientist 深度发现从机制簇 {hypothesis.mechanism} 中筛选该假设；"
            "其谱系、Reflection、Elo 与近似度证据均可回放。"
        ),
        "theoretical_basis": (
            f"候选机制为 {hypothesis.mechanism}，通过可证伪预测、成对比较和"
            "跨簇新颖性筛选后进入 Top-K。"
        ),
        "constraints": list(hypothesis.constraints),
        "testable_predictions": list(hypothesis.testable_predictions),
        "evidence_refs": list(hypothesis.evidence_refs),
        "risk_register": [
            {
                "risk": hypothesis.uncertainty
                or "排名只用于资源分配，不能替代实验真值。",
                "severity": "medium",
                "mitigation": "使用冻结 evaluator、同预算 baseline 与多 seed 实验验证。",
            }
        ],
        "downstream_requirements": [
            "Experiment Agent 必须把假设拆成最小可证伪消融。",
            "所有正式结论必须来自冻结 evaluator，而不是 Elo 排名。",
        ],
        "idea_mode": "deep",
        "discovery_summary": {
            "schema": "idea_discovery_summary.v1",
            "status": selection_status,
            "backend_mode": state.backend_mode,
            "hypothesis_count": len(state.hypotheses),
            "match_count": len(state.matches),
            "round_count": state.config.evolution_rounds + 1,
            "top_hypothesis_ids": list(state.top_hypothesis_ids),
            "selected_hypothesis_id": hypothesis.hypothesis_id,
            "selection_source": source,
            "selection_actor": actor,
            "selection_reason": reason,
            "records": {
                "pool": "idea/discovery/hypothesis_pool.v1.json",
                "hypotheses": "idea/discovery/hypotheses.v1.json",
                "reflections": "idea/discovery/reflections.v1.json",
                "matches": "idea/discovery/pairwise_matches.v1.json",
                "proximity": "idea/discovery/proximity_graphs.v1.json",
                "meta_reviews": "idea/discovery/meta_reviews.v1.json",
            },
        },
    }
    body = (
        "# 深度假设发现提案\n\n"
        f"## 选中假设\n\n{hypothesis.statement}\n\n"
        "## 可证伪预测\n\n"
        + "\n".join(f"- {item}" for item in hypothesis.testable_predictions)
        + "\n\n## 发现依据\n\n"
        f"- Mechanism: `{hypothesis.mechanism}`\n"
        f"- Elo: `{hypothesis.elo:.3f}`\n"
        f"- Cluster: `{hypothesis.cluster_id}`\n"
        f"- Parents: `{', '.join(hypothesis.parent_ids) or 'initial generation'}`\n"
        "- Elo 仅用于候选资源分配；科学结论仍需后续实验验证。\n"
    )
    return metadata, body


def _validated_artifact(metadata: dict[str, object], body: str) -> Artifact:
    text = fm_dumps(metadata, body)
    validation = validate_document(text, expected_schema="proposal.v1")
    if not validation.valid:
        raise RuntimeError(
            "deep discovery synthesized invalid proposal.v1: "
            + (validation.first_error() or "unknown validation error")
        )
    return Artifact(
        text=text,
        schema_id="proposal.v1",
        metadata=dict(metadata),
        body=body,
    )


def _find_hypothesis(
    state: DeepDiscoveryState, hypothesis_id: str
) -> HypothesisRecord:
    for item in state.hypotheses:
        if item.hypothesis_id == hypothesis_id:
            return item
    raise ValueError(f"unknown hypothesis {hypothesis_id}")


def _reflections_for_round(
    *,
    hypotheses: Sequence[HypothesisRecord],
    reflections: Sequence[ReflectionRecord],
    round_index: int,
) -> tuple[ReflectionRecord, ...]:
    ids = {
        item.hypothesis_id
        for item in hypotheses
        if item.round_index == round_index
    }
    return tuple(item for item in reflections if item.hypothesis_id in ids)


def _is_revision(request: RunRequest) -> bool:
    if str(request.extra.get("revision_reason") or "").strip():
        return True
    try:
        if int(request.extra.get("attempt") or 1) > 1:
            return True
    except (TypeError, ValueError):
        return True
    return any(
        key in request.upstream_artifacts
        for key in ("human_revision_request", "previous_version")
    )


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return cleaned or "idea"
