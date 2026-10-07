"""Canonical units stay explicit when two artifact schemas use count aliases."""
import pytest
from app.harness.schema.experiment_contract import budget


def test_explicit_steps_alias_and_identical_merged_counts_are_steps() -> None:
    assert budget({'budget_unit': 'steps', 'max_iters': 50}) == ('steps', 50)
    assert budget({'budget_unit': 'steps', 'max_iters': 50, 'budget_steps': 50}) == ('steps', 50)
    assert budget({'budget_unit': 'epochs', 'max_iters': 50}) == ('epochs', 50)


@pytest.mark.parametrize('config', [
    {'max_iters': 50}, {'budget_steps': 50, 'max_iters': 49, 'budget_unit': 'steps'},
    {'budget_steps': 50, 'max_iters': 50, 'budget_unit': 'epochs'},
    {'budget_steps': 50, 'max_iters': True}, {'max_iters': '50', 'budget_unit': 'steps'},
])
def test_ambiguous_units_changed_counts_and_noninteger_aliases_fail(config: dict) -> None:
    with pytest.raises(ValueError):
        budget(config)
