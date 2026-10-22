"""Read completed execution receipts and compute bounded task diagnostics."""
from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
from typing import Any


def verified_iteration_evidence(run_root: Path, attempt: int,
                                metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for row in metrics:
        matches: list[dict[str, Any]] = []
        for record in (run_root / 'execution/jobs').glob('*.json'):
            job = json.loads(record.read_text())
            result = job.get('result', {})
            if (job.get('attempt') == attempt and job.get('experiment_id') == row.get('experiment_id')
                    and job.get('status') == 'completed' and result.get('is_mock') is False
                    and result.get('fingerprint_hash') == row.get('fingerprint_hash')
                    and result.get('metrics') == row.get('metrics')):
                matches.append(job)
        if len(matches) != 1 or not matches[0].get('evidence'):
            raise ValueError('Metrics do not have one intact completed job receipt: ' + str(row.get('experiment_id')))
        files: list[Path] = []
        for item in matches[0]['evidence']:
            target = Path(item['path'])
            if (target.is_symlink() or not target.resolve().is_relative_to(run_root.resolve())
                    or not target.is_file()):
                raise ValueError('Simulation evidence path is unavailable')
            with target.open('rb') as stream:
                checksum = 'sha256:' + hashlib.file_digest(stream, 'sha256').hexdigest()
            if checksum != item['sha256']:
                raise ValueError('Simulation evidence changed after completion: ' + target.name)
            files.append(target)
        diagnostics: dict[str, Any] = {'experiment_id': row['experiment_id'],
            'execution_fingerprint': row['fingerprint_hash'], 'files': matches[0]['evidence'],
            'observations': {}, 'weights': {}, 'warnings': []}
        # A verified artifact index also binds weights and detailed diagnostics
        # that need not be repeated in the compact job receipt.
        indices = [target for target in files if target.name == 'artifact_index.json']
        for index_path in indices:
            index = json.loads(index_path.read_text())
            for asset in index.get('files', []):
                if (str(asset.get('path', '')).endswith(('.pth', '.pt', 'best_checkpoint.json', 'data_diagnostics.json'))
                        and int(asset.get('bytes', 4000001)) <= 4000000):
                    target = index_path.parent / asset['path']
                    if (target.is_symlink() or not target.resolve().is_relative_to(index_path.parent.resolve())
                            or not target.is_file()):
                        raise ValueError('Indexed checkpoint escaped its verified artifact directory')
                    with target.open('rb') as stream:
                        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
                    if checksum != asset['sha256'].removeprefix('sha256:'):
                        raise ValueError('Indexed checkpoint or diagnostic evidence changed')
                    if target not in files:
                        files.append(target)
                    diagnostics['files'].append({'path': str(target.resolve()), 'sha256': 'sha256:' + checksum})
        for target in files:
            if target.name == 'execution_receipt.json':
                receipt = json.loads(target.read_text())
                diagnostics['scientific_inputs'] = receipt.get('scientific_inputs', [])
                diagnostics['scientific_request'] = receipt.get('scientific_request', {})
            if target.name in {'summary.json', 'observations.summary.json', 'best_checkpoint.json',
                               'data_diagnostics.json'} and target.stat().st_size <= 256000:
                document = json.loads(target.read_text())
                diagnostics['observations'][target.name] = document.get('summary', document)
        steps = next((target for target in files if target.name == 'steps.jsonl'), None)
        if steps is not None:
            first: dict[str, Any] | None = None
            last: dict[str, Any] | None = None
            count = 0
            with steps.open() as stream:
                for line in stream:
                    if line.strip():
                        value = json.loads(line)
                        if first is None:
                            first = value
                        last = value
                        count += 1
            diagnostics['training_curve'] = {'actual_updates': count, 'first_update': first, 'last_update': last,
                                             'source': str(steps.relative_to(run_root))}
        checkpoints = [target for target in files if target.suffix in {'.pth', '.pt'} and target.stat().st_size <= 4000000]
        for target in checkpoints[:2]:
            try:
                diagnostics['weights'][target.name] = coefficient_diagnostics(target)
            except (ImportError, RuntimeError, ValueError, TypeError) as exc:
                diagnostics['warnings'].append(f'{target.name}: coefficient diagnostics unavailable ({type(exc).__name__})')
        evidence.append(diagnostics)
    return evidence


def coefficient_diagnostics(path: Path) -> dict[str, Any]:
    """Actual checkpoint tensors, not an inferred architecture or model score."""
    torch = importlib.import_module('torch')
    raw = torch.load(path, map_location='cpu', weights_only=True)
    if not isinstance(raw, dict):
        raise ValueError('Checkpoint is not a tensor mapping')
    for key in ('state_dict', 'model_state_dict', 'model'):
        if isinstance(raw.get(key), dict):
            raw = raw[key]
            break
    results: dict[str, Any] = {}
    for name, tensor in raw.items():
        if not isinstance(name, str) or not torch.is_tensor(tensor) or not tensor.is_floating_point() and not tensor.is_complex():
            continue
        value = tensor.detach().to(dtype=torch.complex128 if tensor.is_complex() else torch.float64)
        if not bool(torch.isfinite(value).all()):
            results[name] = {'finite': False, 'shape': list(value.shape)}
            continue
        if not value.numel():
            results[name] = {'finite': True, 'shape': list(value.shape), 'numel': 0, 'l2_norm': 0.0}
            continue
        item: dict[str, Any] = {'finite': True, 'shape': list(value.shape), 'numel': value.numel(),
            'l2_norm': float(torch.linalg.vector_norm(value)), 'max_magnitude': float(value.abs().max())}
        if value.ndim >= 2 and value.numel() and value.shape[0] <= 256 and value.numel() // value.shape[0] <= 1024:
            matrix = value.reshape(value.shape[0], -1)
            singular = torch.linalg.svdvals(matrix)
            threshold = 1e-6 * float(singular.max())
            power = singular.square()
            item.update(matrix_definition='axis 0 versus flattened remaining axes of this saved tensor',
                matrix_shape=list(matrix.shape), singular_values=singular.tolist(), rank_threshold=threshold,
                numerical_rank=int((singular > threshold).sum()),
                leading_energy_fraction=float(power[0] / power.sum()) if float(power.sum()) else 0.0)
        results[name] = item
    return results
