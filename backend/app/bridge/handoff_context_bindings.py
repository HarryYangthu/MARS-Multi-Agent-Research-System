"""Explicit, hash-bound input bindings for already-approved legacy proposals.

This records a human-authorized structural repair without rewriting the approved
scientific document, inventing inputs, or selecting a key by reading prose.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import Field

from app.harness.agent_loop.trace import atomic_json, digest
from app.harness.persistence import path_lock
from app.harness.runtime.task_contract import Contract, HandoffPrerequisite
from app.harness.schema.frontmatter_parser import parse
from app.harness.schema.validator import validate_document
from app.storage.run_store import RunHandle


class LegacyInputBinding(Contract):
    source_ref: str
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    prerequisite_index: int = Field(ge=0)
    prerequisite_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    context_ref: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
    context_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    resource_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(min_length=1)
    authorized_by: str = Field(min_length=1)


class LegacyInputBindings(Contract):
    run_id: str
    project: str
    bindings: list[LegacyInputBinding]


def _path(run: RunHandle) -> Path:
    path = run.root / 'input/handoff_context_bindings.json'
    if not path.resolve().is_relative_to(run.root.resolve()):
        raise ValueError('交接绑定记录越出当前任务')
    return path


def _source(run: RunHandle, ref: str) -> Path:
    path = (run.root / ref).resolve()
    if (not path.is_relative_to((run.root / 'idea').resolve())
            or not path.name.endswith('.approved.md') or not path.is_file()):
        raise ValueError('交接绑定必须指向当前任务已批准的研究方案')
    return path


def _file_sha(path: Path) -> str:
    hashed = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            hashed.update(block)
    return hashed.hexdigest()


def _load(run: RunHandle) -> LegacyInputBindings:
    path = _path(run)
    if not path.is_file():
        return LegacyInputBindings(run_id=run.run_id, project=run.project, bindings=[])
    receipt = LegacyInputBindings.model_validate_json(path.read_text())
    if receipt.run_id != run.run_id or receipt.project != run.project:
        raise ValueError('交接绑定不属于当前任务和项目')
    identities = [(item.source_ref, item.prerequisite_index) for item in receipt.bindings]
    if len(set(identities)) != len(identities):
        raise ValueError('同一交接条件存在重复或冲突绑定')
    return receipt


def _check(run: RunHandle, binding: LegacyInputBinding, supplied: dict[str, str]) -> HandoffPrerequisite:
    source = _source(run, binding.source_ref)
    content = source.read_bytes()
    validated = validate_document(content.decode(), expected_schema='proposal.v1')
    if (not validated.valid or validated.metadata.get('project') != run.project
            or hashlib.sha256(content).hexdigest() != binding.source_sha256):
        raise ValueError('已批准方案已改变，旧交接绑定失效；请重新核对')
    rows = validated.metadata.get('handoff', {}).get('required_context', [])
    if binding.prerequisite_index >= len(rows):
        raise ValueError('交接条件已改变，旧绑定失效')
    item = HandoffPrerequisite.model_validate(rows[binding.prerequisite_index])
    if (digest(rows[binding.prerequisite_index]) != binding.prerequisite_sha256
            or item.kind != 'other' or item.context_ref is not None or not item.blocks_execution):
        raise ValueError('绑定仅用于未指定引用的历史特殊资料，不能覆盖明确引用或放宽条件')
    value = supplied.get(binding.context_ref, '')
    if not value.strip() or digest(value) != binding.context_sha256:
        raise ValueError(f'已绑定输入 {binding.context_ref} 缺失或已改变，请重新核对')
    if binding.resource_sha256:
        path = Path(value)
        if not path.is_absolute() or not path.is_file() or _file_sha(path) != binding.resource_sha256:
            raise ValueError(f'已绑定文件 {binding.context_ref} 缺失或内容已改变，请重新核对')
    return item.model_copy(update={'context_ref': binding.context_ref})


def resolve_legacy_bindings(run: RunHandle, source: Path, prerequisites: list[HandoffPrerequisite],
                            supplied: dict[str, str]) -> tuple[list[HandoffPrerequisite], list[str]]:
    receipt = _load(run)
    result = list(prerequisites)
    refs: list[str] = []
    for binding in receipt.bindings:
        # Validate all entries, so a stale record never silently loses its guard.
        resolved = _check(run, binding, supplied)
        if _source(run, binding.source_ref) == source.resolve():
            result[binding.prerequisite_index] = resolved
            refs.append('input/handoff_context_bindings.json#' + digest(binding.model_dump()))
    return result, refs


def bind_legacy_input(run: RunHandle, *, source_ref: str, prerequisite_index: int,
                      context_ref: str, supplied_context: dict[str, str], reason: str,
                      authorized_by: str, resource_sha256: str | None = None) -> LegacyInputBinding:
    """A caller must explicitly select the named input; no automatic guesses."""
    source = _source(run, source_ref)
    rows = parse(source.read_text()).metadata.get('handoff', {}).get('required_context', [])
    if not 0 <= prerequisite_index < len(rows):
        raise ValueError('交接条件索引不存在')
    binding = LegacyInputBinding(source_ref=source_ref, source_sha256=_file_sha(source),
        prerequisite_index=prerequisite_index, prerequisite_sha256=digest(rows[prerequisite_index]),
        context_ref=context_ref, context_sha256=digest(supplied_context.get(context_ref, '')),
        reason=reason, authorized_by=authorized_by, resource_sha256=resource_sha256)
    _check(run, binding, supplied_context)
    path = _path(run)
    with path_lock(path.with_suffix('.lock')):
        receipt = _load(run)
        matches = [item for item in receipt.bindings
                   if (item.source_ref, item.prerequisite_index) == (source_ref, prerequisite_index)]
        if matches:
            if matches != [binding]:
                raise ValueError('现有绑定与本次选择不同，不能静默覆盖')
            return matches[0]
        receipt.bindings.append(binding)
        atomic_json(path, receipt.model_dump())
    return binding
