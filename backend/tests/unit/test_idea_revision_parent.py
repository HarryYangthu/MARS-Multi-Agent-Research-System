"""Pure parent lineage guards; no provider or tool substitute."""
import pytest

from app.harness.agent_loop.review import ExternalReview
from app.harness.agent_loop.trace import digest
from scripts.revise_idea_candidate import check_parent


@pytest.mark.parametrize("status", ["running", "interrupted", "model_error"])
def test_nonterminal_parent_is_not_restarted(status: str) -> None:
    review = ExternalReview(digest("authored document"), "reviewer", ("missing definition",))
    with pytest.raises(ValueError, match="terminal"):
        check_parent({"status": status, "pending": None, "candidate": "authored document"}, review)


def test_pending_operation_is_not_replayed() -> None:
    review = ExternalReview(digest("authored document"), "reviewer", ("missing definition",))
    with pytest.raises(ValueError, match="terminal"):
        check_parent({"status": "error", "pending": "tool", "candidate": "authored document"}, review)


def test_review_is_bound_to_exact_candidate() -> None:
    review = ExternalReview(digest("authored document"), "reviewer", ("missing definition",))
    state = {"status": "reflection_rejected", "pending": None, "candidate": "another document"}
    with pytest.raises(ValueError, match="exact parent"):
        check_parent(state, review)
    state["candidate"] = "authored document"
    check_parent(state, review)
