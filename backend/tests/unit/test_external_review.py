"""Pure candidate-bound review transitions; no provider or tool substitute."""
from dataclasses import asdict, replace
import pytest
from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.review import ExternalReview, review_revision
from app.harness.agent_loop.trace import digest


def test_external_review_preserves_candidate_counts_and_requires_revision() -> None:
    # Authored state-machine input; no model execution is represented.
    state = {"candidate": "human-authored document", "pending": None, "counts": {"reflections": 1, "model_requests": 3},
             "review_issues": ["old issue"], "reflection_accepted": True}
    review = ExternalReview(digest(state["candidate"]), "human reviewer", ("new issue",))
    assert ExternalReview.from_mapping(asdict(review)) == review
    updates = review_revision(state, review, AgentLoopPolicy(mode="reflection", max_reflections=3, max_model_calls=8))
    assert updates["review_issues"] == ["old issue", "new issue"]
    assert updates["reflection_accepted"] is False and updates["next_phase"] == "act"
    assert "candidate" not in updates and "counts" not in updates
    assert state["reflection_accepted"] is True
    with pytest.raises(ValueError, match="does not match"):
        review_revision(state, replace(review, candidate_digest=digest("other")), AgentLoopPolicy())
    with pytest.raises(ValueError, match="pending"):
        review_revision({**state, "pending": "tool"}, review, AgentLoopPolicy())
    with pytest.raises(ValueError, match="reflection budget"):
        review_revision(state, review, AgentLoopPolicy(mode="reflection", max_reflections=1))
    with pytest.raises(ValueError, match="insufficient model"):
        review_revision(state, review, AgentLoopPolicy(mode="reflection", max_model_calls=4))


@pytest.mark.parametrize("issues", [[], [""], ["x"] * 33])
def test_empty_or_unbounded_review_is_rejected(issues: list[str]) -> None:
    with pytest.raises(ValueError):
        ExternalReview.from_mapping({"candidate_digest": digest("document"), "reviewer": "human", "issues": issues})
