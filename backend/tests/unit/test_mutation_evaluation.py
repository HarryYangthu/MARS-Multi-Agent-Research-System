from pathlib import Path

import pytest

from app.harness.evaluation.mutation import compare_task_results, load_mutation_suite, verify_task_evidence
from app.storage.agent_context_store import list_agent_context_files
from app.storage.run_store import RunStore
from app.storage.self_evolution_store import create_self_evolution_mutation, approve_self_evolution_mutation


def test_new_mutation_has_snapshots_but_no_unearned_evaluation_pass(tmp_path: Path) -> None:
    original = next(x for x in list_agent_context_files("idea", include_runtime_code=False) if x.path.startswith("prompts/"))
    run = RunStore(tmp_path / "runs").create(task="mutation-gate-test", project="pimc")
    item = create_self_evolution_mutation(run=run, lever_id="test-reviewed-reference", agent="idea",
        path=original.path, proposed_content=original.content + "\nState missing evidence explicitly.",
        rationale="Require evidence gaps to remain visible.")
    assert item["proposal_gate"]["passed"] is True
    assert item["eval_gate"]["passed"] is False
    assert (run.root / item["snapshot_ref"] / "before.md").read_text() == original.content
    with pytest.raises(ValueError, match="did not pass eval gate"):
        approve_self_evolution_mutation(run=run, mutation_id=item["id"])


def test_comparison_arithmetic_cannot_replace_real_model_evidence(tmp_path: Path) -> None:
    # These are numeric inputs to a pure comparison function, not claimed Agent
    # execution. The independent evidence validator must reject them.
    before = [{"task_id": x, "host_accepted": True, "total_tokens": 30} for x in ("a", "b")]
    after = [{"task_id": x, "host_accepted": True, "total_tokens": 20} for x in ("a", "b")]
    assert compare_task_results(before, after)["passed"] is True
    assert verify_task_evidence(tmp_path, after[0], context_sha256="0" * 64)
    assert compare_task_results(before, before)["passed"] is False
    assert compare_task_results(before, [{**x, "host_accepted": False} for x in after])["passed"] is False


def test_mutation_suite_is_trusted_registered_configuration() -> None:
    suite, _path = load_mutation_suite("experiment_mutation_contract_v1")
    assert len(suite["tasks"]) == 2
    assert suite["validation_scope"] == "agent_contract_behavior"
    with pytest.raises(ValueError, match="registered suite ID"):
        load_mutation_suite("../../shell")
