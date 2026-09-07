"""Pure feedback routing with authored records; no model execution or answers."""
from __future__ import annotations

import json

import pytest

from app.agents.idea.discovery.backend import _evolution_request_input
from app.agents.idea.discovery.evolution import build_evolution_requests
from app.harness.discovery import HypothesisRecord, MetaReviewRecord, ReflectionRecord


def authored_hypothesis(identifier: str, *, elo: float, blocked: bool = False) -> HypothesisRecord:
    return HypothesisRecord(hypothesis_id=identifier, run_id="authored-feedback-contract", round_index=0,
        mechanism="Human-authored contract record", statement="This is routing data, not a research hypothesis.",
        cluster_id=identifier, elo=elo, blocked=blocked)


def authored_review(identifier: str) -> ReflectionRecord:
    return ReflectionRecord(reflection_id="review-" + identifier, hypothesis_id=identifier,
        correctness="Human-authored routing marker for " + identifier, novelty="Not assessed.",
        falsifiability="Not assessed.", failure_modes=("Record belongs only to " + identifier,))


def test_only_selected_parent_reviews_and_previous_guidance_reach_payload() -> None:
    candidates = (authored_hypothesis("a", elo=1200), authored_hypothesis("b", elo=1100),
                  authored_hypothesis("blocked", elo=9000, blocked=True))
    reviews = tuple(authored_review(identifier) for identifier in ("a", "b", "blocked", "unrelated"))
    guidance = ("保留原始审查内容\r\n  不改写这条纯测试记录。",)
    meta = MetaReviewRecord(meta_review_id="meta-zero", run_id="authored-feedback-contract",
                            round_index=0, next_round_guidance=guidance)
    requests = build_evolution_requests(hypotheses=candidates, round_index=1, child_count=1,
                                        reflections=reviews, previous_meta_review=meta)
    assert len(requests) == 1 and requests[0].operator == "strengthen"
    assert tuple(parent.hypothesis_id for parent in requests[0].parents) == ("a",)
    assert requests[0].parent_reflections == (reviews[0],)
    assert requests[0].next_round_guidance == guidance
    assert requests[0].previous_meta_review_id == "meta-zero"
    payload = _evolution_request_input(requests[0])
    assert payload["parent_reflections"] == [reviews[0].model_dump(mode="json")]
    assert payload["next_round_guidance"] == list(guidance)
    assert payload["previous_meta_review_id"] == meta.meta_review_id
    encoded = json.dumps(payload, ensure_ascii=False)
    assert "review-b" not in encoded and "review-blocked" not in encoded and "review-unrelated" not in encoded
    assert candidates[2].blocked and reviews[0].correctness == "Human-authored routing marker for a"


def test_combined_parents_receive_their_own_reviews_without_changing_operator_cycle() -> None:
    candidates = tuple(authored_hypothesis(identifier, elo=1200 - index * 100)
                       for index, identifier in enumerate(("a", "b", "c")))
    reviews = tuple(authored_review(identifier) for identifier in ("a", "b", "c", "unrelated"))
    requests = build_evolution_requests(hypotheses=candidates, round_index=1, child_count=4,
                                        reflections=reviews)
    assert [request.operator for request in requests] == ["strengthen", "combine", "simplify", "diverge"]
    combined = requests[1]
    assert tuple(parent.hypothesis_id for parent in combined.parents) == ("b", "c")
    assert {review.hypothesis_id for review in combined.parent_reflections} == {"b", "c"}
    assert all({review.hypothesis_id for review in request.parent_reflections}
               == {parent.hypothesis_id for parent in request.parents} for request in requests)


def test_blocked_pool_remains_excluded_instead_of_inventing_a_repair_path() -> None:
    candidate = authored_hypothesis("blocked", elo=9000, blocked=True)
    assert build_evolution_requests(hypotheses=(candidate,), round_index=1, child_count=1,
                                     reflections=(authored_review("blocked"),)) == ()


@pytest.mark.parametrize("meta_round", [1, 2])
def test_guidance_from_a_different_round_is_rejected(meta_round: int) -> None:
    meta = MetaReviewRecord(meta_review_id="wrong-round", run_id="authored-feedback-contract",
                            round_index=meta_round, next_round_guidance=("A routing marker.",))
    with pytest.raises(ValueError, match="immediately preceding round"):
        build_evolution_requests(hypotheses=(authored_hypothesis("a", elo=1200),), round_index=1,
                                  child_count=1, previous_meta_review=meta)


def test_absent_feedback_is_explicitly_empty_and_never_filled_by_the_host() -> None:
    request = build_evolution_requests(hypotheses=(authored_hypothesis("a", elo=1200),),
                                       round_index=1, child_count=1)[0]
    assert request.parent_reflections == ()
    assert request.next_round_guidance == ()
    assert request.previous_meta_review_id == ""
    payload = _evolution_request_input(request)
    assert payload["parent_reflections"] == payload["next_round_guidance"] == []
