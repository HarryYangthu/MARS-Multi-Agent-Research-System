"""Lossless YAML configuration copies with explicitly scoped scalar changes."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import tempfile
from typing import TYPE_CHECKING, Any

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode
from yaml.tokens import AliasToken, AnchorToken

if TYPE_CHECKING:
    from app.harness.tools.registry import ToolContext, ToolResult


class YamlCopyError(ValueError):
    """The requested edit cannot be made without guessing configuration syntax."""


class _UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key, _ in node.value:
            if key.tag == 'tag:yaml.org,2002:merge':
                raise YamlCopyError('YAML merge keys require code.write_file with an explicit complete configuration')
            value = self.construct_object(key, deep=True)
            try:
                duplicate = value in seen
                seen.add(value)
            except TypeError as exc:
                raise YamlCopyError('complex YAML mapping keys require code.write_file') from exc
            if duplicate:
                raise YamlCopyError(f'duplicate YAML key at line {key.start_mark.line + 1}: {value!r}')
        return super().construct_mapping(node, deep=deep)


def _parse(text: str) -> tuple[dict[str, Any], MappingNode]:
    if any(isinstance(token, (AliasToken, AnchorToken)) for token in yaml.scan(text)):
        raise YamlCopyError('YAML anchors/aliases require code.write_file; shared-node edits are ambiguous')
    value = yaml.load(text, Loader=_UniqueLoader)
    node = yaml.compose(text, Loader=_UniqueLoader)
    if not isinstance(value, dict) or not isinstance(node, MappingNode):
        raise YamlCopyError('source YAML must contain one mapping document')
    return value, node


def _updates(value: Any, *, prefix: str = 'updates') -> dict[str, Any]:
    if not isinstance(value, dict):
        raise YamlCopyError(f'{prefix} must be a nested mapping')
    out: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise YamlCopyError(f'{prefix} keys must be nonempty strings')
        if isinstance(item, dict):
            out[key] = _updates(item, prefix=f'{prefix}.{key}')
        elif item is None or type(item) in (str, bool, int, float):
            if type(item) is float and not math.isfinite(item):
                raise YamlCopyError(f'{prefix}.{key} must be finite')
            out[key] = item
        else:
            raise YamlCopyError(f'{prefix}.{key} must be a scalar or nested mapping; use code.write_file for sequence edits')
    return out


def _merge(common: dict[str, Any], specific: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(common)
    for key, value in specific.items():
        if key in result and isinstance(result[key], dict) != isinstance(value, dict):
            raise YamlCopyError(f'common and variant updates conflict at {key!r}')
        result[key] = _merge(result[key], value) if key in result and isinstance(value, dict) else deepcopy(value)
    return result


def _same(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    if type(left) is float and math.isnan(left) and math.isnan(right):
        return True
    return bool(left == right)


def render_yaml_copy(text: str, updates: dict[str, Any], *, allow_new_keys: bool = False) -> str:
    """Replace only changed scalar spans; retain all other source text verbatim."""
    original, document = _parse(text)
    expected = deepcopy(original)
    changes: list[tuple[int, int, str]] = []
    newline = '\r\n' if '\r\n' in text else '\n'

    def visit(values: dict[str, Any], node: MappingNode, requested: dict[str, Any], path: str) -> None:
        nodes: dict[str, Node] = {}
        for key, value in node.value:
            if isinstance(key, ScalarNode) and key.tag == 'tag:yaml.org,2002:str':
                nodes[key.value] = value
        additions: dict[str, Any] = {}
        for key, value in requested.items():
            field = f'{path}.{key}' if path else key
            if key not in values:
                if not allow_new_keys:
                    raise YamlCopyError(f'unknown configuration field {field!r}; explicitly allow_new_keys to add it')
                additions[key] = deepcopy(value)
                values[key] = deepcopy(value)
                continue
            current = nodes.get(key)
            if isinstance(value, dict):
                if not isinstance(values[key], dict) or not isinstance(current, MappingNode):
                    raise YamlCopyError(f'{field!r} is not a mapping')
                visit(values[key], current, value, field)
            else:
                if not isinstance(current, ScalarNode):
                    raise YamlCopyError(f'{field!r} is not a scalar; use code.write_file for structural edits')
                if _same(values[key], value):
                    continue
                if current.style in ('|', '>'):
                    raise YamlCopyError(f'{field!r} uses a multiline scalar; use code.write_file for this field')
                replacement = json.dumps(value, ensure_ascii=False, allow_nan=False)
                changes.append((current.start_mark.index, current.end_mark.index, replacement))
                values[key] = deepcopy(value)
        if additions:
            if node.flow_style:
                position = node.end_mark.index - 1
                if text[position] != '}':
                    raise YamlCopyError('unrecognized flow mapping boundary; use code.write_file')
                snippet = ', '.join(json.dumps(key, ensure_ascii=False) + ': ' + json.dumps(value, ensure_ascii=False, allow_nan=False)
                                    for key, value in additions.items())
                changes.append((position, position, (', ' if node.value else '') + snippet))
                return
            position = node.end_mark.index
            start = text.rfind('\n', 0, position) + 1
            if not text[start:position].strip():
                position = start
            prefix = '' if position == 0 or text[position - 1] == '\n' else newline
            snippet = yaml.safe_dump(additions, allow_unicode=True, sort_keys=False).replace('\n', newline)
            indentation = ' ' * (0 if node is document else node.start_mark.column)
            snippet = ''.join(indentation + line if line.strip() else line for line in snippet.splitlines(keepends=True))
            changes.append((position, position, prefix + snippet))

    visit(expected, document, _updates(updates), '')
    result = text
    for start, end, replacement in sorted(changes, reverse=True):
        result = result[:start] + replacement + result[end:]
    actual, _ = _parse(result)
    if not _same(actual, expected):
        raise YamlCopyError('configuration round-trip differs; use code.write_file for this YAML syntax')
    return result


@dataclass(frozen=True)
class PreparedCopy:
    path: str
    target: Path
    before: bytes | None
    content: str
    mode: int


def _safe_target(root: Path, relative: str, target: Path) -> None:
    raw = root / relative
    if target.suffix.lower() not in {'.yaml', '.yml'}:
        raise YamlCopyError('source and destination must be YAML files')
    for path in (raw, *raw.parents):
        if path == root.parent:
            break
        if path.is_symlink():
            raise YamlCopyError(f'symlink configuration paths are not admitted: {relative}')
    if target.exists() and (not target.is_file() or target.stat().st_nlink != 1):
        raise YamlCopyError(f'configuration must be a regular, unshared file: {relative}')


async def copy_yaml(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    # Import only at invocation: the public code module owns common authority and receipts.
    from app.harness.tools import code
    from app.harness.tools.registry import ToolResult
    denied = code._read_only_error(ctx)
    if denied:
        return ToolResult(ok=False, error=denied)
    staged: list[tuple[PreparedCopy, Path]] = []
    applied: list[PreparedCopy] = []
    rollback_ref: str | None = None
    try:
        root = code._project_root(ctx)
        if root is None:
            raise YamlCopyError('project code repository is not connected')
        source_name = str(args.get('source', '')).strip()
        resolved = code._resolve_project_file(ctx, source_name, must_exist=True)
        if isinstance(resolved, ToolResult):
            return resolved
        _, source_rel, source = resolved
        _safe_target(root, source_name, source)
        if source.stat().st_size > 4 * 1024 * 1024:
            raise YamlCopyError('source YAML exceeds the 4 MiB configuration limit')
        source_bytes = source.read_bytes()
        text = source_bytes.decode('utf-8')
        common = _updates(args.get('common_updates', {}), prefix='common_updates')
        variants = args.get('variants')
        if not isinstance(variants, list) or not 1 <= len(variants) <= 128:
            raise YamlCopyError('variants must contain between 1 and 128 YAML destinations')
        allow_new = args.get('allow_new_keys', False)
        if type(allow_new) is not bool:
            raise YamlCopyError('allow_new_keys must be boolean')
        prepared: list[PreparedCopy] = []
        targets: set[Path] = set()
        for item in variants:
            if not isinstance(item, dict) or item.keys() - {'path', 'updates'}:
                raise YamlCopyError('each variant requires path and optional nested updates only')
            supplied_path = str(item.get('path', '')).strip()
            resolved = code._resolve_project_file(ctx, supplied_path, must_exist=False, for_write=True)
            if isinstance(resolved, ToolResult):
                return resolved
            _, relative, target = resolved
            _safe_target(root, supplied_path, target)
            if target == source or target in targets:
                raise YamlCopyError('destinations must be distinct and must not overwrite the source')
            targets.add(target)
            content = render_yaml_copy(text, _merge(common, _updates(item.get('updates', {}))), allow_new_keys=allow_new)
            prepared.append(PreparedCopy(relative, target, target.read_bytes() if target.exists() else None,
                                         content, (target.stat().st_mode if target.exists() else source.stat().st_mode) & 0o777))
        if sum(len(item.content.encode('utf-8')) + len(item.before or b'') for item in prepared) > 64 * 1024 * 1024:
            raise YamlCopyError('YAML batch exceeds the 64 MiB snapshot limit; split into smaller calls')
        source_hash = code._sha_text(text)
        from app.harness.evolution import check_evolution_write
        for item in prepared:
            check_evolution_write(root, item.path, item.content)
        files = [{'path': item.path, 'sha256': code._sha_text(item.content),
                  'bytes': len(item.content.encode('utf-8')), 'source_sha256': source_hash} for item in prepared]
        if ctx.dry_run or args.get('dry_run') is True:
            return ToolResult(ok=True, output={'files': files, 'source': source_rel, 'dry_run': True, 'applied': False})
        snapshots = [code.FileSnapshot(item.path, item.before is not None,
                     code._sha_text(item.before.decode('utf-8')) if item.before is not None else '',
                     item.before.decode('utf-8') if item.before is not None else '') for item in prepared]
        # Prepare every postimage before changing any destination.
        for item in prepared:
            item.target.parent.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(prefix='.mars-yaml-', dir=item.target.parent)
            temporary = Path(name)
            staged.append((item, temporary))
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(item.content.encode('utf-8'))
            os.chmod(temporary, item.mode)
        if source.read_bytes() != source_bytes:
            raise YamlCopyError('source configuration changed during preparation; retry against its current contents')
        for item, _ in staged:
            _safe_target(root, item.path, item.target)
            actual = item.target.read_bytes() if item.target.exists() else None
            if actual != item.before:
                raise YamlCopyError(f'destination changed during preparation: {item.path}')
        rollback_ref = code._write_rollback(ctx, 'code.copy_yaml', snapshots,
            after_snapshots=[code.FileSnapshot(item.path, True, code._sha_text(item.content), item.content) for item in prepared])
        if rollback_ref is None:
            raise YamlCopyError('a current run directory is required for the YAML copy rollback receipt')
        for item, temporary in staged:
            os.replace(temporary, item.target)
            applied.append(item)
        code._record_branch_write(ctx, [item.path for item in prepared])
        return ToolResult(ok=True, output={'files': files, 'source': source_rel, 'source_sha256': source_hash, 'applied': True},
                          rollback_ref=rollback_ref, evidence_refs=[rollback_ref])
    except (ValueError, OSError, yaml.YAMLError) as exc:
        restoration_errors: list[str] = []
        for item in reversed(applied):
            try:
                if item.before is None:
                    item.target.unlink(missing_ok=True)
                else:
                    descriptor, name = tempfile.mkstemp(prefix='.mars-yaml-restore-', dir=item.target.parent)
                    temporary = Path(name)
                    try:
                        with os.fdopen(descriptor, 'wb') as stream:
                            stream.write(item.before)
                        os.chmod(temporary, item.mode)
                        os.replace(temporary, item.target)
                    finally:
                        temporary.unlink(missing_ok=True)
            except OSError as restore_error:
                restoration_errors.append(f'{item.path}: {restore_error}')
        return ToolResult(ok=False, error=str(exc), output={'applied': False, 'files': [],
                          'fallback_tool': 'code.write_file', 'rollback_errors': restoration_errors}, rollback_ref=rollback_ref)
    finally:
        for _, temporary in staged:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
