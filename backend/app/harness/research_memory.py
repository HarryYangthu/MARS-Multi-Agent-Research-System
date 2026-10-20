"""Durable agent-independent research records; evidence is never executed.

Semantic retrieval is only inspiration. Reuse requires complete equal protocols
and intact measured evidence. Baseline pointers are explicit and never auto-promoted.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import yaml

from app.harness.project_workspace import project_root
from app.settings import repo_root

SCHEMA = "research.memory.v1"
PROTOCOL_KEYS = ("data_sha256", "split", "preprocessing", "evaluation")
MAX_ASSET = 128 * 1024 * 1024
MAX_TEXT = 4 * 1024 * 1024
EXCLUDED = {".git", ".venv", "node_modules", "data", "__pycache__", "research_memory"}


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _object(path: Path) -> dict[str, Any]:
    if path.stat().st_size > MAX_TEXT:
        raise ValueError(f"记录超过4MB: {path.name}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"记录必须是对象: {path.name}")
    return raw


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()
                if not re.search(r"password|api.?key|authorization|secret|token|cookie", str(k), re.I)}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value if value is None or isinstance(value, (str, int, float, bool)) else str(value)


def _files(root: Path, limit: int = 3000) -> list[Path]:
    files: list[Path] = []
    for directory, dirs, names in os.walk(root, followlinks=False):
        here = Path(directory)
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED and not d.startswith(".")
                         and not (here / d).is_symlink())
        if len(here.relative_to(root).parts) > 12:
            dirs[:] = []
            continue
        for name in sorted(names):
            path = here / name
            if not path.is_symlink() and path.is_file():
                files.append(path)
                if len(files) > limit:
                    raise ValueError("资料过多，请选择单次研究或训练的结果文件夹（最多3000个文件）")
    return files


def compatibility(reference: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Missing/unequal conditions never become an approximate reuse decision."""
    missing, conflicts = [], []
    for key in PROTOCOL_KEYS:
        a, b = reference.get(key), candidate.get(key)
        if not a or not b:
            missing.append(key)
        elif canonical(a) != canonical(b):
            conflicts.append(key)
    return {"compatible": not missing and not conflicts, "missing": missing, "conflicts": conflicts}


class ResearchMemory:
    def __init__(self, project: str, *, root: Path | None = None) -> None:
        self.project = project
        metadata = root or project_root(project)
        if not metadata.is_dir() or (root is None and not (metadata / "project.yaml").is_file()):
            raise ValueError("项目不存在")
        self.root = metadata / "research_memory"
        if self.root.is_symlink():
            raise ValueError("记忆目录不能是符号链接")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.root / "memory.sqlite3", timeout=30)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, created TEXT NOT NULL, body TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS annotations (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS pointers (role TEXT PRIMARY KEY, id TEXT NOT NULL)")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _asset(self, source: Path, boundary: Path, category: str) -> dict[str, Any]:
        if source.is_symlink() or not source.resolve().is_relative_to(boundary.resolve()):
            raise ValueError("证据越过导入文件夹边界")
        size = source.stat().st_size
        if size > MAX_ASSET:
            raise ValueError(f"证据过大，未复制: {source.name}")
        checksum = sha_file(source)
        assets = self.root / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        suffix = source.suffix.lower()[:12]
        name = checksum + suffix
        target = assets / name
        if not target.exists():
            temp = assets / (uuid.uuid4().hex + ".tmp")
            try:
                with source.open("rb") as src, temp.open("xb") as dst:
                    for block in iter(lambda: src.read(1024 * 1024), b""):
                        dst.write(block)
                if sha_file(temp) != checksum:
                    raise ValueError("导入期间证据发生变化，请重试")
                temp.replace(target)
            finally:
                temp.unlink(missing_ok=True)
        elif sha_file(target) != checksum:
            raise ValueError("已保存证据损坏，请核对记忆备份")
        return {"name": source.name, "source": str(source), "sha256": checksum,
                "asset": name, "bytes": size, "category": category}

    def save(self, record: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        body = _clean(record)
        body.update(schema=SCHEMA, project=self.project)
        # Identity comes from original evidence, not extractor wording/version.
        identity = {"kind": body.get("kind"), "evidence": [{k: a.get(k) for k in ("sha256", "source", "category")}
                     for a in body.get("evidence", [])]}
        if body.get("reported_findings"):
            identity["reported_findings"] = body["reported_findings"]
        if body.get("provenance", {}).get("protocol_origin") == "user_declared":
            identity["declared_protocol"] = body.get("protocol", {})
        # Asset provenance (source path) is kept; timestamp doesn't duplicate repeated imports.
        key = "mem_" + hashlib.sha256(canonical(identity).encode()).hexdigest()[:32]
        body.update(id=key, created_at=datetime.now(timezone.utc).isoformat())
        with self._connect() as db:
            cursor = db.execute("INSERT OR IGNORE INTO records VALUES (?,?,?)", (key, body["created_at"], canonical(body)))
            inserted = cursor.rowcount == 1
            body = json.loads(db.execute("SELECT body FROM records WHERE id=?", (key,)).fetchone()[0])
        return body, inserted

    def records(self) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        with self._connect() as db:
            rows = [json.loads(row[0]) for row in db.execute("SELECT body FROM records ORDER BY created DESC,id")]
            notes = {row[0]: json.loads(row[1]) for row in db.execute("SELECT id,body FROM annotations")}
        for row in rows:
            row["annotations"] = notes.get(row["id"], {})
        return rows

    def get(self, record_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"mem_[0-9a-f]{32}", record_id):
            raise ValueError("无效记录编号")
        with self._connect() as db:
            row = db.execute("SELECT body FROM records WHERE id=?", (record_id,)).fetchone()
            note = db.execute("SELECT body FROM annotations WHERE id=?", (record_id,)).fetchone()
        if not row:
            raise ValueError("记忆记录不存在")
        body: dict[str, Any] = json.loads(row[0])
        body["annotations"] = json.loads(note[0]) if note else {}
        problems = []
        for asset in body.get("evidence", []):
            path = self.asset_path(asset)
            if not path.is_file() or sha_file(path) != asset["sha256"]:
                problems.append(asset["name"])
        body["integrity"] = {"intact": not problems, "problems": problems}
        return body

    def asset_path(self, asset: dict[str, Any]) -> Path:
        name = str(asset.get("asset", ""))
        if not re.fullmatch(r"[0-9a-f]{64}\.[a-z0-9]{1,10}", name):
            raise ValueError("无效证据路径")
        target = self.root / "assets" / name
        if target.is_symlink() or not target.resolve().is_relative_to((self.root / "assets").resolve()):
            raise ValueError("证据路径越界")
        return target

    def overview(self) -> dict[str, Any]:
        pointers = {}
        if self.root.exists():
            with self._connect() as db:
                pointers = dict(db.execute("SELECT role,id FROM pointers"))
        return {"schema": SCHEMA, "project": self.project, "pointers": pointers,
                "records": self.records(), "storage": str(self.root),
                "policy": "原始证据长期保留。候选不自动覆盖基线。缺失条件或证据损坏时禁止直接复用。"}

    def select(self, record_id: str, role: str) -> dict[str, Any]:
        if role not in {"original", "current_best"}:
            raise ValueError("基线角色只能为original或current_best")
        record = self.get(record_id)
        if (record["status"] not in {"completed", "code_reference"} or record.get("is_mock")
                or record["result_quality"] not in {"measured", "code_only"} or not record["integrity"]["intact"]):
            raise ValueError("失败、模拟或证据损坏的记录不能设为基线")
        with self._connect() as db:
            previous = db.execute("SELECT id FROM pointers WHERE role=?", (role,)).fetchone()
            if role == "original" and previous and previous[0] != record_id:
                raise ValueError("原始基线已冻结；新候选请设为当前最好版本")
            db.execute("INSERT OR REPLACE INTO pointers VALUES (?,?)", (role, record_id))
        return self.overview()

    def annotate(self, record_id: str, notes: dict[str, Any]) -> dict[str, Any]:
        record = self.get(record_id)
        allowed = {"hypothesis", "changes", "conclusion", "limitations", "parent_id", "tags"}
        if set(notes) - allowed:
            raise ValueError("只能补充研究结论和条件，不能修改原始测量")
        if notes.get("parent_id"):
            self.get(str(notes["parent_id"]))
            if notes["parent_id"] == record_id:
                raise ValueError("父版本不能是自身")
        merged = {**record.get("annotations", {}), **_clean(notes)}
        if len(canonical(merged)) > 16000:
            raise ValueError("研究备注过长")
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO annotations VALUES (?,?)", (record_id, canonical(merged)))
        return self.get(record_id)

    def import_directory(self, source: str, *, label: str = "", protocol: dict[str, Any] | None = None) -> dict[str, Any]:
        boundary = Path(source).expanduser()
        if not boundary.is_absolute() or not boundary.is_dir() or boundary.is_symlink():
            raise ValueError("请选择真实结果文件夹的完整路径")
        boundary = boundary.resolve()
        if boundary == Path.home() or boundary == Path(boundary.anchor):
            raise ValueError("请选择具体结果文件夹")
        files = _files(boundary)
        summaries = [p for p in files if p.name == "summary.json"]
        receipts: dict[Path, tuple[Path, dict[str, Any]]] = {}
        warnings: list[str] = []
        for path in files:
            if path.name != "execution_receipt.json":
                continue
            try:
                receipt = _object(path)
                summary = Path(str(receipt.get("summary_path", "")))
                if summary.is_absolute() and summary.resolve().is_relative_to(boundary):
                    receipts[summary.resolve()] = (path, receipt)
            except (OSError, ValueError, yaml.YAMLError) as exc:
                warnings.append(f"{path.name}: {exc}")
        added, ids = 0, []
        for summary in summaries:
            try:
                row, inserted = self._import_summary(summary, boundary, label, receipts.get(summary.resolve()), protocol)
                added += inserted
                ids.append(row["id"])
            except (OSError, ValueError, yaml.YAMLError) as exc:
                warnings.append(f"{summary.relative_to(boundary)}: {exc}")
        # Partial, structured human histories are useful without inventing metrics.
        for path in files:
            if path.name != "research_history.json":
                continue
            try:
                raw = _object(path)
                entries = raw.get("experiments", [])
                if not isinstance(entries, list) or len(entries) > 200:
                    raise ValueError("experiments必须为最多200条的列表")
                asset = self._asset(path, boundary, "human_history")
                for entry in entries:
                    if not isinstance(entry, dict) or not entry.get("title"):
                        raise ValueError("每条历史需要title")
                    row, inserted = self.save({"kind": "human_history", "title": str(entry["title"]), "status": "historical_note",
                        "metrics": _clean(entry.get("metrics", {})), "protocol": _clean(entry.get("protocol", {})),
                        "architecture": _clean(entry.get("architecture", {})), "recipe": _clean(entry.get("recipe", {})),
                        "config": _clean(entry.get("config", {})), "evidence": [asset], "missing": ["人工历史，尚未核验原始测量证据"],
                        "result_quality": "human_reported", "is_mock": False, "run_id": "", "experiment_id": "",
                        "source_root": str(boundary), "reported_findings": _clean(entry)})
                    if inserted:
                        self.annotate(row["id"], {k:entry[k] for k in ("hypothesis", "changes", "conclusion", "limitations", "tags") if k in entry})
                    added += inserted
                    ids.append(row["id"])
            except (OSError, ValueError, yaml.YAMLError) as exc:
                warnings.append(f"{path.name}: {exc}")
        # Preserve operational failures as failures, never as negative scientific results.
        for path in files:
            if path.parent.name != "jobs" or path.suffix != ".json":
                continue
            try:
                raw = _object(path)
                if raw.get("status") not in {"completed", "failed", "interrupted"}:
                    continue
                if raw.get("status") == "completed" and any(
                        raw.get("experiment_id") == pair[1].get("experiment_id") for pair in receipts.values()):
                    continue
                # Persist a stable failure receipt; ignore the later archive response.
                from app.harness.persistence import atomic_write_json
                stable = {k:v for k,v in raw.items() if k not in {"research_memory", "updated_at"}}
                staging = self.root / "staging"
                staging.mkdir(parents=True, exist_ok=True)
                staged = staging / (hashlib.sha256(canonical(stable).encode()).hexdigest()+".json")
                atomic_write_json(staged, stable)
                asset = self._asset(staged, staging, "execution")
                asset["source"] = str(path)
                assets = [asset]
                conflict = False
                for original in raw.get("evidence", []):
                    target = Path(str(original.get("path", "")))
                    if not target.is_file() or not target.resolve().is_relative_to(boundary):
                        conflict = True
                        continue
                    if sha_file(target) != str(original.get("sha256", "")).removeprefix("sha256:"):
                        conflict = True
                    assets.append(self._asset(target, boundary, "execution"))
                result = raw.get("result", {})
                completed = raw["status"] == "completed"
                inputs = raw.get("memory_inputs", {})
                config = _clean(inputs.get("config", {}))
                protocol = config.get("protocol", {})
                is_mock = bool(result.get("is_mock", False) or result.get("metrics", {}).get("dry_run", False))
                quality = ("execution_failure" if not completed else "mock_excluded" if is_mock
                           else "evidence_conflict" if conflict else "measured" if raw.get("evidence") and result.get("metrics") else "no_measurements")
                row, inserted = self.save({"kind": "experiment", "title": label or str(raw.get("experiment_id", boundary.name)),
                    "run_id": str(raw.get("run_id", "")), "experiment_id": str(raw.get("experiment_id", "")),
                    "status": raw["status"], "metrics": _clean(result.get("metrics", {})) if completed else {},
                    "protocol": protocol, "config": config, "architecture": config.get("model", {}),
                    "recipe": {"execution": config, "seed": inputs.get("seed"), "duration_seconds":result.get("duration_seconds")},
                    "missing": [f"比较条件:{k}" for k in PROTOCOL_KEYS if not protocol.get(k)] + ([] if completed else ["有效测量结果"]),
                    "evidence": assets, "is_mock": is_mock, "error": str(raw.get("error", "")),
                    "result_quality": quality, "source_root": str(boundary)})
                added += inserted
                ids.append(row["id"])
            except (OSError, ValueError, yaml.YAMLError) as exc:
                warnings.append(f"{path.name}: {exc}")
        if not ids:
            warnings.append("未发现summary.json或失败作业。人工记录请导入包含summary.json、config.yaml的结果文件夹。")
        return {"added": added, "existing": len(ids) - added, "ids": ids, "warnings": warnings}

    def _import_summary(self, summary: Path, boundary: Path, label: str,
                        receipt_pair: tuple[Path, dict[str, Any]] | None,
                        declared_protocol: dict[str, Any] | None) -> tuple[dict[str, Any], bool]:
        measured = _object(summary)
        receipt_path, receipt = receipt_pair or (None, {})
        cfg_path = summary.parent / "config.yaml"
        cfg = _object(cfg_path) if cfg_path.is_file() else {}
        manifest = {}
        manifest_path = None
        # Standalone training provenance; run-local receipts remain distinct per attempt.
        for directory in (summary.parent, *summary.parents):
            if not directory.is_relative_to(boundary):
                break
            candidate = directory / "manifest.json"
            if candidate.is_file():
                manifest_path, manifest = candidate, _object(candidate)
                break
        metrics = measured.get("metrics") if isinstance(measured.get("metrics"), dict) else {
            k: v for k, v in measured.items() if k not in {"run_id", "experiment_id", "model", "status", "seed", "band", "protocol", "config", "is_mock"}}
        metrics = _clean(metrics)
        status = str(measured.get("status", receipt.get("status", manifest.get("status", "completed"))))
        is_mock = measured.get("is_mock", False) is not False or manifest.get("is_mock", False) is not False
        dry = measured.get("dry_run", metrics.get("dry_run", False))
        missing: list[str] = []
        evidence = [self._asset(summary, boundary, "metrics")]
        if cfg:
            evidence.append(self._asset(cfg_path, boundary, "config"))
        else:
            missing.append("训练配置快照")
        corrupt = False
        if receipt_path:
            evidence.append(self._asset(receipt_path, boundary, "receipt"))
            expected = str(receipt.get("summary_sha256", "")).removeprefix("sha256:")
            if expected and expected != evidence[0]["sha256"]:
                corrupt = True
                missing.append("收据与测量文件指纹不一致")
        if manifest_path:
            evidence.append(self._asset(manifest_path, boundary, "provenance"))
        # Persist inspectable curves, spectra, coefficients and final/best checkpoints.
        for asset in _files(summary.parent, 1200):
            if asset in {summary, cfg_path} or asset.suffix.lower() not in {".png", ".jpg", ".json", ".pt", ".pth", ".csv"}:
                continue
            if asset.name.startswith("events"):
                continue
            if "jobs" in asset.relative_to(summary.parent).parts:
                continue
            category = ("checkpoint" if asset.suffix in {".pt", ".pth"} else "spectrum" if "cancellation" in asset.name or "spectrum" in asset.name
                        else "coefficients" if "coeff" in str(asset.relative_to(summary.parent)) else "training")
            try:
                evidence.append(self._asset(asset, boundary, category))
            except ValueError as exc:
                missing.append(str(exc))
        # A recipe is recorded literally; e.g. config 'adam' is not renamed AdamW without source evidence.
        recipe: dict[str, Any] = {k: cfg[k] for k in ("optimizer_param", "loss", "lr_init", "if_scheduler", "scheduler", "step_size", "reg_coeff",
                  "clip_grad_norm", "init_checkpoint", "freeze_prefixes", "Etotal", "Epoch", "epoch", "train_param", "seed", "threads") if k in cfg}
        recipe["execution"] = _clean(receipt.get("training_protocol", {}))
        recipe["actual_epochs"] = measured.get("epochs", metrics.get("epochs"))
        recipe["duration_seconds"] = manifest.get("duration_seconds", measured.get("duration_seconds"))
        initial = receipt.get("config", {})
        recipe["initial_checkpoint"] = cfg.get("init_checkpoint") or initial.get("init_checkpoint")
        recipe["initial_checkpoint_sha256"] = initial.get("checkpoint_sha256")
        selected = summary.parent / "best_checkpoint.json"
        recipe["checkpoint_selection"] = _object(selected) if selected.is_file() else None
        recipe["measurement_scope"] = "summary.json原文；不把最后一轮指标自动归为最佳权重指标"
        protocol_raw = declared_protocol or measured.get("protocol", {}) or {}
        protocol = dict(protocol_raw) if isinstance(protocol_raw, dict) else {}
        if protocol_raw and not isinstance(protocol_raw, dict):
            missing.append("历史协议是文字描述，需补充结构化比较条件")
        if not protocol.get("data_sha256"):
            value = manifest.get("data_sha256") or receipt.get("data_sha256")
            if value:
                protocol["data_sha256"] = str(value).removeprefix("sha256:")
        train = cfg.get("train_param", {}) or {}
        if isinstance(train, dict) and train.get("train_ratio") is not None and not protocol.get("split"):
            protocol["split"] = {"kind": "legacy_ordered_two_way", "train_ratio": train["train_ratio"],
                                 "selection": "no_independent_validation_test_claim"}
        preprocessing: dict[str, Any] = {k: cfg[k] for k in ("fs", "frq_shift", "bound0", "bound1", "fir_taps", "fir_type", "fir_cutoff", "fir_bw") if k in cfg}
        for k in ("xmax", "ymax", "batch_size", "flt_err_ord"):
            if k in train:
                preprocessing[k] = train[k]
        if preprocessing and not protocol.get("preprocessing"):
            protocol["preprocessing"] = preprocessing
        sources = manifest.get("source_sha256", {})
        eval_sources = {k: v for k, v in sources.items() if k in {"libs/metrics.py", "libs/dsp.py", "libs/data.py"}}
        if len(eval_sources) == 3 and not protocol.get("evaluation"):
            protocol["evaluation"] = {"source_sha256": eval_sources, "metric": "paper_filtered_RES_APE_batch"}
        missing += [f"比较条件:{k}" for k in PROTOCOL_KEYS if not protocol.get(k)]
        if not any(e["category"] == "checkpoint" for e in evidence):
            missing.append("训练后权重")
        if not sources:
            missing.append("执行时源码指纹")
        quality = ("mock_excluded" if is_mock or dry else "evidence_conflict" if corrupt else "execution_failure" if status != "completed"
                   else "measured" if metrics else "no_measurements")
        return self.save({"kind": "experiment", "title": label or str(receipt.get("experiment_id") or measured.get("run_id") or summary.parent.name),
            "run_id": str(receipt.get("run_id") or manifest.get("run_id") or measured.get("run_id", "")),
            "experiment_id": str(receipt.get("experiment_id", "")), "status": status, "metrics": metrics,
            "architecture": {"name": cfg.get("model_name", measured.get("model", "")), "channels": cfg.get("data_param", {}).get("channels", measured.get("channels")),
                             "before": cfg.get("model", {}), "after": "训练仅改变权重；此记录未提供剪枝或结构变更证据", "parameter_counts": measured.get("parameter_counts", metrics.get("parameter_counts"))},
            "config": _clean(cfg), "recipe": recipe, "protocol": _clean(protocol), "evidence": evidence,
            "missing": missing, "result_quality": quality, "is_mock": bool(is_mock or dry),
            "source_root": str(boundary), "provenance": {"code_commit": manifest.get("code_commit", ""), "source_sha256": sources,
                "protocol_description": protocol_raw if isinstance(protocol_raw, str) else "",
                "protocol_origin": "user_declared" if declared_protocol else "artifact", "training_scope": "记录结果，不声明理论最优或独立测试结论"}})

    def capture_code(self) -> tuple[dict[str, Any], bool]:
        metadata = project_root(self.project)
        link = _object(metadata / "repo_link.yaml")
        raw = link.get("baseline_repo_path") or link.get("repo_path")
        if not raw:
            raise ValueError("请先绑定基线代码仓")
        path = Path(str(raw)).expanduser()
        boundary = path.resolve() if path.is_absolute() else (metadata / path).resolve()
        if not boundary.is_dir():
            raise ValueError("基线代码仓不可访问")
        cfg_path = boundary / "configs/static.yaml"
        cfg = _object(cfg_path) if cfg_path.is_file() else {}
        evidence = []
        for name in ("configs/static.yaml", "configs/base.yaml", "libs/model.py", "libs/engine.py", "libs/metrics.py", "libs/data.py", "libs/dsp.py", "train_static.py"):
            source = boundary / name
            if source.is_file():
                evidence.append(self._asset(source, boundary, "code"))
        if not evidence:
            raise ValueError("未找到StaticPIMC配置或源码；请导入项目实际结果文件夹")
        return self.save({"kind": "code_reference", "title": "基线代码快照", "status": "code_reference", "metrics": {}, "protocol": {},
            "config": _clean(cfg), "architecture": {"name": cfg.get("model_name", ""), "before": cfg.get("model", {}), "after": None},
            "recipe": {k: cfg[k] for k in ("optimizer_param", "loss", "lr_init", "scheduler", "if_scheduler", "step_size", "train_param", "seed") if k in cfg},
            "evidence": evidence, "missing": ["本记录只有代码，训练能力请从真实结果导入"], "result_quality": "code_only", "is_mock": False,
            "source_root": str(boundary), "run_id": "", "experiment_id": ""})

    def retrieve(self, query: str, *, limit: int = 4, protocol: dict[str, Any] | None = None) -> dict[str, Any]:
        if not query.strip():
            raise ValueError("请输入当前研究任务")
        overview = self.overview()
        pointers = overview["pointers"]
        tokens = set(re.findall(r"[a-z0-9_.-]+|[\u4e00-\u9fff]", query.lower()))
        def rank(row: dict[str, Any]) -> tuple[int, str]:
            text = canonical({k: row.get(k) for k in ("title", "architecture", "recipe", "annotations", "error")}).lower()
            return sum(token in text for token in tokens) + (20 if row["id"] in pointers.values() else 0), row["created_at"]
        ordered = sorted(overview["records"], key=rank, reverse=True)
        hits = []
        for summary in ordered[:max(1, min(limit, 10))]:
            row = self.get(summary["id"])
            check = compatibility(row.get("protocol", {}), protocol or {})
            reusable = (check["compatible"] and row["status"] == "completed" and not row.get("is_mock")
                        and row["result_quality"] == "measured" and row["integrity"]["intact"])
            hits.append({"id": row["id"], "title": row["title"], "status": row["status"], "metrics": row["metrics"],
                         "architecture": row["architecture"], "recipe": row["recipe"], "annotations": row.get("annotations", {}),
                         "missing": row["missing"], "result_quality": row["result_quality"], "protocol": row["protocol"],
                         "roles": [k for k, v in pointers.items() if v == row["id"]], "integrity": row["integrity"],
                         "comparison": {**check, "reuse_allowed": reusable}, "evidence_count": len(row["evidence"]),
                         "detail_ref": f"/api/projects/{self.project}/research-memory/{row['id']}"})
        return {"project": self.project, "query": query, "hits": hits,
                "policy": "历史实验只在记录条件下成立。失败不等于方法无效。复用需要完整一致协议与完好测量证据；缺少信息优先补评测，不自动重训。"}


def memory_context(project: str, query: str, run_root: Path | None = None, invocation: str = "") -> str:
    """Bounded task retrieval, frozen per invocation for replay and prompt tracing."""
    if not (project_root(project) / "project.yaml").is_file():
        return ""
    memory = ResearchMemory(project)
    if not memory.root.exists():
        return ""
    snapshot = None
    if run_root:
        from app.harness.persistence import atomic_write_json, path_lock
        key = hashlib.sha256((project + query + invocation).encode()).hexdigest()[:32]
        snapshot = run_root / "context/research_memory" / (key + ".json")
        with path_lock(snapshot.with_suffix(".lock")):
            if snapshot.exists():
                record = json.loads(snapshot.read_text())
            else:
                record = memory.retrieve(query, limit=3)
                atomic_write_json(snapshot, record)
    else:
        record = memory.retrieve(query, limit=3)
    # Summaries only; exact config/metrics/figures remain available through the read tool.
    parts = ["## 项目基线与历史实验记忆（证据资料）", record["policy"]]
    for row in record["hits"]:
        compact = {k: row[k] for k in ("id", "title", "roles", "status", "metrics", "architecture", "recipe", "protocol",
                                      "comparison", "missing", "result_quality", "annotations", "detail_ref")}
        parts.append(canonical(compact))
    if snapshot:
        parts.append(f"本轮记忆快照: {snapshot}")
    parts.append("用 knowledge.research_memory 查询当前任务相关记录，knowledge.research_memory_read 读取完整配置、训练方式和证据。比较前传入实际protocol核对，未核对不得声称可复用。")
    return "\n".join(parts)


def archive_job(project: str, run_root: Path) -> dict[str, Any]:
    return ResearchMemory(project).import_directory(str(run_root / "execution"))
