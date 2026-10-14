"""Bounded, read-only handoff of this run's actual job files to reporting."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from pathlib import Path
import re
from typing import Any

from app.harness.runtime.project_scope import safe_scope_path


def _read(root: Path, name: str) -> tuple[Path, bytes]:
    path = safe_scope_path(root, name, must_exist=True)
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Execution evidence exceeds the bounded handoff size")
    return path, path.read_bytes()


def measured_metrics(raw: object) -> dict[str, float]:
    """Validate bounded measured values without choosing a comparison policy."""
    if not isinstance(raw, dict) or len(raw) > 128:
        raise ValueError("Job metrics must be a bounded mapping")
    values: dict[str, float] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or type(value) not in (int, float):
            raise ValueError("Job metrics must contain named numeric measurements")
        try:
            number = float(value)
        except OverflowError as exc:
            raise ValueError("Job metrics must be finite") from exc
        if not math.isfinite(number):
            raise ValueError("Job metrics must be finite")
        values[key] = number
    return values


def _digest_matches(expected: object, raw: bytes) -> bool:
    return (isinstance(expected, str) and re.fullmatch(r"(?:sha256:)?[a-f0-9]{64}", expected) is not None
            and expected.removeprefix("sha256:") == hashlib.sha256(raw).hexdigest())


_CHECKPOINT_SUFFIXES = frozenset({".pth", ".pt", ".ckpt"})


def _execution_relative(root: Path, value: object, *, indexed_checkpoint: bool = False) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("Execution evidence path is missing")
    target = Path(value)
    name = target.relative_to(root).as_posix() if target.is_absolute() else target.as_posix()
    if not name.startswith("execution/"):
        raise ValueError("Job evidence is outside execution scope")
    from app.harness.runtime.project_scope import forbidden_source_path
    # Source inspection excludes model binaries. A completed job's hashed
    # artifact index may declare its generated checkpoint, which is hashed as
    # opaque bytes only. Credential/control directories stay excluded.
    checkpoint = (indexed_checkpoint and Path(name).suffix.casefold() in _CHECKPOINT_SUFFIXES
                  and not forbidden_source_path(Path(name).with_suffix(".opaque").as_posix()))
    if forbidden_source_path(name) and not checkpoint:
        raise ValueError("Job evidence names a forbidden source")
    safe_scope_path(root, name, must_exist=True)
    return name


def _checkpoint_item(path: Path, source: str, entry: dict[str, Any], max_bytes: int) -> dict[str, Any]:
    """Verify a declared generated checkpoint without decoding or admitting it."""
    expected = entry.get("sha256")
    if not isinstance(expected, str) or re.fullmatch(r"(?:sha256:)?[a-f0-9]{64}", expected) is None:
        raise ValueError("Indexed checkpoint requires a recorded SHA-256 digest")
    size = entry.get("bytes")
    if type(size) is not int or size < 0 or path.stat().st_size != size:
        raise ValueError("Indexed checkpoint size differs from its receipt: " + source)
    bound = min(16 * 1024 * 1024, max_bytes)
    if size > bound:
        return {"source": source, "declared_sha256": expected.removeprefix("sha256:"), "bytes": size,
                "integrity": "index_declared_only", "hash_verification": "deferred_due_to_streaming_budget",
                "bytes_hash_verified": False, "text_readable": False, "content_admitted": False}
    digest, total = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while chunk := stream.read(65536):
            total += len(chunk)
            if total > bound:
                raise ValueError("Indexed checkpoint exceeds bounded handoff capacity")
            digest.update(chunk)
    if total != size or digest.hexdigest() != expected.removeprefix("sha256:"):
        raise ValueError("Indexed checkpoint no longer matches its recorded digest: " + source)
    return {"source": source, "sha256": digest.hexdigest(), "bytes": total,
            "integrity": "recorded_sha256_verified", "read_method": "streamed_hash_only",
            "text_readable": False, "content_admitted": False}


def _step_summary(path: Path, text: str) -> dict[str, Any]:
    if path.suffix == ".csv":
        rows: list[Any] = list(csv.DictReader(io.StringIO(text)))
    elif path.suffix == ".jsonl":
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    elif path.suffix == ".json":
        raw = json.loads(text)
        rows = raw if isinstance(raw, list) else []
    else:
        return {}
    if not rows or not any(isinstance(row, dict) and "optimizer_step" in row for row in rows):
        return {}
    values: dict[str, list[float]] = {}
    for key in ("lr", "optimizer_step", "training_loss"):
        found: list[float] = []
        for row in rows:
            value = row.get(key) if isinstance(row, dict) else None
            if value is None or isinstance(value, bool):
                continue
            try:
                number = float(value)
            except (ValueError, TypeError, OverflowError):
                continue
            if math.isfinite(number):
                found.append(number)
        values[key] = found
    unique_lr = sorted(set(values["lr"]))
    return {"rows": len(rows), "lr_rows": len(values["lr"]), "lr_values": unique_lr[:64],
            "lr_values_omitted": max(0, len(unique_lr) - 64),
            "last_optimizer_step": values["optimizer_step"][-1] if values["optimizer_step"] else None,
            "training_loss_min": min(values["training_loss"]) if values["training_loss"] else None,
            "training_loss_max": max(values["training_loss"]) if values["training_loss"] else None}


def _file_item(root: Path, name: str, raw: bytes, *, integrity: str,
               admit_materials: bool, text_budget: list[int]) -> dict[str, Any]:
    item: dict[str, Any] = {"source": name, "sha256": hashlib.sha256(raw).hexdigest(),
                            "bytes": len(raw), "integrity": integrity}
    path = Path(name)
    if path.suffix.casefold() not in {".json", ".jsonl", ".csv", ".yaml", ".yml", ".md", ".txt"}:
        item["text_readable"] = False
        return item
    if len(raw) > 131072 or len(raw) > text_budget[0]:
        item.update(text_readable=False, text_limit="bounded text readback capacity exceeded")
        return item
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        item.update(text_readable=False, text_limit="file is not UTF-8 text")
        return item
    text_budget[0] -= len(raw)
    from app.harness.tools.execution.command_preflight import bounded_error
    text = bounded_error(text, max_chars=16777216)
    item.update(text_readable=True, preview=text[:1600], preview_truncated=len(text) > 1600)
    try:
        item.update(_step_summary(path, text))
    except (ValueError, TypeError, csv.Error):
        item["structured_summary_error"] = "Cannot parse this actual file as a structured training record"
    if admit_materials:
        from app.harness.context.runtime_pack import reference_message, store_material
        message = reference_message("execution evidence", name,
            f"source: {name}\nsource_sha256: {item['sha256']}\n"
            "Actual stored evidence; credential redaction may change its displayed text.\n" + text)
        item["material_ref"] = store_material(root, message)
        item["reader"] = {"tool": "context.read_material", "args": {
            "ref": item["material_ref"], "char_offset": 0}}
    return item


def execution_job_evidence(root: Path, run_id: str, project: str, *, admit_materials: bool = False) -> str:
    """Verify actual receipts/index entries and optionally admit bounded text readback.

    Never construct filenames from a presumed adapter layout. Legacy sibling
    files are explicitly observed-only when no hashed artifact index exists.
    """
    jobs = safe_scope_path(root, "execution/jobs/.handoff_anchor").parent
    lines = ["# Actual job file evidence", "Paths below belong to this run, relative to its root.",
             "Use the actual artifact index and source paths, not assumed steps.jsonl/config.yaml filenames.",
             "A file is absent only when actual receipts/indexed paths establish that; format names are not mandatory.",
             "Current job manifest, effective configuration and executed metric implementation take priority over old README interfaces.",
             "source is a run-relative citation path, not a code.repo_reader path. Use each text file's paired reader with its exact material_ref.",
             "Each completed job includes its actual stored metrics; use all paired jobs, not only abbreviated aggregate previews.",
             "Min/max loss does not establish monotonic convergence or statistical significance."]
    paths = sorted(jobs.glob("*.json")) if jobs.exists() else []
    text_budget, file_budget, byte_budget = [1048576], 256, 134217728
    for job_path in paths[:64]:
        name = job_path.relative_to(root).as_posix()
        try:
            _, raw = _read(root, name)
            job = json.loads(raw)
            if job.get("run_id") != run_id or job.get("project") != project:
                raise ValueError("Job identity differs from the current run/project")
            record: dict[str, Any] = {"source": name, "sha256": hashlib.sha256(raw).hexdigest(),
                "experiment_id": job.get("experiment_id"), "attempt": job.get("attempt"),
                "status": job.get("status"), "duration_seconds": job.get("result", {}).get("duration_seconds"),
                "updated_at": job.get("updated_at"),
                "error": job.get("result", {}).get("error") or job.get("error"),
                "files": []}
            if job.get("status") not in {"completed", "failed", "cancelled"}:
                lines.append(json.dumps(record, ensure_ascii=False, allow_nan=False))
                continue
            verified: dict[str, bytes] = {}
            evidence_rows = job.get("evidence", [])
            if not isinstance(evidence_rows, list) or len(evidence_rows) > 64:
                raise ValueError("Job evidence exceeds bounded receipt capacity")
            for evidence in evidence_rows:
                relative = _execution_relative(root, evidence.get("path"))
                target = safe_scope_path(root, relative, must_exist=True)
                if file_budget <= 0 or target.stat().st_size > byte_budget:
                    raise ValueError("Job evidence exceeds bounded handoff capacity")
                _, content = _read(root, relative)
                if not _digest_matches(evidence.get("sha256"), content):
                    raise ValueError("Job evidence no longer matches its recorded digest")
                if relative not in verified:
                    file_budget -= 1
                    byte_budget -= len(content)
                verified[relative] = content
            indexed = False
            opaque: dict[str, dict[str, Any]] = {}
            legacy_directories: set[Path] = set()
            historical_receipts: set[str] = set()
            fingerprint = job.get('result', {}).get('fingerprint_hash')
            for relative, content in verified.items():
                if Path(relative).name != 'execution_receipt.json' or not _digest_matches(fingerprint, content):
                    continue
                primary = json.loads(content)
                recovery = primary.get('recovery')
                if not isinstance(recovery, dict):
                    continue
                if (primary.get('status') != 'completed' or recovery.get('schema') != 'local_command_result_recovery.v1'
                        or recovery.get('training_rerun') is not False):
                    raise ValueError('Primary execution recovery binding is invalid')
                source = _execution_relative(root, recovery.get('source_receipt_path'))
                original_bytes = verified.get(source)
                if original_bytes is None or not _digest_matches(recovery.get('source_receipt_sha256'), original_bytes):
                    raise ValueError('Original execution receipt differs from its recovered primary receipt')
                original = json.loads(original_bytes)
                if (original.get('schema') != 'local_command_receipt.v1' or original.get('status') != 'failed'
                        or original.get('returncode') != 0 or original.get('run_id') != run_id
                        or original.get('experiment_id') != job.get('experiment_id')):
                    raise ValueError('Original execution receipt is not this job\'s zero-exit historical failure')
                for kind in ('request', 'result'):
                    source_target = _execution_relative(root, recovery.get('source_' + kind + '_path'))
                    if (Path(source_target).parent != Path(source).parent or source_target not in verified
                            or not _digest_matches(primary.get(kind + '_sha256'), verified[source_target])):
                        raise ValueError('Original execution request/result differs from its recovered primary receipt')
                historical_receipts.add(source)
                record['historical_execution_receipts'] = [{'source': source, 'status': 'failed', 'returncode': 0,
                    'role': 'original host contract failure; retained audit, not a new measurement'}]
                record['measurement_recovered_without_retraining'] = True
                record['actual_output_dir'] = Path(source).parent.as_posix()
            for relative, content in list(verified.items()):
                path = Path(relative)
                if path.name == "execution_receipt.json":
                    receipt = json.loads(content)
                    if (receipt.get("run_id") != run_id or receipt.get("experiment_id") != job.get("experiment_id")
                            or (job.get('status') == 'completed' and receipt.get("status") != "completed"
                                and relative not in historical_receipts)):
                        raise ValueError("Execution receipt identity/status differs from its completed job")
                    if not historical_receipts:
                        record["actual_output_dir"] = path.parent.as_posix()
                if path.name == "summary.json":
                    legacy_directories.add(path.parent)
                    record.setdefault("actual_output_dir", path.parent.as_posix())
                if path.suffix != ".json":
                    continue
                document = json.loads(content)
                entries = document.get("files") if isinstance(document, dict) else None
                if not isinstance(entries, list):
                    continue
                indexed = True
                if len(entries) > 256:
                    raise ValueError("Artifact index exceeds bounded file capacity")
                for entry in entries:
                    if not isinstance(entry, dict):
                        raise ValueError("Artifact index entry must be a mapping")
                    child = entry.get("path")
                    if not isinstance(child, str) or Path(child).is_absolute():
                        raise ValueError("Artifact index requires output-relative file paths")
                    target = safe_scope_path(root / path.parent, child, must_exist=True)
                    source = _execution_relative(root, target.relative_to(root).as_posix(), indexed_checkpoint=True)
                    if target.suffix.casefold() in _CHECKPOINT_SUFFIXES:
                        if file_budget <= 0:
                            raise ValueError("Artifact index exceeds bounded handoff capacity")
                        item = _checkpoint_item(target, source, entry, byte_budget)
                        if source in opaque:
                            if item != opaque[source]:
                                raise ValueError("Indexed checkpoint declarations disagree")
                            continue
                        opaque[source] = item
                        file_budget -= 1
                        if item["integrity"] == "recorded_sha256_verified":
                            byte_budget -= int(item["bytes"])
                        continue
                    if source in verified:
                        if not _digest_matches(entry.get("sha256"), verified[source]):
                            raise ValueError("Artifact index digest differs from the job evidence digest")
                        if type(entry.get("bytes")) is not int or entry["bytes"] != len(verified[source]):
                            raise ValueError("Indexed artifact size differs from its receipt: " + source)
                        continue
                    if file_budget <= 0 or target.stat().st_size > byte_budget:
                        raise ValueError("Artifact index exceeds bounded handoff capacity")
                    _, artifact = _read(root, source)
                    if not _digest_matches(entry.get("sha256"), artifact):
                        raise ValueError("Indexed artifact no longer matches its recorded digest: " + source)
                    if type(entry.get("bytes")) is not int or entry["bytes"] != len(artifact):
                        raise ValueError("Indexed artifact size differs from its receipt: " + source)
                    verified[source] = artifact
                    file_budget -= 1
                    byte_budget -= len(artifact)
            for source, content in verified.items():
                record["files"].append(_file_item(root, source, content, integrity="recorded_sha256_verified",
                    admit_materials=admit_materials, text_budget=text_budget))
            record["files"].extend(opaque.values())
            if not indexed:
                for directory in sorted(legacy_directories):
                    for sibling in sorted((root / directory).iterdir()):
                        source = sibling.relative_to(root).as_posix()
                        if source in verified or not sibling.is_file() or sibling.suffix not in {".json", ".jsonl", ".csv", ".yaml", ".yml"}:
                            continue
                        if file_budget <= 0 or sibling.stat().st_size > byte_budget:
                            record["index_limit"] = "Additional observed legacy files omitted by bounded capacity"
                            break
                        source = _execution_relative(root, source)
                        _, content = _read(root, source)
                        file_budget -= 1
                        byte_budget -= len(content)
                        record["files"].append(_file_item(root, source, content, integrity="observed_only_no_recorded_digest",
                            admit_materials=admit_materials, text_budget=text_budget))
            if job.get('status') == 'completed':
                record["metrics"] = measured_metrics(job.get("result", {}).get("metrics"))
            lines.append(json.dumps(record, ensure_ascii=False, allow_nan=False))
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            from app.harness.tools.execution.command_preflight import bounded_error
            lines.append(json.dumps({"source": name, "evidence_error": bounded_error(str(exc))}, ensure_ascii=False))
    if len(paths) > 64:
        lines.append(f"Additional job records omitted: {len(paths) - 64}")
    return "\n".join(lines)
