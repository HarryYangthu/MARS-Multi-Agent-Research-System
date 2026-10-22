"""Connect audited research, governed coding, real simulation and goal feedback.

Uses the existing run graph and approval/recovery mechanisms. No parallel task
engine, simulated scores or LLM declarations of scientific success are used.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from app.harness.agent_loop.trace import atomic_json
from app.harness.evolution import (EvolutionContract, ModuleScope, bind_evolution_writes,
    bound_candidate_aliases, candidate_priority, evaluate_candidates, validate_module_content)
from app.harness.runtime.project_scope import safe_scope_path
from app.harness.schema.frontmatter_parser import parse
from app.harness.tools.project_repo import load_project_repo, load_run_project_repo
from app.storage.run_state_store import RunStateStore
from app.storage.run_store import RunHandle


def bind_user_protocol(protocol: dict[str, Any], user_texts: list[str]) -> dict[str, Any]:
    """Reuse the existing host check; correct only explicit user budget/seed."""
    from app.bridge.commander_existing import ExistingIntentValidationError, validate_existing_user_intent
    bound = dict(protocol)
    requested = {'budget_unit': bound.get('budget_unit', 'steps'),
                 'max_iters': bound.get('max_iters', 1), 'seed': bound.get('seed')}
    for _ in range(3):
        try:
            validate_existing_user_intent({'requested': requested, 'default_budget': protocol,
                'default_seed': protocol.get('seed'), 'ordinary_coding': True}, user_texts,
                allow_unqualified_rounds=False)
            bound.update(budget_unit=requested['budget_unit'], max_iters=requested['max_iters'])
            if requested['seed'] is not None:
                bound['seed'] = requested['seed']
            return bound
        except ExistingIntentValidationError as exc:
            details = exc.structured_fields().get('details', {})
            if isinstance(details.get('expected_budget'), dict):
                requested.update(details['expected_budget'])
            elif type(details.get('expected_seed')) is int:
                requested['seed'] = details['expected_seed']
            else:
                raise
    raise ValueError('Could not bind a consistent training protocol from the original request')


def freeze_evolution(run: RunHandle, raw: object) -> str:
    contract = EvolutionContract.model_validate(raw)
    root = run.root / 'evolution'
    root.mkdir(exist_ok=True)
    atomic_json(run.root / 'input/evolution_contract.v1.json', {
        'run_id': run.run_id, 'project': run.project, 'sha256': contract.sha256(),
        'contract': contract.model_dump(mode='json')})
    atomic_json(root / 'state.json', {'schema': 'evolution_state.v1', 'run_id': run.run_id,
        'project': run.project, 'contract_sha256': contract.sha256(), 'status': 'researching',
        'created_at': datetime.now(timezone.utc).isoformat(), 'attempts': [], 'best': None,
        'verification_count': 0, 'stagnant_rounds': 0})
    return contract.sha256()


def load_evolution(run: RunHandle) -> EvolutionContract | None:
    path = run.root / 'input/evolution_contract.v1.json'
    if not path.exists() and not (run.root / 'evolution').exists():
        # Ordinary simulation requests do not acquire research-loop overhead.
        return None
    snapshot = RunStateStore(run).load()
    extra = snapshot.request.get('extra', {}) if snapshot is not None else {}
    expected = extra.get('evolution_contract_sha256') if isinstance(extra, dict) else None
    if expected is None and not path.exists():
        raise ValueError('Evolution task is missing its frozen contract')
    if expected is None or path.is_symlink():
        raise ValueError('Evolution contract has no matching task authority')
    raw = json.loads(path.read_text())
    contract = EvolutionContract.model_validate(raw['contract'])
    if (raw['run_id'] != run.run_id or raw['project'] != run.project
            or raw['sha256'] != expected or contract.sha256() != expected):
        raise ValueError('Evolution goal or module boundary changed after task creation')
    return contract


def evolution_view(run: RunHandle) -> dict[str, Any] | None:
    contract = load_evolution(run)
    if contract is None:
        return None
    state = _state(run)
    public = {key: value for key, value in state.items() if key != 'attempts'}
    public['attempts'] = [{key: value for key, value in card.items() if key not in {
        'diagnostic_summary', 'memory_archive'}} for card in state['attempts']]
    selection = run.root / 'evolution/handoff.json'
    if selection.is_file():
        record = json.loads(selection.read_text())
        approval = run.root / 'evolution/selection_approval.json'
        approved = json.loads(approval.read_text()) if approval.is_file() else {}
        public['proposal'] = {'mechanism': record['mechanism_hypothesis']['statement'],
            'modules': record['allowed_modules'], 'ranking_ref': record['ranking_ref'],
            'approval_recorded': approved.get('handoff_sha256') == hashlib.sha256(selection.read_bytes()).hexdigest()}
    return {**public, 'contract': contract.model_dump(mode='json'),
            'evidence_ref': 'evolution/state.json'}


def _state(run: RunHandle) -> dict[str, Any]:
    value: dict[str, Any] = json.loads((run.root / 'evolution/state.json').read_text())
    if value['run_id'] != run.run_id or value['project'] != run.project:
        raise ValueError('Evolution state belongs to another task')
    return value


def _save(run: RunHandle, state: dict[str, Any]) -> None:
    atomic_json(run.root / 'evolution/state.json', state)


def _scopes(run: RunHandle, contract: EvolutionContract) -> tuple[ModuleScope, ...]:
    handoff = _handoff(run)
    if handoff is None:
        return contract.modules
    selected = tuple(ModuleScope.model_validate(item) for item in handoff['allowed_modules'])
    if not selected or any(scope not in contract.modules for scope in selected):
        raise ValueError('Selected proposal exceeds the frozen evolution scope')
    return selected


def _handoff(run: RunHandle, *, allow_pending: bool = False) -> dict[str, Any] | None:
    path = run.root / 'evolution/handoff.json'
    if not path.is_file():
        return None
    value: dict[str, Any] = json.loads(path.read_text())
    approved = run.root / 'idea/idea_proposal.approved.md'
    if approved.exists() and hashlib.sha256(approved.read_bytes()).hexdigest() != value['proposal_sha256']:
        original = safe_scope_path(run.root, value.get('audited_proposal_ref',
            f"evolution/planning/attempt_{value['attempt']}/audited_research.md"), must_exist=True)
        before, after = parse(original.read_text()).metadata, parse(approved.read_text()).metadata
        important = ('method_spec', 'decision_rule', 'evaluation_protocol', 'handoff', 'hypothesis',
                     'research_question', 'research_assessment', 'research_links')
        if any(before.get(key) != after.get(key) for key in important):
            if allow_pending:
                snapshot = RunStateStore(run).load()
                if snapshot is not None and any(node.metadata.get('stage', key.split('_attempt_')[0]) == 'idea'
                        and node.metadata.get('attempt', 1) == value['attempt'] and node.state.value in {'running', 'waiting_review'}
                        for key, node in snapshot.graph.nodes.items()):
                    return {**value, 'approval_pending': True}
            raise ValueError('Approved research mechanism or protocol changed; review its evolution selection again')
    if approved.is_file():
        atomic_json(run.root / 'evolution/selection_approval.json', {
            'proposal_sha256': hashlib.sha256(approved.read_bytes()).hexdigest(),
            'handoff_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'hypothesis_id': value['mechanism_hypothesis']['hypothesis_id'],
            'source': 'actual approved research artifact', 'scientific_goal_met': False})
    return value


@contextmanager
def evolution_code_scope(run: RunHandle, *, stage: str, attempt: int) -> Iterator[None]:
    contract = load_evolution(run)
    if contract is None or stage not in {'coding', 'execution'}:
        yield
        return
    repo = load_project_repo(run.project)
    root = run.root / 'evolution'
    if repo.git_branch is not None:
        reference = root / 'baseline_code'
        binding_path = root / 'baseline_binding.json'
        if not binding_path.exists():
            from app.harness.tools.git_branch import git
            initial = repo.git_branch.initial_commit or repo.git_branch.baseline_commit
            if not reference.exists():
                git(root, 'clone', '--local', '--no-hardlinks', '--single-branch', '--no-checkout',
                    str(repo.root), str(reference))
            git(reference, 'checkout', '--detach', initial)
            atomic_json(binding_path, {'schema': 'evolution_baseline_source.v1', 'project': run.project,
                'run_id': run.run_id, 'contract_sha256': contract.sha256(), 'baseline_names': contract.baseline_names,
                'commit': initial, 'source': 'immutable initial research snapshot; no network fetch'})
    frozen = root / 'source_manifest.json'
    if not frozen.exists():
        originals = {scope.path: _source(repo.root, scope.path) for scope in contract.modules}
        protected: dict[str, str] = {}
        protected_roots: dict[str, str] = {}
        from app.harness.project_workspace import folder_project
        folder = folder_project(run.project)
        for path in contract.protected_paths:
            base = repo.root
            if not (base / path).exists() and folder is not None and (folder.root / path).exists():
                base = folder.root
            protected[path] = _hash_file(base, path)
            protected_roots[path] = str(base.resolve())
        atomic_json(frozen, {'root': str(repo.root), 'originals': originals, 'protected': protected,
                            'protected_roots': protected_roots})
    manifest = json.loads(frozen.read_text())
    if manifest['root'] != str(repo.root):
        raise ValueError('Evolution source workspace changed')
    for path, expected in manifest['protected'].items():
        if _hash_file(Path(manifest.get('protected_roots', {}).get(path, str(repo.root))), path) != expected:
            raise ValueError('Frozen baseline/evaluator changed: ' + path)
    scopes = _scopes(run, contract)
    if stage == 'coding':
        before = root / 'coding_inputs' / f'attempt_{attempt}.json'
        if not before.exists():
            atomic_json(before, {'attempt': attempt, 'code': {scope.path: _source(repo.root, scope.path)
                                                           for scope in contract.modules}})
    current_originals = {scope.path: _source(repo.root, scope.path) for scope in contract.modules}
    with bind_evolution_writes(repo.root, scopes, current_originals):
        yield
        for path, old in current_originals.items():
            selected = tuple(scope for scope in scopes if scope.path == path)
            content = _source(repo.root, path)
            if selected:
                validate_module_content(selected, path, old, content)
            elif old != content:
                raise ValueError('This proposal did not select the changed module: ' + path)
        for scope in contract.modules:
            validate_module_content(contract.modules, scope.path, manifest['originals'][scope.path],
                                    _source(repo.root, scope.path))
        if repo.git_branch is not None:
            from app.harness.tools.git_branch import changed_files
            changed = changed_files(repo.root, repo.git_branch.initial_commit or repo.git_branch.baseline_commit)
            unexpected = changed - {scope.path for scope in contract.modules}
            if unexpected:
                raise ValueError('Evolution changed files outside its frozen modules: ' + ', '.join(sorted(unexpected)))


def _source(root: Path, path: str) -> str | None:
    target = safe_scope_path(root, path)
    return target.read_text() if target.is_file() else None


def _hash_file(root: Path, path: str) -> str:
    from app.harness.runtime.research_contract import relative_scope
    target = root / relative_scope(path)
    if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()) or not target.exists():
        raise ValueError('Frozen source or project data is unavailable: ' + path)
    if target.is_file():
        with target.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()
    # A data directory is one frozen input tree, not a file to open as bytes.
    files: list[tuple[str, str]] = []
    for child in sorted(target.rglob('*')):
        if child.is_symlink():
            raise ValueError('Frozen input tree cannot traverse symbolic links')
        if child.is_file():
            with child.open('rb') as stream:
                files.append((child.relative_to(target).as_posix(), hashlib.file_digest(stream, 'sha256').hexdigest()))
    return hashlib.sha256(json.dumps(files).encode()).hexdigest()


def evolution_context(run: RunHandle, *, attempt: int, stage: str) -> str | None:
    contract = load_evolution(run)
    if contract is None:
        return None
    state = _state(run)
    handoff = _handoff(run, allow_pending=stage == 'idea')
    context = {'contract': contract.model_dump(mode='json'), 'stage': stage, 'attempt': attempt,
        'current_iteration': len(state['attempts']) + 1,
        'last_design_repair': state.get('last_design_repair'),
        'selected_proposal': handoff, 'best': state['best'],
        'recent_evidence': [_feedback_card(card) for card in state['attempts'][-3:]],
        'instructions': [
            'This is outer code evolution. LLM weights remain fixed; training updates model weights internally.',
            'The selected handoff takes precedence over alternative research proposals. Change only its selected modules.',
            'Keep a matched frozen baseline in every execution batch. Use the declared candidate pattern for variant names.',
            'Epochs and optimizer updates are different units. Preserve data split, evaluation and seed conventions.',
            'Use actual measurements to identify a gap, give competing explanations, and make one discriminating change.',
            'The attempt is an internal graph ID; current_iteration counts actual simulation batches. Preparation retries do not consume training rounds.',
            'The experiment matrix is only the current batch. Future adaptive rounds and verification are controller policy, never scheduled rows.',
            'If the mechanism is falsified, return an explicit replan reason. Never change a goal to claim success.',
            'A passing candidate is repeated unchanged before goal_met. Preserve best and final checkpoints separately.',
            'Use repository readers and archived evidence references rather than inventing past results.']}
    from app.harness.tools.execution.handoff import execution_delivery_context
    context['registered_execution'] = execution_delivery_context()
    from app.storage.research_memory import memory_context
    try:
        context['related_project_history'] = memory_context(run.project, contract.goal, run.root,
                                                           f'evolution-{attempt}-{stage}')
    except Exception as error:
        context['history_retrieval'] = {'status': 'unavailable', 'error_type': type(error).__name__,
            'message': str(error)[:300], 'instruction': 'Use current verified evidence; history was not retrieved.'}
    if stage == 'coding' and state['best'] is not None:
        best_path = safe_scope_path(run.root, state['best']['code_ref'], must_exist=True)
        context['best_parent_source'] = json.loads(best_path.read_text())
        context['parent_selection'] = (
            'Start from the best measured parent above; propose one discriminating change. '
            'Apply changes through the governed code tools. The frozen module boundaries still apply.')
    return json.dumps(context, ensure_ascii=False, allow_nan=False)


def archive_execution_memory(run: RunHandle, *, attempt: int) -> dict[str, Any]:
    """Index ordinary simulations too, without invalidating completed training."""
    from app.bridge.research_run_service import load_run_research_contract
    if load_run_research_contract(run) is not None:
        return {'status': 'not_available', 'reason': 'This sealed research namespace has no global project-memory grant'}
    from app.harness.project_storage import project_storage_root
    from app.settings import repo_root
    owned = project_storage_root(run.project, 'runs', repo_root() / 'runs') / run.run_id
    if run.root.resolve() != owned.resolve():
        return {'status': 'not_available', 'reason': 'This custom run store has no global project-memory grant'}
    jobs = sorted((run.root / 'execution/jobs').glob('*.json'))
    current = [path for path in jobs if json.loads(path.read_text()).get('attempt') == attempt]
    if not current or any(json.loads(path.read_text()).get('status') != 'completed' for path in current):
        return {'status': 'not_available', 'reason': 'No complete measured execution batch'}
    identity = hashlib.sha256(b''.join(path.name.encode() + path.read_bytes() for path in current)).hexdigest()
    receipt = run.root / 'memory' / f'execution_archive_{attempt}.json'
    if receipt.is_file():
        try:
            saved = json.loads(receipt.read_text())
        except (OSError, ValueError):
            # This is an index receipt, not scientific evidence. Preserve its
            # bytes and rebuild the index from the original completed jobs.
            content = receipt.read_bytes()
            retained = receipt.with_suffix('.invalid-' + hashlib.sha256(content).hexdigest() + '.json')
            if not retained.exists():
                retained.write_bytes(content)
            saved = {}
        if saved.get('jobs_sha256') == identity and saved.get('status') == 'completed':
            return dict(saved['result'])
    try:
        from app.storage.research_memory import archive_job
        result = archive_job(run.project, run.root)
        atomic_json(receipt, {'status': 'completed', 'jobs_sha256': identity, 'result': result})
        return result
    except Exception as error:
        result = {'status': 'pending', 'error_type': type(error).__name__, 'message': str(error)[:300]}
        atomic_json(receipt, {'jobs_sha256': identity, **result})
        return result


def _feedback_card(card: dict[str, Any]) -> dict[str, Any]:
    """Keep decision facts visible without sending entire tensor spectra as text."""
    result = {key: value for key, value in card.items() if key not in {'diagnostic_summary', 'memory_archive'}}
    result['computed_comparisons'] = []
    for candidate in card.get('candidates', []):
        for check in candidate.get('checks', []):
            value, baseline = check.get('value'), check.get('baseline')
            if value is not None and baseline is not None:
                comparison = {'experiment_id': candidate['experiment_id'], 'metric': check['name'],
                    'candidate': value, 'baseline': baseline, 'candidate_minus_baseline': value - baseline}
                if check['name'] in {'real_parameters', 'parameters', 'parameter_count', 'params'} and baseline != 0:
                    comparison['reduction_percent'] = 100 * (baseline - value) / abs(baseline)
                result['computed_comparisons'].append(comparison)
    result['diagnostics'] = []
    for item in card.get('diagnostic_summary', []):
        weights = {checkpoint: {name: {key: value for key, value in tensor.items() if key in {
            'shape', 'finite', 'l2_norm', 'max_magnitude', 'numerical_rank', 'leading_energy_fraction', 'matrix_definition'}}
            for name, tensor in tensors.items()} for checkpoint, tensors in item.get('weights', {}).items()}
        result['diagnostics'].append({'experiment_id': item['experiment_id'],
            'training_curve': item.get('training_curve'), 'coefficient_summary': weights,
            'warnings': item.get('warnings', []), 'full_diagnostics_ref': card.get('diagnostics_ref')})
    return result


def _protocol_errors(run: RunHandle, contract: EvolutionContract) -> list[str]:
    document = run.root / 'coding/code_spec.approved.md'
    if not document.is_file():
        return ['No approved coding delivery']
    jobs = parse(document.read_text()).metadata.get('execution_jobs', [])
    errors: list[str] = []
    for job in jobs:
        config = job.get('config', {})
        for key, value in contract.protocol.items():
            if key in {'budget_unit', 'max_iters', 'seed', 'data_path', 'entrypoint', 'command_id'} and config.get(key) != value:
                errors.append(str(job.get('name')) + ': frozen protocol differs at ' + key)
    return errors


def record_execution(run: RunHandle, *, attempt: int) -> dict[str, Any]:
    """Durable, idempotent decision made from this attempt's real output."""
    contract = load_evolution(run)
    if contract is None:
        raise ValueError('No evolution contract')
    state = _state(run)
    existing = next((item for item in state['attempts'] if item['attempt'] == attempt), None)
    for card in state['attempts']:
        if card.get('memory_index_status') == 'pending' and card.get('diagnostics_ref'):
            _index_iteration(run, contract, state, card)
    if existing is not None:
        return dict(existing['decision'])
    rows = json.loads((run.root / 'execution/metrics.json').read_text())
    batch = json.loads((run.root / 'execution/batch_summary.json').read_text())
    if batch.get('attempt') != attempt or batch.get('failures'):
        raise ValueError('Evolution requires a successful execution batch for the current attempt')
    from app.execution.evolution_evidence import verified_iteration_evidence
    diagnostics = verified_iteration_evidence(run.root, attempt, rows)
    repo = load_run_project_repo(run.project, run.root, run.run_id)
    code = {scope.path: _source(repo.root, scope.path) for scope in contract.modules}
    code_hash = hashlib.sha256(json.dumps(code, sort_keys=True).encode()).hexdigest()
    verification_key = hashlib.sha256(json.dumps({'code': code_hash, 'protocol': contract.protocol,
        'inputs': {item['experiment_id']: item.get('scientific_inputs', []) for item in diagnostics}},
        sort_keys=True).encode()).hexdigest()
    jobs = parse((run.root / 'coding/code_spec.approved.md').read_text()).metadata.get('execution_jobs', [])
    results = evaluate_candidates(contract, rows, bound_aliases=bound_candidate_aliases(contract, jobs))
    protocol_errors = _protocol_errors(run, contract)
    if not results:
        protocol_errors.append('No completed candidate matched the frozen experiment selector')
    eligible = [item for item in results if item['baseline_present']]
    ranked = sorted(eligible, key=lambda item: (*candidate_priority(item), item['experiment_id']))
    winner = ranked[0] if ranked else None
    passed = winner is not None and winner['passed'] and not protocol_errors
    previous = state['attempts'][-1] if state['attempts'] else None
    iteration = len(state['attempts']) + 1
    repeat = bool(passed and winner is not None and previous and previous['passed'] and previous.get('verification_key') == verification_key
                  and previous['winner']['experiment_id'] == winner['experiment_id'])
    count = state['verification_count'] + 1 if repeat else (1 if passed else 0)
    state['verification_count'] = count
    score = winner['score'] if winner is not None else None
    best = state['best']
    improved = bool(winner is not None and not protocol_errors and (best is None
                    or candidate_priority(winner) < candidate_priority(best['winner'])))
    archive = run.root / 'evolution/attempts' / f'attempt_{attempt}'
    archive.mkdir(parents=True, exist_ok=True)
    atomic_json(archive / 'metrics.json', rows)
    atomic_json(archive / 'batch_summary.json', batch)
    atomic_json(archive / 'code.json', code)
    atomic_json(archive / 'diagnostics.json', diagnostics)
    if improved:
        state['best'] = {'attempt': attempt, 'score': score, 'winner': winner,
            'code_sha256': code_hash, 'code_ref': str((archive / 'code.json').relative_to(run.root)),
            'metrics_ref': str((archive / 'metrics.json').relative_to(run.root))}
    state['stagnant_rounds'] = 0 if improved else state['stagnant_rounds'] + (0 if passed else 1)
    elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(state['created_at'])).total_seconds()
    if passed and count >= contract.verification_runs:
        status, target, reason = 'goal_met', 'writing', 'All objective and constraint checks passed in repeated unchanged executions.'
    elif iteration >= contract.max_iterations or elapsed >= contract.max_wall_seconds:
        status, target, reason = 'budget_exhausted', 'writing', 'The bounded research budget ended before verified goal attainment.'
    elif not passed and state['stagnant_rounds'] >= contract.patience:
        status, target, reason = 'plateau', 'writing', 'No measured improvement within the declared patience budget.'
    elif passed:
        status, target, reason = 'verifying', 'execution', 'Repeat the passing code unchanged; the goal is not yet verified.'
    else:
        replan = state['stagnant_rounds'] >= contract.replan_after
        status, target, reason = ('researching', 'idea', 'Evidence no longer supports continued local tuning; reconsider the mechanism.') if replan else (
            'evolving', 'coding', 'Measured gap remains. Make a focused change within the selected modules using the evidence card.')
    decision = {'status': status, 'target': target, 'continue': target != 'writing', 'next_attempt': attempt + 1,
                'reason': reason, 'verified_goal_met': status == 'goal_met'}
    card = {'attempt': attempt, 'iteration': iteration, 'passed': passed, 'winner': winner, 'candidates': results,
        'protocol_errors': protocol_errors, 'code_sha256': code_hash, 'verification_key': verification_key,
        'code_ref': str((archive / 'code.json').relative_to(run.root)),
        'metrics_ref': str((archive / 'metrics.json').relative_to(run.root)), 'decision': decision,
        'diagnostics_ref': str((archive / 'diagnostics.json').relative_to(run.root)),
        'diagnostic_summary': [{key: value for key, value in item.items() if key != 'files'} for item in diagnostics],
        'facts': 'Checks are calculated from completed real simulation metrics. Mechanistic explanations remain hypotheses.',
        'limitations': 'Repeatability within this frozen protocol does not establish broader generalization.'}
    atomic_json(archive / 'evidence_card.json', card)
    state['attempts'].append(card)
    state['status'] = status
    state['last_decision'] = decision
    _save(run, state)
    _index_iteration(run, contract, state, card)
    return decision


def _index_iteration(run: RunHandle, contract: EvolutionContract, state: dict[str, Any], card: dict[str, Any]) -> None:
    # Shared project memory keeps both successful and unsuccessful measurements.
    # Scope the note to this attempt; do not relabel earlier measurements or
    # overwrite the project's separately selected original/current-best baseline.
    attempt = card['attempt']
    archive = run.root / 'evolution/attempts' / f'attempt_{attempt}'
    try:
        from app.storage.research_memory import ResearchMemory
        memory_result = archive_execution_memory(run, attempt=attempt)
        if memory_result.get('status') in {'pending', 'not_available'}:
            raise ValueError(str(memory_result))
        memory = ResearchMemory(run.project)
        diagnostics = json.loads(safe_scope_path(run.root, card['diagnostics_ref'], must_exist=True).read_text())
        current_sources = {item['path'] for diagnostic in diagnostics for item in diagnostic['files']}
        for identifier in memory_result.get('ids', []):
            record = memory.get(identifier)
            if any(asset.get('source') in current_sources for asset in record.get('evidence', [])):
                memory.annotate(identifier, {'hypothesis': (_handoff(run) or {}).get('mechanism_hypothesis', {}),
                    'conclusion': {'contract_sha256': contract.sha256(), 'attempt': attempt, 'status': card['decision']['status'],
                        'criteria': card['candidates'], 'evidence_ref': str((archive / 'evidence_card.json').relative_to(run.root))},
                    'limitations': card['limitations'], 'tags': ['auto_research', 'measured_iteration']})
        from app.storage.self_evolution_store import append_learning_event, read_jsonl
        event = {'event': 'evolution.measured_iteration', 'attempt': attempt,
            'status': card['decision']['status'], 'passed': card['passed'],
            'evidence_ref': str((archive / 'evidence_card.json').relative_to(run.root)),
            'contract_sha256': contract.sha256(), 'best': state['best']}
        previous_events = read_jsonl(run.root / 'memory/episode_memory.jsonl')
        if not any(item.get('event') == event['event'] and item.get('attempt') == attempt for item in previous_events):
            append_learning_event(run=run, event=event, memory_candidates=[])
        card['memory_archive'], card['memory_index_status'] = memory_result, 'completed'
    except Exception as error:
        # The verified local card is authoritative. A secondary index outage
        # must not turn completed measurements into a failed simulation.
        card['memory_index_status'] = 'pending'
        card['memory_archive'] = {'status': 'pending', 'error_type': type(error).__name__,
            'message': str(error)[:500], 'retry': 'next controller reconciliation'}
    atomic_json(archive / 'evidence_card.json', card)
    _save(run, state)


def record_failed_execution(run: RunHandle, *, attempt: int, error: str) -> dict[str, Any] | None:
    """An invalid candidate can evolve; a failed control is an execution problem.

    Keep failed nodes/receipts as failures. No performance is invented for them.
    """
    contract = load_evolution(run)
    if contract is None:
        return None
    state = _state(run)
    previous = next((card for card in state['attempts'] if card['attempt'] == attempt), None)
    if previous is not None:
        return dict(previous['decision'])
    jobs = [json.loads(path.read_text()) for path in (run.root / 'execution/jobs').glob('*.json')]
    jobs = [job for job in jobs if job.get('attempt') == attempt]
    controls = [job for job in jobs if job.get('experiment_id') in contract.baseline_names]
    invalid = [job for job in jobs if job.get('status') == 'failed' and job.get('experiment_id') not in contract.baseline_names]
    if (not invalid or {job.get('experiment_id') for job in controls} != set(contract.baseline_names)
            or any(job.get('status') != 'completed' for job in controls)
            or any(job.get('status') not in {'completed', 'failed'} for job in jobs)):
        return None
    from app.execution.evolution_evidence import verified_iteration_evidence
    rows = [{'experiment_id': job['experiment_id'], **job['result']} for job in controls]
    verified_iteration_evidence(run.root, attempt, rows)
    elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(state['created_at'])).total_seconds()
    state['stagnant_rounds'] += 1
    iteration = len(state['attempts']) + 1
    stopped = iteration >= contract.max_iterations or elapsed >= contract.max_wall_seconds
    plateau = state['stagnant_rounds'] >= contract.patience
    status, target = (('budget_exhausted', 'writing') if stopped else
                      ('plateau', 'writing') if plateau else ('evolving', 'coding'))
    decision = {'status': status, 'target': target, 'continue': target != 'writing', 'next_attempt': attempt + 1,
        'verified_goal_met': False, 'reason': 'Candidate execution failed; inspect its real error and repair the selected module. '
        'No candidate performance was measured.'}
    archive = run.root / 'evolution/attempts' / f'attempt_{attempt}'
    archive.mkdir(parents=True, exist_ok=True)
    atomic_json(archive / 'failed_jobs.json', jobs)
    card = {'attempt': attempt, 'iteration': iteration, 'passed': False, 'winner': None, 'candidates': [],
        'protocol_errors': [error], 'metrics_ref': '', 'decision': decision,
        'failure_ref': str((archive / 'failed_jobs.json').relative_to(run.root)),
        'facts': 'The control completed with verified evidence; the candidate failed without a valid metric.'}
    atomic_json(archive / 'evidence_card.json', card)
    state['attempts'].append(card)
    state['status'], state['last_decision'], state['verification_count'] = status, decision, 0
    _save(run, state)
    return decision
