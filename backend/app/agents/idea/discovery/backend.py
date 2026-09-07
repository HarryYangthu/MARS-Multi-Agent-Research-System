"""Real model role backend for typed Co-Scientist discovery."""
from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Literal, Protocol, cast

from app.agents.idea.discovery.contracts import contract_errors, role_schema
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
                **_task_input(context),
                "count": count,
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
                **_task_input(context),
                "hypotheses": [item.model_dump(mode="json") for item in hypotheses],
                "output_requirement": "Exactly one reflection for every supplied hypothesis_id.",
            },
        )
        rows = _mapping_list(raw.get("reflections"))
        _validate_reflection_ids(rows, expected=tuple(item.hypothesis_id for item in hypotheses))
        output: dict[str, ReflectionDraft] = {}
        for row in rows:
            hypothesis_id = _required_text(row, "hypothesis_id")
            output[hypothesis_id] = _reflection_draft(row)
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
                **_task_input(context),
                "pairs": [
                    {
                        "left": left.model_dump(mode="json"),
                        "right": right.model_dump(mode="json"),
                    }
                    for left, right in pairs
                ],
                "output_requirement": "Exactly one decision per input pair, in the same order; decisions[i] judges pairs[i].",
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
                **_task_input(context),
                "requests": [_evolution_request_input(item) for item in requests],
                "feedback_policy": "Parent reflections and prior-round guidance are model assessments to check against "
                    "the task and evidence, not established facts or compulsory conclusions.",
                "scheduling_limits": "Blocked candidates are excluded from parent selection and have no repair path. "
                    "The fixed operator cycle is unchanged; one child per round still uses strengthen.",
                "output_requirement": "Exactly one child per input request, in the same order; children[i] answers requests[i].",
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
                **_task_input(context),
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
        schema = role_schema(role)
        prompt = _role_prompt(payload, schema=schema)
        text = await self._complete(role, prompt)
        return _validate_role_response(text, role=role, schema=schema)


def _task_input(context: DiscoveryContext) -> dict[str, Any]:
    return {"task": context.research_question, "project": context.project,
            "evidence_refs": context.evidence_refs, "constraints": context.constraints}


def _evolution_request_input(request: EvolutionRequest) -> dict[str, Any]:
    return {"round_index": request.round_index, "operator": request.operator,
            "parents": [parent.model_dump(mode="json") for parent in request.parents],
            "parent_reflections": [review.model_dump(mode="json") for review in request.parent_reflections],
            "previous_meta_review_id": request.previous_meta_review_id,
            "next_round_guidance": list(request.next_round_guidance)}


def _role_prompt(payload: Mapping[str, Any], *, schema: dict[str, Any]) -> str:
    return (
        "You are an Idea Agent internal Co-Scientist role. Return JSON only matching response_schema. "
        "Use exactly the required field names and types; do not substitute aliases or omit fields. "
        "Do not invent evidence refs and do not make scientific truth claims.\n"
        + json.dumps({**payload, "response_schema": schema}, ensure_ascii=False, sort_keys=True, default=str)
    )


def _validate_role_response(text: str, *, role: str, schema: dict[str, Any]) -> Mapping[str, Any]:
    parsed = _extract_json(text)
    errors = contract_errors(parsed, schema)
    if errors:
        raise DiscoveryProtocolError(f"{role} response schema: " + "; ".join(errors[:12]))
    if not isinstance(parsed, Mapping):
        raise DiscoveryProtocolError(f"{role} must return a JSON object")
    return parsed


def _validate_reflection_ids(rows: Sequence[Mapping[str, Any]], *, expected: Sequence[str]) -> None:
    known = set(expected)
    seen: set[str] = set()
    for row in rows:
        hypothesis_id = _required_text(row, "hypothesis_id")
        if hypothesis_id not in known:
            raise DiscoveryProtocolError(f"reflection returned unknown hypothesis_id: {hypothesis_id}")
        if hypothesis_id in seen:
            raise DiscoveryProtocolError(f"reflection returned duplicate hypothesis_id: {hypothesis_id}")
        seen.add(hypothesis_id)
    missing = known - seen
    if missing:
        raise DiscoveryProtocolError("reflection omitted hypotheses: " + ", ".join(sorted(missing)))


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
        uncertainty=_required_text(row, "uncertainty"),
    )


def _reflection_draft(row: Mapping[str, Any]) -> ReflectionDraft:
    return ReflectionDraft(
        correctness=_required_text(row, "correctness"),
        novelty=_required_text(row, "novelty"),
        falsifiability=_required_text(row, "falsifiability"),
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
        reason=_required_text(row, "reason"),
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
