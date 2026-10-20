"""Task-owned scientific goals and six-module evolution boundaries.

Model judgements prioritize experiments. Only measured metrics satisfy goals.
"""
from __future__ import annotations

import ast
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from enum import Enum
from fnmatch import fnmatchcase
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.harness.runtime.project_scope import forbidden_source_path, safe_scope_path
from app.harness.runtime.research_contract import relative_scope


class EvolutionModule(str, Enum):
    FORWARD = 'forward'
    LOSS = 'loss'
    BACKWARD = 'backward'
    OPTIMIZER = 'optimizer'
    TRAINING = 'training_strategy'
    NUMERICS = 'numerics'


class ModuleScope(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    module: EvolutionModule
    path: str
    symbols: tuple[str, ...] = ()
    yaml_keys: tuple[str, ...] = ()

    @field_validator('path')
    @classmethod
    def safe_path(cls, value: str) -> str:
        name = relative_scope(value)
        if forbidden_source_path(name):
            raise ValueError('Evolution cannot modify credentials or runtime control files')
        return name

    @model_validator(mode='after')
    def narrow_boundary(self) -> ModuleScope:
        if self.symbols and self.yaml_keys:
            raise ValueError('Choose Python symbols or YAML keys for one code boundary')
        if self.symbols and not self.path.endswith('.py'):
            raise ValueError('Symbol boundaries require a Python source file')
        if self.yaml_keys and not self.path.endswith(('.yaml', '.yml')):
            raise ValueError('YAML key boundaries require a YAML file')
        if any(not name or '*' in name for name in (*self.symbols, *self.yaml_keys)):
            raise ValueError('Module members must be explicit names')
        return self


class EvolutionMetric(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    name: str = Field(min_length=1)
    direction: Literal['lte', 'gte']
    target: float = Field(allow_inf_nan=False, description='Threshold in the declared comparison. '
        'For baseline_delta use candidate minus baseline: reduce by 0.5 means lte -0.5, increase by 0.5 means gte +0.5.')
    tolerance: float = Field(default=0, ge=0, allow_inf_nan=False)
    role: Literal['objective', 'constraint'] = 'objective'
    baseline_delta: bool = False
    baseline_ratio: bool = False

    @model_validator(mode='after')
    def comparison(self) -> EvolutionMetric:
        if self.baseline_delta and self.baseline_ratio:
            raise ValueError('Choose a baseline delta or ratio, not both')
        return self


class EvolutionContract(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    schema_id: Literal['evolution_contract.v1'] = 'evolution_contract.v1'
    goal: str = Field(min_length=8)
    criteria: tuple[EvolutionMetric, ...] = Field(min_length=1)
    modules: tuple[ModuleScope, ...] = Field(min_length=1)
    protected_paths: tuple[str, ...] = ()
    baseline_names: tuple[str, ...] = ('baseline',)
    candidate_pattern: str = 'candidate*'
    max_iterations: int = Field(default=6, ge=2, le=100)
    patience: int = Field(default=3, ge=1, le=100)
    verification_runs: int = Field(default=2, ge=2, le=10)
    initial_hypotheses: int = Field(default=3, ge=3, le=8)
    model_profile_ids: tuple[str, ...] = ()
    replan_after: int = Field(default=2, ge=1, le=20)
    max_wall_seconds: int = Field(default=3600, ge=60, le=86400)
    protocol: dict[str, Any] = Field(default_factory=dict)

    @field_validator('protected_paths')
    @classmethod
    def protected_relative(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(relative_scope(value) for value in values)

    @model_validator(mode='after')
    def coherent(self) -> EvolutionContract:
        if len(set(self.model_profile_ids)) > self.initial_hypotheses:
            raise ValueError('Each selected model needs at least one independent candidate session')
        if not self.baseline_names or not self.candidate_pattern.strip():
            raise ValueError('Explicit baseline and candidate selectors are required')
        if not any(rule.role == 'objective' for rule in self.criteria):
            raise ValueError('At least one objective metric is required')
        for scope in self.modules:
            if any(scope.path == p or scope.path.startswith(p + '/') for p in self.protected_paths):
                raise ValueError('A frozen evaluation or baseline path cannot also evolve')
        return self

    def sha256(self) -> str:
        return hashlib.sha256(json.dumps(self.model_dump(mode='json'), ensure_ascii=False,
            sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def bind_explicit_metric_deltas(contract: EvolutionContract, texts: list[str]) -> EvolutionContract:
    """Bind an explicit relative improvement in the user's own metric clause.

    An LLM supplies the structure, but cannot reverse the subtraction sign.
    Unspecified or ambiguous numeric goals remain unchanged for review.
    """
    criteria = []
    for rule in contract.criteria:
        corrected = rule
        for text in texts:
            for clause in re.split(r'[。；;\n，,]', text):
                name = re.search(r'(?<![A-Za-z_0-9])' + re.escape(rule.name) + r'(?![A-Za-z_0-9])', clause, re.IGNORECASE)
                if name is None or not (rule.baseline_delta or re.search(r'baseline|基线', clause, re.IGNORECASE)):
                    continue
                if re.search(r'最多|至多|不超过|at most|no more than', clause, re.IGNORECASE):
                    continue  # A tolerated reduction is not a required improvement.
                lower = re.search(r'(?:降低|减少|下降|reduce[d]?|decrease[d]?|lower)\s*(?:至少|by|at least|by at least)?\s*(\d+(?:\.\d+)?)',
                                  clause[name.end():], re.IGNORECASE)
                higher = re.search(r'(?:提高|增加|上升|increase[d]?|raise[d]?)\s*(?:至少|by|at least|by at least)?\s*(\d+(?:\.\d+)?)',
                                   clause[name.end():], re.IGNORECASE)
                if lower is None and re.search(r'(?:reduce|decrease|lower)\s*$', clause[:name.start()], re.IGNORECASE):
                    lower = re.search(r'\bby\s+(?:at least\s+)?(\d+(?:\.\d+)?)', clause[name.end():], re.IGNORECASE)
                match = lower or higher
                if match is not None and re.match(r'\s*(?:%|％|percent|[eE][+-]?\d)', clause[name.end() + match.end():]):
                    continue  # Ratios/scientific notation keep their explicit typed contract.
                tolerance = rule.tolerance if re.search(r'容差|误差|tolerance', clause, re.IGNORECASE) else 0.0
                if lower is not None and higher is None:
                    corrected = rule.model_copy(update={'direction': 'lte', 'target': -float(lower.group(1)), 'baseline_delta': True, 'baseline_ratio': False, 'tolerance': tolerance})
                elif higher is not None and lower is None:
                    corrected = rule.model_copy(update={'direction': 'gte', 'target': float(higher.group(1)), 'baseline_delta': True, 'baseline_ratio': False, 'tolerance': tolerance})
        criteria.append(corrected)
    return contract.model_copy(update={'criteria': tuple(criteria)})


def bound_candidate_aliases(contract: EvolutionContract, jobs: list[dict[str, Any]]) -> frozenset[str]:
    sources = {scope.path for scope in contract.modules if scope.yaml_keys}
    return frozenset('candidate' for job in jobs if job.get('name') == 'candidate'
        and job.get('name') not in contract.baseline_names and isinstance(job.get('config'), dict)
        and job['config'].get('config_path', job['config'].get('cfg')) in sources)


def evaluate_candidates(contract: EvolutionContract, rows: list[dict[str, Any]], *,
                        bound_aliases: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    """Evaluate each complete candidate separately; never blend controls/variants."""
    valid = [row for row in rows if row.get('status') == 'completed' and row.get('is_mock') is False
             and row.get('fingerprint_hash') and isinstance(row.get('metrics'), dict)]
    baselines = [row for row in valid if row.get('experiment_id') in contract.baseline_names]
    baseline_present = {row.get('experiment_id') for row in baselines} == set(contract.baseline_names)
    results: list[dict[str, Any]] = []
    for row in valid:
        name = str(row.get('experiment_id', ''))
        if name in contract.baseline_names or not (fnmatchcase(name, contract.candidate_pattern) or name in bound_aliases):
            continue
        checks: list[dict[str, Any]] = []
        for rule in contract.criteria:
            value = finite_metric(row['metrics'].get(rule.name))
            baseline_values = [finite_metric(item['metrics'].get(rule.name)) for item in baselines]
            known = [value for value in baseline_values if value is not None]
            baseline = (max(known) if rule.direction == 'gte' else min(known)) if known and len(
                known) == len(baseline_values) else None
            observed = value
            if rule.baseline_delta:
                observed = value - baseline if value is not None and baseline is not None else None
            elif rule.baseline_ratio:
                observed = value / baseline if value is not None and baseline is not None and baseline != 0 else None
            gap = (max(0.0, observed - rule.target - rule.tolerance) if rule.direction == 'lte'
                   else max(0.0, rule.target - rule.tolerance - observed)) if observed is not None else None
            checks.append({'name': rule.name, 'role': rule.role, 'value': value, 'baseline': baseline,
                'comparison': 'candidate_minus_baseline' if rule.baseline_delta else 'candidate_over_baseline' if rule.baseline_ratio else 'absolute',
                'observed': observed, 'target': rule.target, 'direction': rule.direction,
                'gap': gap, 'passed': gap == 0.0 if gap is not None else False})
        results.append({'experiment_id': name, 'metrics': {str(k): finite_metric(v) for k, v in row['metrics'].items()}, 'checks': checks,
            'passed': baseline_present and all(item['passed'] for item in checks),
            'baseline_present': baseline_present, 'fingerprint_hash': row['fingerprint_hash'],
            'score': sum(float(check['gap']) if check['gap'] is not None else 1e100 for check in checks)})
    return results


def candidate_priority(result: dict[str, Any]) -> tuple[float, ...]:
    """Hard constraints first, then the declared objective order and actual value.

    Passing a threshold does not make all passing candidates equally good.
    """
    constraints = [check for check in result['checks'] if check['role'] == 'constraint']
    objectives = [check for check in result['checks'] if check['role'] == 'objective']
    constraint_gap = sum(float(check['gap']) if check['gap'] is not None else 1e100 for check in constraints)
    observed = tuple((float(check['observed']) * (1 if check['direction'] == 'lte' else -1))
                     if check['observed'] is not None else 1e100 for check in objectives)
    return (float(not result['passed']), constraint_gap, *observed)


def evolution_plan_errors(contract: EvolutionContract, metadata: dict[str, Any]) -> list[str]:
    """One executable batch now; future adaptive rounds are policy, not jobs."""
    rows = metadata.get('ablations', [])
    if not isinstance(rows, list) or not rows:
        return ['Auto Research needs a concrete current-iteration matrix.']
    errors: list[str] = []
    names = {row.get('name') for row in rows if isinstance(row, dict)}
    if not set(contract.baseline_names).issubset(names):
        errors.append('Current batch must use the frozen control names: ' + ', '.join(contract.baseline_names))
    candidates = [row for row in rows if isinstance(row, dict) and row.get('name') not in contract.baseline_names
                  and (fnmatchcase(str(row.get('name', '')), contract.candidate_pattern)
                       or row.get('name') in bound_candidate_aliases(contract, rows))]
    if not candidates or len(rows) != len(candidates) + len(contract.baseline_names):
        errors.append('Matrix contains future rounds or wrong aliases. Include only the current controls and candidates matching '
            + contract.candidate_pattern + '; put future evolution/verification rules in prose, not ablations.')
    yaml_sources = {scope.path for scope in contract.modules if scope.yaml_keys}
    mutable_keys = {name.split('.')[-1] for scope in contract.modules for name in scope.yaml_keys}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('config'), dict):
            continue
        config = row['config']
        shadowed = mutable_keys.intersection(config) - {'seed', 'budget_unit', 'max_iters', 'budget_steps'}
        if shadowed:
            errors.append(str(row.get('name')) + ': evolved fields belong in the selected YAML, not fixed job overrides: '
                          + ', '.join(sorted(shadowed)))
        for key in ('budget_unit', 'max_iters', 'seed'):
            if key in contract.protocol and config.get(key) != contract.protocol[key]:
                errors.append(str(row.get('name')) + ': current batch differs from frozen protocol at ' + key)
        if row in candidates and yaml_sources and config.get('config_path', config.get('cfg')) not in yaml_sources:
            errors.append(str(row.get('name')) + ': bind config_path to an allowed candidate YAML: ' + ', '.join(sorted(yaml_sources))
                          + '. Invented candidate_config/lr_init job overrides do not change the actual training configuration.')
    return errors


def finite_metric(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        return None
    return float(value)


def _masked_python(text: str, symbols: tuple[str, ...]) -> str:
    tree = ast.parse(text)
    found: set[str] = set()

    def visit(body: list[ast.stmt], prefix: str = '') -> None:
        for index, node in enumerate(body):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + node.name
                if name in symbols:
                    body[index] = ast.Pass()
                    found.add(name)
                elif isinstance(node, ast.ClassDef):
                    visit(node.body, name + '.')
    visit(tree.body)
    if set(symbols) != found:
        raise ValueError('A selected Python code block is missing: ' + ', '.join(set(symbols) - found))
    return ast.dump(tree, include_attributes=False)


def _masked_yaml(text: str, keys: tuple[str, ...]) -> object:
    from app.harness.tools.code.yaml_copy import _parse
    value, _ = _parse(text)
    # Work on the parsed copy: formatting changes cannot alter frozen semantics.
    for name in keys:
        current = value
        parts = name.split('.')
        for part in parts[:-1]:
            if not isinstance(current, dict) or part not in current:
                raise ValueError('Selected YAML field is missing: ' + name)
            current = current[part]
        if not isinstance(current, dict) or parts[-1] not in current:
            raise ValueError('Selected YAML field is missing: ' + name)
        current[parts[-1]] = '<MARS_EVOLUTION_FIELD>'
    return value


def validate_module_content(scopes: tuple[ModuleScope, ...], path: str, original: str | None,
                            content: str | None) -> None:
    selected = [scope for scope in scopes if scope.path == path]
    if not selected:
        raise ValueError('This path is outside the selected evolution modules: ' + path)
    if any(not scope.symbols and not scope.yaml_keys for scope in selected):
        return
    if original is None or content is None:
        raise ValueError('A narrowed module file cannot be created or deleted: ' + path)
    symbols = tuple(dict.fromkeys(s for scope in selected for s in scope.symbols))
    keys = tuple(dict.fromkeys(s for scope in selected for s in scope.yaml_keys))
    left, right = ((_masked_python(original, symbols), _masked_python(content, symbols)) if symbols
                   else (_masked_yaml(original, keys), _masked_yaml(content, keys)))
    if left != right:
        raise ValueError('The edit changes frozen code blocks or YAML fields outside ' + path)


_WRITE_SCOPE: ContextVar[tuple[Path, tuple[ModuleScope, ...], dict[str, str | None]] | None] = ContextVar(
    'evolution_write_scope', default=None)


@contextmanager
def bind_evolution_writes(root: Path, scopes: tuple[ModuleScope, ...],
                          originals: dict[str, str | None]) -> Iterator[None]:
    token = _WRITE_SCOPE.set((root.resolve(), scopes, originals))
    try:
        yield
    finally:
        _WRITE_SCOPE.reset(token)


def check_evolution_write(root: Path, path: str, content: str | None) -> None:
    policy = _WRITE_SCOPE.get()
    if policy is None:
        return
    bound_root, scopes, originals = policy
    if root.resolve() != bound_root:
        raise ValueError('Evolution write belongs to a different repository')
    safe_scope_path(bound_root, path)
    validate_module_content(scopes, path, originals.get(path), content)


def frozen_baseline_root(run_root: Path, project: str, experiment_id: str) -> Path | None:
    """Host-owned control checkout. Models cannot select this execution root."""
    path = run_root / 'evolution/baseline_binding.json'
    if not path.exists():
        return None
    if path.is_symlink():
        raise ValueError('Frozen baseline binding cannot be symbolic')
    binding = json.loads(path.read_text())
    if binding['project'] != project or binding['run_id'] != run_root.name:
        raise ValueError('Baseline source belongs to a different research task')
    if experiment_id not in binding['baseline_names']:
        return None
    from app.harness.runtime.state_journal import StateJournal
    journal = StateJournal.from_authority(run_root, run_id=run_root.name)
    if journal is None or journal.read()['request']['extra'].get('evolution_contract_sha256') != binding['contract_sha256']:
        raise ValueError('Baseline source is not bound to the frozen research contract')
    root = run_root / 'evolution/baseline_code'
    if root.is_symlink() or not root.resolve().is_relative_to(run_root.resolve()):
        raise ValueError('Frozen baseline checkout escaped its research directory')
    from app.harness.tools.git_branch import git
    if git(root, 'rev-parse', 'HEAD') != binding['commit'] or git(root, 'diff', '--name-only', 'HEAD', '--'):
        raise ValueError('Frozen baseline source changed after research admission')
    return root
