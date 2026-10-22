"""Pure contract/metric/scope checks. These fixtures represent no executed run."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from app.bridge.commander_native import commander_tool_input_schema
from app.agents.idea.evolution_planner import reasoning_history, selected_scopes
from app.bridge.evolution_service import archive_execution_memory, bind_user_protocol, freeze_evolution, load_evolution
from app.bridge.evolution_service import _feedback_card, _hash_file
from app.bridge.workflow_service import build_pipeline
from app.harness.evolution import (EvolutionContract, EvolutionModule, ModuleScope, bind_evolution_writes, bound_candidate_aliases,
    bind_explicit_metric_deltas, candidate_priority, check_evolution_write, evaluate_candidates, evolution_plan_errors, validate_module_content)
from app.storage.run_state_store import RunStateStore
from app.storage.run_store import RunStore


def contract(**changes: Any) -> EvolutionContract:
    return EvolutionContract.model_validate({'goal': 'Reduce residual while preserving parameter count',
        'criteria': [{'name': 'RES', 'direction': 'lte', 'target': -1, 'baseline_delta': True},
                     {'name': 'parameters', 'direction': 'lte', 'target': 100, 'role': 'constraint'}],
        'modules': [{'module': 'training_strategy', 'path': 'candidate.yaml', 'yaml_keys': ['optimizer.lr']}],
        **changes})


def row(name: str, residual: Any, parameters: Any = 100) -> dict[str, Any]:
    return {'experiment_id': name, 'status': 'completed', 'is_mock': False,
            'fingerprint_hash': 'parser-fixture-only', 'metrics': {'RES': residual, 'parameters': parameters}}


def test_frozen_input_directory_detects_content_additions_and_changes(tmp_path: Path) -> None:
    data = tmp_path / 'data'
    data.mkdir()
    (data / 'capture.bin').write_bytes(b'authored input bytes')
    before = _hash_file(tmp_path, 'data')
    assert _hash_file(tmp_path, 'data') == before
    (data / 'second.bin').write_bytes(b'new input')
    assert _hash_file(tmp_path, 'data') != before
    (data / 'second.bin').unlink()
    (data / 'capture.bin').write_bytes(b'changed input bytes')
    assert _hash_file(tmp_path, 'data') != before


@pytest.mark.parametrize('text', ['目标：候选的 RES 比本轮匹配基线至少降低0.5dB，且参数不能增加。',
    'Reduce RES by at least 0.5 dB compared with baseline.'])
def test_relative_improvement_cannot_be_reversed_by_model_sign(text: str) -> None:
    spec = contract(criteria=[{'name': 'RES', 'direction': 'lte', 'target': .5, 'baseline_delta': True}])
    bound = bind_explicit_metric_deltas(spec, [text])
    assert bound.criteria[0].target == -.5
    assert not evaluate_candidates(bound, [row('baseline', 5), row('candidate', 5)])[0]['passed']
    assert evaluate_candidates(bound, [row('baseline', 5), row('candidate', 4.5)])[0]['passed']


def test_relative_metric_binding_preserves_absolute_and_ambiguous_goals() -> None:
    spec = contract()
    assert bind_explicit_metric_deltas(spec, ['不要降低模型性能，最多5轮。']) == spec


def test_improvement_binding_cannot_invent_tolerance_or_convert_a_percent_into_db() -> None:
    spec = contract(criteria=[{'name': 'RES', 'direction': 'lte', 'target': .5, 'baseline_delta': True, 'tolerance': 10}])
    bound = bind_explicit_metric_deltas(spec, ['候选RES比基线至少降低0.5dB'])
    assert bound.criteria[0].tolerance == 0
    assert not evaluate_candidates(bound, [row('baseline', 5), row('candidate', 5)])[0]['passed']
    assert bind_explicit_metric_deltas(spec, ['RES比基线最多降低0.5dB']) == spec
    assert bind_explicit_metric_deltas(spec, ['RES比基线降低20%']) == spec


@pytest.mark.parametrize('label', ['module:training_strategy (only candidate.yaml lr)',
    'Module ： training-strategy; preserve seed', 'module: training strategy'])
def test_module_explanations_do_not_become_identifiers(label: str) -> None:
    assert selected_scopes(contract(), [label]) == list(contract().modules)


def test_module_labels_never_expand_the_frozen_permission() -> None:
    with pytest.raises(ValueError, match='no allowed'):
        selected_scopes(contract(), ['module:forward (rewrite architecture)'])


def test_reasoning_preserves_measured_feedback_after_a_large_prior_handoff() -> None:
    authored = {'selected_proposal': {'explanation': 'long source material ' * 1000},
        'current_iteration': 4, 'best': {'attempt': 2}, 'recent_evidence': [
            {'attempt': 3, 'passed': False, 'winner': {'metrics': {'RES': 8.0}},
             'protocol_errors': [], 'diagnostics_ref': 'evolution/attempts/attempt_3/diagnostics.json',
             'diagnostics': [{'coefficient_summary': {'saved_tensor': [3, 1, 0]}}]}],
        'related_project_history': 'Earlier candidate: lr=0.0002, RES=9, matching protocol still requires checking.'}
    result = reasoning_history({'evolution_task_context': json.dumps(authored), 'baseline_code': 'x' * 7000})
    actual = result['evolution_task_context']
    assert actual['recent_evidence'][0]['winner']['metrics']['RES'] == 8
    assert actual['related_project_history'] == authored['related_project_history']
    assert actual['diagnostic_refs'] == ['evolution/attempts/attempt_3/diagnostics.json']
    assert 'selected_proposal' not in actual
    assert result['baseline_code']['excerpt'] and result['baseline_code']['original_chars'] == 7000


def test_memory_index_does_not_invent_a_completed_execution(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task='memory-no-measurement', project='parser')
    result = archive_execution_memory(run, attempt=1)
    assert result['status'] == 'not_available'
    assert result['reason'] == 'This custom run store has no global project-memory grant'
    assert not (run.root / 'memory/execution_archive_1.json').exists()


def test_each_candidate_must_meet_all_criteria_against_its_control() -> None:
    result = evaluate_candidates(contract(), [row('baseline', 5), row('candidate_a', 4), row('candidate_b', 1, 101)])
    assert result[0]['passed'] is True
    assert result[1]['passed'] is False
    assert result[0]['checks'][0]['observed'] == -1


def test_passing_candidates_are_still_ranked_by_actual_objective_value() -> None:
    results = evaluate_candidates(contract(), [row('baseline', 5), row('candidate_a', 4), row('candidate_b', 2)])
    assert all(item['passed'] for item in results)
    assert min(results, key=candidate_priority)['experiment_id'] == 'candidate_b'


def test_current_batch_is_not_a_list_of_future_adaptive_rounds() -> None:
    spec = contract(protocol={'seed': 2026, 'budget_unit': 'epochs', 'max_iters': 2})
    base = {'seed': 2026, 'budget_unit': 'epochs', 'max_iters': 2}
    good = {'ablations': [{'name': 'baseline', 'config': {**base, 'config_path': 'baseline.yaml'}},
                         {'name': 'candidate', 'config': {**base, 'config_path': 'candidate.yaml'}}]}
    assert evolution_plan_errors(spec, good) == []
    bad = {'ablations': [*good['ablations'], {'name': 'future_round', 'config': {**base, 'lr': 'determine later'}}]}
    errors = evolution_plan_errors(spec, bad)
    assert any('future rounds' in error for error in errors)
    assert any('fixed job overrides' in error for error in errors)
    wrong_source = {'ablations': [good['ablations'][0], {'name': 'candidate', 'config': {**base,
        'config_path': 'baseline.yaml', 'candidate_config': 'candidate.yaml'}}]}
    assert any('actual training configuration' in error for error in evolution_plan_errors(spec, wrong_source))


def test_generic_candidate_label_is_bound_by_the_real_configuration_not_its_spelling() -> None:
    spec = contract(candidate_pattern='registered_variant*')
    jobs: list[dict[str, Any]] = [{'name': 'baseline', 'config': {'config_path': 'baseline.yaml'}},
            {'name': 'candidate', 'config': {'config_path': 'candidate.yaml'}}]
    aliases = bound_candidate_aliases(spec, jobs)
    assert aliases == frozenset({'candidate'}) and evolution_plan_errors(spec, {'ablations': jobs}) == []
    assert not evaluate_candidates(spec, [row('baseline', 5), row('candidate', 3)])
    assert evaluate_candidates(spec, [row('baseline', 5), row('candidate', 3)], bound_aliases=aliases)[0]['passed']
    jobs[1]['config']['config_path'] = 'baseline.yaml'
    assert not bound_candidate_aliases(spec, jobs) and evolution_plan_errors(spec, {'ablations': jobs})


def test_parameter_reduction_is_calculated_from_measured_counts() -> None:
    authored = {'candidates': [{'experiment_id': 'authored-parser-case', 'checks': [
        {'name': 'real_parameters', 'value': 17216, 'baseline': 19264}]}]}
    computed = _feedback_card(authored)['computed_comparisons'][0]
    assert computed['candidate_minus_baseline'] == -2048
    assert computed['reduction_percent'] == pytest.approx(10.631229235880399)


@pytest.mark.parametrize('value', [None, True, float('nan'), float('inf'), '4'])
def test_missing_or_nonfinite_metrics_never_pass(value: Any) -> None:
    result = evaluate_candidates(contract(), [row('baseline', 5), row('candidate', value)])
    assert result[0]['passed'] is False
    json.dumps(result, allow_nan=False)


def test_baseline_and_candidate_selectors_cannot_be_mixed_into_an_average() -> None:
    assert evaluate_candidates(contract(), [row('baseline', 1)]) == []
    assert not evaluate_candidates(contract(), [row('candidate', 0)])[0]['passed']
    inputs = [row('baseline', 5), row('candidate', 3)]
    inputs[1]['is_mock'] = True
    assert evaluate_candidates(contract(), inputs) == []


def test_all_declared_controls_are_required_before_goal_attainment() -> None:
    spec = contract(baseline_names=['baseline', 'baseline_seed2'])
    assert not evaluate_candidates(spec, [row('baseline', 5), row('candidate', 3)])[0]['passed']
    assert evaluate_candidates(spec, [row('baseline', 5), row('baseline_seed2', 4), row('candidate', 3)])[0]['passed']


def test_checkpoint_spectrum_is_computed_from_real_tensor_bytes(tmp_path: Path) -> None:
    torch = pytest.importorskip('torch')
    from app.execution.evolution_evidence import coefficient_diagnostics
    path = tmp_path / 'matrix.pth'
    torch.save({'state_dict': {'known_matrix': torch.diag(torch.tensor([3., 1., 0.])),
                             'empty': torch.empty(0)}}, path)
    before = path.read_bytes()
    result = coefficient_diagnostics(path)
    assert result['known_matrix']['singular_values'] == [3., 1., 0.]
    assert result['known_matrix']['numerical_rank'] == 2
    assert result['known_matrix']['leading_energy_fraction'] == pytest.approx(.9)
    assert result['empty']['numel'] == 0
    assert path.read_bytes() == before


def test_resource_ratio_uses_actual_baseline_and_zero_baseline_cannot_pass() -> None:
    spec = contract(criteria=[{'name': 'parameters', 'direction': 'lte', 'target': .8, 'baseline_ratio': True}])
    assert evaluate_candidates(spec, [row('baseline', 1, 100), row('candidate', 1, 80)])[0]['passed']
    assert not evaluate_candidates(spec, [row('baseline', 1, 0), row('candidate', 1, 0)])[0]['passed']


def test_python_scope_preserves_other_methods_imports_and_helpers() -> None:
    scope = (ModuleScope(module=EvolutionModule.FORWARD, path='model.py', symbols=('Model.forward',)),)
    old = 'import math\nclass Model:\n def forward(self,x): return x\n def backward(self,x): return x\n'
    validate_module_content(scope, 'model.py', old, old.replace('forward(self,x): return x', 'forward(self,x): return x*2'))
    for changed in [old.replace('backward(self,x): return x', 'backward(self,x): return 0'),
                    old + '\ndef extra(): return 3\n', old.replace('import math', 'import os')]:
        with pytest.raises(ValueError, match='frozen'):
            validate_module_content(scope, 'model.py', old, changed)


def test_yaml_scope_preserves_seed_split_and_epoch_unit(tmp_path: Path) -> None:
    spec = contract()
    old = 'optimizer:\n  lr: 0.001\nseed: 2026\nepochs: 2\n'
    with bind_evolution_writes(tmp_path, spec.modules, {'candidate.yaml': old}):
        check_evolution_write(tmp_path, 'candidate.yaml', old.replace('0.001', '0.0005'))
        for changed in [old.replace('2026', '2027'), old.replace('epochs: 2', 'steps: 2')]:
            with pytest.raises(ValueError):
                check_evolution_write(tmp_path, 'candidate.yaml', changed)
        with pytest.raises(ValueError):
            check_evolution_write(tmp_path, 'evaluate.py', 'pass')
    check_evolution_write(tmp_path, 'other.py', 'unbound normal coding remains available')


@pytest.mark.parametrize('path', ['../escape.py', '/outside.py', '.git/config', '.env'])
def test_evolution_paths_cannot_escape_or_access_credentials(path: str) -> None:
    with pytest.raises(ValueError):
        contract(modules=[{'module': 'loss', 'path': path}])


def test_frozen_contract_uses_sql_authority_and_detects_edits(tmp_path: Path) -> None:
    run = RunStore(tmp_path).create(task='contract_parser', project='parser')
    spec = contract()
    checksum = freeze_evolution(run, spec.model_dump(mode='json'))
    RunStateStore(run).write(graph=build_pipeline(), status='created', request={
        'project': run.project, 'task': run.task, 'entrypoint': run.entrypoint,
        'extra': {'evolution_contract_sha256': checksum}})
    assert load_evolution(run) == spec
    path = run.root / 'input/evolution_contract.v1.json'
    value = json.loads(path.read_text())
    value['contract']['criteria'][0]['target'] = 999
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='changed'):
        load_evolution(run)
    path.unlink()
    with pytest.raises((ValueError, OSError)):
        load_evolution(run)


def test_native_auto_research_contract_resolves_all_nested_schema_references() -> None:
    schema = commander_tool_input_schema('run.auto_research', {})
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate({'user_request': 'Find an improved training setting',
        'evolution': contract().model_dump(mode='json')})


def test_report_delivery_binds_the_actual_project_id_instead_of_a_display_name() -> None:
    from app.agents.base import RunRequest
    from app.agents.writing.agent import WritingAgent
    schema = WritingAgent().submission_schema(RunRequest(project='folder_authored_identity', user_request='Summarize archived results'))
    assert schema is not None and schema['properties']['project']['const'] == 'folder_authored_identity'


def test_original_user_epoch_and_seed_correct_incomplete_model_protocol() -> None:
    actual = bind_user_protocol({'training_unit': 'epochs', 'seed': 2026},
                               ['完整训练2个epoch，不是2次更新，固定seed=2026'])
    assert actual['budget_unit'] == 'epochs' and actual['max_iters'] == 2 and actual['seed'] == 2026
    actual = bind_user_protocol({'budget_unit': 'steps', 'max_iters': 2, 'seed': 7},
                               ['完整训练2个epoch，不是2次更新，固定seed=2026'])
    assert actual['budget_unit'] == 'epochs' and actual['seed'] == 2026


def test_search_iterations_are_independent_of_training_epochs() -> None:
    actual = bind_user_protocol({'budget_unit': 'epochs', 'seed': 2026},
        ['每组完整训练2个epoch，第一轮先比较，最多5轮仿真，连续3轮未改善停止，固定seed=2026'])
    assert actual['budget_unit'] == 'epochs' and actual['max_iters'] == 2
