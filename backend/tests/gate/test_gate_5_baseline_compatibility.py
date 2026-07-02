"""Gate 5 must hook into the tool dispatch path and block forbidden ops."""
from __future__ import annotations

import pytest

from app.harness.gates.baseline_compatibility import (
    GATE_ID,
    static_check,
)
from app.harness.tools.registry import ToolContext, ToolResult, reset_for_tests


def _ctx() -> ToolContext:
    return ToolContext(run_id="r1", project="pimc", agent="coding")


def test_writing_to_baseline_path_blocked() -> None:
    out = static_check(
        project="pimc",
        tool_name="code.patch_generator",
        args={"path": "baseline/checkpoint.pt", "diff": "x"},
    )
    assert out.triggered and out.blocking
    assert "baseline/" in out.reason


def test_writing_to_production_interface_blocked() -> None:
    out = static_check(
        project="pimc",
        tool_name="code.write_file",
        args={"path": "production_interface/x.py", "content": "x"},
    )
    assert out.triggered and out.blocking


def test_writing_to_allowed_path_passes() -> None:
    out = static_check(
        project="pimc",
        tool_name="code.patch_generator",
        args={"path": "libs/Router.py", "diff": "+ new code"},
    )
    assert not out.triggered


def test_apply_patch_to_baseline_path_blocked() -> None:
    out = static_check(
        project="pimc",
        tool_name="code.apply_patch",
        args={"files": [{"path": "baseline/x.py"}], "diff": "+x"},
    )
    assert out.triggered and out.blocking


def test_apply_patch_diff_only_to_baseline_path_blocked() -> None:
    diff = """diff --git a/baseline/x.py b/baseline/x.py
--- a/baseline/x.py
+++ b/baseline/x.py
@@ -1 +1 @@
-old
+new
"""
    out = static_check(
        project="pimc",
        tool_name="code.apply_patch",
        args={"diff": diff},
    )
    assert out.triggered and out.blocking
    assert "baseline/" in out.reason


def test_apply_patch_diff_only_to_protected_class_blocked() -> None:
    diff = """diff --git a/libs/model.py b/libs/model.py
--- a/libs/model.py
+++ b/libs/model.py
@@ -1 +1 @@
-class Paper_Total_0327: pass
+class Paper_Total_0327: ...
"""
    out = static_check(
        project="pimc",
        tool_name="code.apply_patch",
        args={"diff": diff},
    )
    assert out.triggered and out.blocking
    assert "Paper_Total_0327" in out.reason


def test_forward_signature_break_blocked() -> None:
    bad_diff = """
@@
+    def forward(self, x, weights, label):
+        return x
"""
    out = static_check(
        project="pimc",
        tool_name="code.patch_generator",
        args={"path": "libs/model.py", "diff": bad_diff},
    )
    assert out.triggered, "forward(x, weights, label) should fail because third arg is not stream_label"
    assert "stream_label" in out.reason


def test_forward_signature_ok_passes() -> None:
    good_diff = """
+    def forward(self, x, stream_label, *, gain=1.0):
+        return x
"""
    out = static_check(
        project="pimc",
        tool_name="code.patch_generator",
        args={"path": "libs/model.py", "diff": good_diff},
    )
    assert not out.triggered


def test_unmonitored_tool_passes() -> None:
    out = static_check(
        project="pimc",
        tool_name="code.repo_reader",
        args={"path": "production_interface/x"},
    )
    assert not out.triggered


@pytest.mark.asyncio
async def test_gate_runs_inside_dispatch_and_blocks() -> None:
    """The integration we care about: dispatch() must short-circuit on Gate 5."""
    reg = reset_for_tests()

    called = False

    async def fake_tool(args: dict[str, object], ctx: ToolContext) -> ToolResult:
        nonlocal called
        called = True
        return ToolResult(ok=True, output="should not run")

    reg.register("code.patch_generator", fake_tool, override=True)

    res = await reg.dispatch(
        "code.patch_generator",
        {"path": "baseline/x", "diff": "x"},
        _ctx(),
    )
    assert called is False
    assert res.ok is False
    assert res.blocked_by_gate == GATE_ID


@pytest.mark.asyncio
async def test_dispatch_lets_safe_tools_through() -> None:
    reg = reset_for_tests()

    async def fake_tool(args: dict[str, object], ctx: ToolContext) -> ToolResult:
        return ToolResult(ok=True, output="ran")

    reg.register("code.patch_generator", fake_tool, override=True)
    res = await reg.dispatch(
        "code.patch_generator",
        {"path": "libs/x.py", "diff": "x"},
        _ctx(),
    )
    assert res.ok is True
    assert res.output == "ran"
