"""Real configuration files and parsing contracts, no trainer/provider doubles."""
from pathlib import Path

import pytest

from app.execution.paper_static_protocol import static_summary_errors, static_training_protocol


def test_full_epoch_budget_uses_both_loop_factors(tmp_path: Path) -> None:
    (tmp_path / 'base.yaml').write_text('Etotal: 20\nEpoch: 10\nif_scheduler: true\n')
    scenario = tmp_path / 'static.yaml'
    scenario.write_text('model_name: StaticPIMC\n')
    full = static_training_protocol(scenario, [], unit='epochs', count=200)
    assert full['training_epochs'] == full['configured_total_epochs'] == 200
    assert full['scheduler_unit'] == 'epochs' and not full['budget_warning']
    steps = static_training_protocol(scenario, [], unit='steps', count=50)
    assert steps['training_epochs'] is None and steps['configured_total_epochs'] == 200
    assert steps['scheduler_unit'] == 'steps' and '不是 50 个完整训练轮次' in steps['budget_warning']


def test_configured_loop_cannot_silently_truncate_approved_epochs(tmp_path: Path) -> None:
    scenario = tmp_path / 'static.yaml'
    scenario.write_text('Etotal: 1\nEpoch: 1\nif_scheduler: true\n')
    with pytest.raises(ValueError, match='仅允许 1 轮'):
        static_training_protocol(scenario, [], unit='epochs', count=200)
    exact = static_training_protocol(scenario, ['--set', 'Etotal=20', '--set', 'Epoch=10', '--set', 'if_scheduler=false'], unit='epochs', count=200)
    assert exact['training_epochs'] == 200 and exact['scheduler_unit'] == 'disabled'
    assert static_training_protocol(scenario, [], unit='steps', count=50)['requested_budget'] == 50


@pytest.mark.parametrize('unit,field', [('steps', 'optimizer_steps'), ('epochs', 'epochs')])
def test_completed_summary_must_match_actual_axis_and_seed(unit: str, field: str) -> None:
    assert not static_summary_errors({field: 200, 'seed': 2026}, unit=unit, count=200, seed=2026)
    assert static_summary_errors({field: 1, 'seed': 2026}, unit=unit, count=200, seed=2026)
    assert static_summary_errors({field: 200, 'seed': 0}, unit=unit, count=200, seed=2026)
    assert static_summary_errors({field: True, 'seed': 2026}, unit=unit, count=1, seed=2026)
    assert static_summary_errors({field: 200}, unit=unit, count=200, seed=2026)
