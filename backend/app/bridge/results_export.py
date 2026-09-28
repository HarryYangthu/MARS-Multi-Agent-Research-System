"""Offline reports made only from the results service's public projections."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from html import escape
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from typing import Any
from uuid import uuid4
import zipfile

from app.bridge.results_service import canonical, collect_run_results, load_results_policy, safe_path, sha256
from app.storage.run_store import RunHandle


def _display(value: Any) -> str:
    return "未知 / 缺失" if value is None else str(value)


def _label(kind: str, value: Any) -> str:
    labels = {
        "status": {"created": "未启动", "running": "运行中", "completed": "流程已完成", "failed": "失败",
                   "blocked": "阻塞", "paused": "已暂停", "cancelled": "已取消", "unknown": "状态未知"},
        "authority": {"sqlite": "已保存的权威状态", "legacy_json": "旧版只读状态", "missing": "尚无状态记录", "invalid": "状态校验未通过"},
        "outcome": {"unknown": "目标尚不能判定", "goal_met": "目标达成", "goal_not_met": "目标未达成",
                    "budget_stopped": "预算停止", "user_cancelled": "用户取消", "blocked": "阻塞", "failed": "失败"},
        "role": {"baseline": "基线", "candidate": "候选", "ablation": "消融", "unknown": "未声明"},
        "verification": {"verified_local_receipt": "回执已核验", "unverified": "未验证", "invalid": "校验未通过"},
        "direction": {"minimize": "越低越好", "maximize": "越高越好"},
    }
    return labels.get(kind, {}).get(str(value), _display(value))


def _md(value: Any) -> str:
    text = escape(_display(value), quote=True)
    return re.sub(r"([\\`*_{}\[\]()#+.!|>-])", r"\\\1", text).replace("\n", " ").replace("\r", " ")


def _csv(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(fields)
    for row in rows:
        values = []
        for field in fields:
            value = row.get(field)
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                value = "'" + value
            values.append(value if value is not None else "")
        writer.writerow(values)
    return output.getvalue().encode("utf-8-sig")


def _curve_svg(curve: dict[str, Any]) -> str:
    values = curve["points"]
    low, high = min(values), max(values)
    # Scale before subtraction so opposite near-limit floats cannot overflow.
    scale = max(abs(low), abs(high), 1.0)
    normalized = [value / scale for value in values]
    bottom, top = min(normalized), max(normalized)
    points = " ".join(f"{40 + 520 * index / max(len(values) - 1, 1):.3f},{180 - 140 * (value - bottom) / (top - bottom or 1):.3f}"
                      for index, value in enumerate(normalized))
    label = escape(f"{curve['experiment_id']} · {curve['metric']}")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 230" role="img" aria-label="{label}">'
            '<rect width="600" height="230" fill="white"/><path d="M40 30V180H570" fill="none" stroke="#777"/>'
            f'<polyline points="{points}" fill="none" stroke="#235b8e" stroke-width="2"/>'
            f'<text x="40" y="18" font-size="12">{label}</text>'
            f'<text x="40" y="215" font-size="11">recorded point index; range {escape(str(low))} to {escape(str(high))}</text></svg>')


def render_files(result: dict[str, Any]) -> dict[str, bytes]:
    """All filenames and links are host generated, never taken from artifacts."""
    identity, state, outcome = result["identity"], result["state"], result["outcome"]
    fields = ("experiment_id", "job_id", "name", "value", "unit", "direction", "role", "verification", "source_id")
    headings = ("实验", "作业标识", "指标", "数值", "单位", "方向", "实验角色", "回执", "来源索引")
    table = '<table><thead><tr>' + ''.join(f'<th>{escape(field)}</th>' for field in headings) + '</tr></thead><tbody>'
    table += ''.join('<tr>' + ''.join(f'<td>{escape(_label(field, row.get(field)))}</td>' for field in fields) + '</tr>'
                     for row in result["metrics"])
    table += '</tbody></table>'
    if not result["metrics"]:
        table += '<p>尚无可展示的测量记录。</p>'
    md = ["# 研究结果", "", "研究问题：" + _md(identity["question"]), "",
          "流程状态：" + _md(_label("status", state["status"])) + "；状态来源：" + _md(_label("authority", state["authority"])),
          "目标结果：" + _md(_label("outcome", outcome["status"])), _md(outcome["reason"]), "", "## 测量记录", "",
          "| " + " | ".join(headings) + " |", "| " + " | ".join("---" for _ in fields) + " |"]
    md.extend("| " + " | ".join(_md(_label(field, row.get(field))) for field in fields) + " |" for row in result["metrics"])
    if not result["metrics"]:
        md.extend(["", "尚无可展示的测量记录。"])
    detail_tables = []
    for title, rows, columns, titles in (
        ("作业与条件", result["experiments"], ("experiment_id", "role", "status", "verification", "duration_seconds", "seed", "steps"),
         ("实验", "角色", "作业状态", "回执", "耗时（秒）", "记录的随机种子", "请求步数")),
        ("描述性统计", result["statistics"], ("experiment_id", "metric", "n", "mean", "standard_deviation"),
         ("实验", "指标", "已核验作业数", "均值", "样本标准差")),
    ):
        detail_tables.append(f'<h2>{title}</h2>')
        md.extend(["", "## " + title, ""])
        if not rows:
            detail_tables.append('<p>尚未记录。</p>')
            md.append("尚未记录。")
            continue
        detail_tables.append('<table><thead><tr>' + ''.join(f'<th>{escape(item)}</th>' for item in titles) + '</tr></thead><tbody>'
            + ''.join('<tr>' + ''.join(f'<td>{escape(_label(field, row.get(field)))}</td>' for field in columns) + '</tr>' for row in rows)
            + '</tbody></table>')
        md.extend(["| " + " | ".join(titles) + " |", "| " + " | ".join("---" for _ in columns) + " |"])
        md.extend("| " + " | ".join(_md(_label(field, row.get(field))) for field in columns) + " |" for row in rows)
    sections = []
    for title, values in (("事实", result["conclusions"]["facts"]), ("假设（非测量结论）", result["conclusions"]["hypotheses"]),
                          ("解释", result["conclusions"]["interpretations"]), ("局限", result["limitations"]),
                          ("外部复现前提", result["reproduction"]["external_requirements"])):
        values = values or ["尚未记录。"]
        sections.append(f'<section><h2>{escape(title)}</h2><ul>' + ''.join(f'<li>{escape(value)}</li>' for value in values) + '</ul></section>')
        md.extend(["", "## " + title, "", *("- " + _md(value) for value in values)])
    files: dict[str, bytes] = {"results.json": canonical(result) + b"\n", "metrics.csv": _csv(result["metrics"], fields),
        "experiments.csv": _csv(result["experiments"], ("experiment_id", "job_id", "role", "status", "verification", "duration_seconds", "seed", "steps", "configuration_sha256", "execution_backend", "os_isolated", "source_id")),
        "statistics.csv": _csv(result["statistics"], ("experiment_id", "metric", "n", "mean", "standard_deviation", "independent_repeats")),
        "evidence/sources.json": canonical({"schema_id": "result_sources.v1", "sources": result["sources"],
            "scope": "Original source digests only; private original files are not included. Receipt checks are not independent reproduction."}) + b"\n",
        "reproduction.json": canonical(result["reproduction"]) + b"\n"}
    charts = []
    for index, curve in enumerate(result["curves"], 1):
        name = f"curves/curve-{index:03d}.svg"
        svg = _curve_svg(curve)
        files[name] = svg.encode("utf-8")
        charts.append(svg)
        md.extend(["", f"![Recorded curve {index}]({name})"])
    md.extend(["", "## 离线文件", "", "- [完整 JSON](results.json)", "- [指标 CSV](metrics.csv)",
               "- [实验 CSV](experiments.csv)", "- [描述性统计 CSV](statistics.csv)",
               "- [来源哈希索引](evidence/sources.json)", "- [复现前提](reproduction.json)", "- [校验清单](manifest.json)", ""])
    files["report.md"] = "\n".join(md).encode("utf-8")
    resources = escape(json.dumps(result["resources"], ensure_ascii=False, indent=2, allow_nan=False))
    history = escape(json.dumps(result["history"], ensure_ascii=False, indent=2, allow_nan=False))
    protocol = escape(json.dumps(result["protocol"], ensure_ascii=False, indent=2, allow_nan=False))
    md.extend(["## 资源与协议", "", "[资源用量、已声明协议与状态记录见完整 JSON](results.json)。预算预留不等于实际消耗；协议声明不等于已执行条件。", ""])
    files["report.md"] = "\n".join(md).encode("utf-8")
    html = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>研究结果</title>'
        '<style>body{font:16px/1.6 system-ui,sans-serif;margin:3rem auto;max-width:1100px;padding:0 1.5rem;color:#172033}'
        'table{border-collapse:collapse;font-size:13px;width:100%}td,th{border:1px solid #ccd3dd;padding:.4rem;text-align:left;overflow-wrap:anywhere}'
        'pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f5f8;padding:1rem}svg{max-width:600px;width:100%}'
        '@media print{body{margin:0;max-width:none}table{font-size:10px}}</style><main><h1>研究结果</h1>'
        f'<p>{escape(_display(identity["question"]))}</p><p>运行：{escape(identity["run_id"])} · {escape(identity["project"])}</p>'
        f'<p>流程状态：<strong>{escape(_label("status", state["status"]))}</strong>（{escape(_label("authority", state["authority"]))}）</p>'
        f'<p>目标结果：<strong>{escape(_label("outcome", outcome["status"]))}</strong> · {escape(outcome["reason"])}</p>'
        '<p>离线可审阅；未验证独立复现。单次测量不代表统计显著性。</p>'
        + ''.join(sections) + '<h2>测量记录</h2>' + table + ''.join(charts) + ''.join(detail_tables)
        + '<p>标准差只描述相同声明配置/命令且记录不同种子的作业数值，不认证统计独立性；数据样本数与匹配协议尚未核验。</p>'
        + f'<h2>资源记录</h2><pre>{resources}</pre><h2>声明的研究协议</h2><pre>{protocol}</pre><h2>状态转换</h2><pre>{history}</pre>'
        + '<h2>离线文件</h2><ul>' + ''.join(f'<li><a href="{name}">{label}</a></li>' for name, label in (
            ("report.md", "Markdown"), ("results.json", "JSON"), ("metrics.csv", "指标 CSV"), ("experiments.csv", "实验 CSV"),
            ("statistics.csv", "描述性统计"), ("evidence/sources.json", "来源索引"), ("reproduction.json", "复现前提"), ("manifest.json", "校验清单")))
        + '</ul><p>可用浏览器打印功能另存 PDF；本包没有自动生成或验证 PDF 文件。</p></main></html>')
    files["report.html"] = html.encode("utf-8")
    return files


def create_results_export(run: RunHandle) -> dict[str, Any]:
    result = collect_run_results(run)
    files = render_files(result)
    policy = load_results_policy()
    identifier = uuid4().hex
    manifest = {"schema_id": "results_export_manifest.v1", "export_id": identifier, "run_id": result["identity"]["run_id"],
        "created_at": datetime.now(timezone.utc).isoformat(), "scope": "sanitized_result_projections",
        "reproduction_status": "reviewable_only", "independent_rerun_verified": False,
        "files": [{"path": name, "sha256": sha256(data), "bytes": len(data)} for name, data in sorted(files.items())]}
    files["manifest.json"] = canonical(manifest) + b"\n"
    if sum(map(len, files.values())) > policy["max_export_bytes"]:
        raise ValueError("Result export exceeds the configured limit")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for name, data in sorted(files.items()):
            target.writestr(name, data)
    encoded = archive.getvalue()
    if len(encoded) > policy["max_export_bytes"]:
        raise ValueError("Result archive exceeds the configured limit")
    base = safe_path(run.root, "results/exports")
    base.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".partial-", dir=base))
    destination = safe_path(run.root, "results/exports/" + identifier)
    try:
        (temporary / "results.zip").write_bytes(encoded)
        (temporary / "archive.sha256").write_text(sha256(encoded), encoding="ascii")
        (temporary / "manifest.json").write_bytes(files["manifest.json"])
        temporary.rename(destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {"export_id": identifier, "download_url": f"/api/results/{run.run_id}/exports/{identifier}/download",
            "archive_sha256": sha256(encoded), "archive_bytes": len(encoded), "manifest": manifest}


def read_results_export(run: RunHandle, export_id: str) -> bytes:
    if re.fullmatch(r"[a-f0-9]{32}", export_id) is None:
        raise ValueError("Invalid result export identifier")
    prefix = "results/exports/" + export_id + "/"
    path = safe_path(run.root, prefix + "results.zip")
    limit = load_results_policy()["max_export_bytes"]
    encoded = _bounded_file(path, limit)
    checksum_path = safe_path(run.root, prefix + "archive.sha256")
    checksum = _bounded_file(checksum_path, 64)
    if len(checksum) != 64 or sha256(encoded).encode("ascii") != checksum:
        raise ValueError("Result archive checksum mismatch")
    with zipfile.ZipFile(io.BytesIO(encoded)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or sum(item.file_size for item in archive.infolist()) > limit:
            raise ValueError("Invalid result archive members")
        manifest = json.loads(archive.read("manifest.json"))
        if (not isinstance(manifest, dict) or manifest.get("schema_id") != "results_export_manifest.v1" or manifest.get("export_id") != export_id
                or manifest.get("run_id") != run.run_id or not isinstance(manifest.get("files"), list)):
            raise ValueError("Result manifest identity mismatch")
        for item in manifest["files"]:
            if (not isinstance(item, dict) or not isinstance(item.get("path"), str)
                    or type(item.get("bytes")) is not int or item["bytes"] < 0
                    or not isinstance(item.get("sha256"), str) or re.fullmatch(r"[a-f0-9]{64}", item["sha256"]) is None):
                raise ValueError("Invalid result manifest file")
        expected = {item["path"] for item in manifest["files"]}
        if expected | {"manifest.json"} != set(names) or len(expected) != len(manifest["files"]):
            raise ValueError("Result manifest member mismatch")
        for item in manifest["files"]:
            safe_path(run.root, item["path"])
            info = archive.getinfo(item["path"])
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Archive symlinks are not supported")
            data = archive.read(item["path"])
            if len(data) != item["bytes"] or sha256(data) != item["sha256"]:
                raise ValueError("Result file checksum mismatch")
    return encoded


def _bounded_file(path: Path, limit: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("Result archive exceeds the configured limit")
        value = stream.read(limit + 1)
        if len(value) > limit:
            raise ValueError("Result archive exceeds the configured limit")
        return value
