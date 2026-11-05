from __future__ import annotations

import json
import re
import struct
import zipfile
import zlib
from pathlib import Path

import pytest
from docx import Document
from lxml import etree
from openpyxl import load_workbook

from mars_app import core, export
from mars_app.export import deck, fmt

CORE_API = Path(__file__).resolve().parents[2] / "mars-core/mars/api.py"


def _png(path: Path, width: int = 4, height: int = 3) -> Path:
    """A real (tiny) PNG file."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return path


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


METRICS = {"primary": "error", "fields": {"error": {"label": "误差", "better": "lower"}, "size": {"label": "规模", "better": "lower"}}}


def _stage(error: float, base: float, gap: float, **extra: object) -> dict:
    return {"metrics": {"error": error, "size": 3}, "baseline_metrics": {"error": base, "size": 2}, "worse_by": {"error": gap, "size": 1},
            "primary": "error", **extra}


@pytest.fixture()
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, object]:
    """A small refine-like run in a project folder: one user proposal, one screened iteration with its plots in runs/."""
    api = core.load(CORE_API.parents[1])
    project = tmp_path / "project"
    monkeypatch.setenv("MARS_DEFAULT_PROJECT", str(project))
    run_dir = project / "runs/demo"
    run_dir.mkdir(parents=True)
    (run_dir / "contract.yaml").write_text("title: 测试研究\nobjective: 降低误差\ntargets: {gates: [{metric: error, max_worse: 0}], "
                                           "checks: [{check: build, field: size, max: 4, stop: true}]}\nconstraints: [只改模型]\n"
                                           "budget: {routes: 1}\n", encoding="utf-8")
    _write(run_dir / "execution.json", {"snapshot_version": 2, "binding": {"id": "b", "config": {"metrics": METRICS}}})
    (run_dir / "events.jsonl").write_text("".join(json.dumps({"ts": f"2026-11-05 10:00:0{i}", "stage": "step2", "message": m},
                                                             ensure_ascii=False) + "\n"
                                                  for i, m in enumerate(["U00 第 1 轮：开始实现", "U00 第 1 轮完成：stopped"])),
                                          encoding="utf-8")
    (run_dir / "step1/r0").mkdir(parents=True)
    (run_dir / "step1/r0/user_proposal.md").write_text("加二次项", encoding="utf-8")
    _write(run_dir / "step1/r0/ranking.json", [{"id": "U00", "title": "二次项", "score": "用户方案", "precheck": []}])
    _write(run_dir / "step1/r0/handoff.json", [{"id": "U00", "proposal": {"title": "二次项", "one_sentence": "加特征",
                                                                         "resource_estimate": "3 个参数"}}])
    _png(run_dir / "step2/plots/baseline_screen_a_curve.png")
    _png(run_dir / "step2/U00/i1/plots/screen_a_curve.png")
    _write(run_dir / "step2/baseline_screen.json", {"a": {"metrics": {"error": 5.0}}, "b": {"metrics": {"error": 4.0}}})
    _write(run_dir / "step2/U00/i1/record.json", {
        "iteration": 1, "task": {"summary": "加二次项"}, "branch": "cand/demo-U00-i1", "commit": "abcdef1234567",
        "implementation": {"ok": True},
        "evidence": {"check_gates": {"all": True, "stop": False, "gates": [{"check": "build", "field": "size", "max": 4, "values": [3], "ok": True}]},
                     "screen": {"b": _stage(14.0, 4.0, 10.0, margin=0.2, passed=False), "a": _stage(15.0, 5.0, 10.0, margin=0.2, passed=False)},
                     "plots": {"screen_a_curve.png": "plots/screen_a_curve.png"}},
        "decision": {"verdict": "refuted", "mechanism_assessment": "=1+1 不成立", "next_change": None}})
    return run_dir, api


def test_export_chooses_full_verification_over_a_better_screen(run: tuple[Path, object]) -> None:
    from mars_app.export.context import gather
    run_dir, api = run
    original = json.loads((run_dir / "step2/U00/i1/record.json").read_text())
    for k, screen, gap in [(2, 0.35, 0.008), (3, 0.2, 18.0)]:
        item = json.loads(json.dumps(original))
        item["iteration"] = k
        for d in ("a", "b"):
            item["evidence"]["screen"][d]["worse_by"]["error"] = screen
        item["evidence"]["verify"] = {d: {**_stage(1.0 + gap, 1.0, gap), "gates": {"error": False}, "passed": False} for d in ("a", "b")}
        _write(run_dir / f"step2/U00/i{k}/record.json", item)
    context = gather(run_dir, api)
    assert context["best"]["iteration"]["iteration"] == 2
    assert context["routes"][0]["best"]["iteration"] == 2


@pytest.mark.skipif(not CORE_API.exists(), reason="mars-core checkout not next to mars-app")
def test_three_formats_from_run_artifacts(run: tuple[Path, object], tmp_path: Path) -> None:
    run_dir, api = run
    out = tmp_path / "exports"
    document = Document(export.export("docx", run_dir, api, out))
    headings = [p.text for p in document.paragraphs if p.style.name.startswith("Heading")]
    assert {"1 概要", "2 研究约定", "用户方案原文", "3 Step1 · 提方案", "4 Step2 · 进化"} <= set(headings)
    assert len(document.inline_shapes) == 2  # baseline + candidate images the core copied into runs/
    cells = {c.text for t in document.tables for row in t.rows for c in row.cells}
    assert "用户方案" in cells and "+10.0000 / +10.0000" in cells and "验收：误差" in cells and "误差" in cells

    book = load_workbook(export.export("xlsx", run_dir, api, out))
    assert book.sheetnames == ["概览", "研究约定", "Step2逐轮", "筛选明细", "验收", "事件", "图表"] or \
        book.sheetnames == ["概览", "研究约定", "Step1排序", "Step2逐轮", "筛选明细", "验收", "事件", "图表"]
    step2 = book["Step2逐轮"]
    header = [c.value for c in step2[1]]
    row = {h: c for h, c in zip(header, step2[2])}
    assert row["检查门槛"].value == "是" and row["筛选主指标差于基线均值"].value == 10.0
    assert row["机制判断"].value == "=1+1 不成立" and row["机制判断"].data_type == "s"  # text, never a formula
    assert [c.value for c in book["筛选明细"]["C"][1:]] == ["a", "b"]

    pptx_path = export.export("pptx", run_dir, api, out)
    with zipfile.ZipFile(pptx_path) as archive:
        slides = [n for n in archive.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)]
        for name in slides:
            etree.fromstring(archive.read(name))
        assert len([n for n in archive.namelist() if n.startswith("ppt/media/")]) == 1
    assert sorted(p.suffix for p in (out / "demo").iterdir()) == [".docx", ".pptx", ".xlsx"]  # no temp files left


def test_unknown_format_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        export.export("pdf", tmp_path, None, tmp_path)  # type: ignore[arg-type]


def test_png_size_and_formatting(tmp_path: Path) -> None:
    assert deck.png_size(_png(tmp_path / "x.png", 7, 5)) == (7, 5)
    assert fmt.target(True) == "是" and fmt.target(1) == "1" and fmt.target(0.7) == "0.70"
    assert fmt.score("用户方案") == "用户方案" and fmt.score(7.364) == "7.36"
    assert fmt.pair({"b": 0.1, "a": None}, sign=True) == "— / +0.100"
    assert fmt.clip("a\nb", 10) == "a\nb" and fmt.short("a\nb", 10) == "a b"
