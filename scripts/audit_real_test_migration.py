"""Inventory legacy test doubles for manual migration; never delete or run tests.

This conservative AST inventory is not a proof that unflagged tests use no
doubles. Exit 1 means migration/review remains; no blanket skips are installed.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any


def inventory(root: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    removed = ("mock_provider", "mock_simulation", "mars_posttrain.dry_run")
    prefixes = ("Fake", "_Fake", "Dummy", "_Dummy", "Stub", "_Stub", "Mock", "_Mock")
    for path in sorted((root / "backend" / "tests").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            reason = ""
            if isinstance(node, ast.ImportFrom) and any(x in (node.module or "") for x in removed):
                reason = "imports removed simulated runtime"
            elif isinstance(node, ast.Import) and any(any(x in n.name for x in removed) for n in node.names):
                reason = "imports removed simulated runtime"
            elif isinstance(node, ast.ClassDef) and node.name.startswith(prefixes):
                reason = "named test double; migrate its assertions to real behavior"
            elif isinstance(node, ast.ClassDef) and any(
                isinstance(b, ast.Name) and b.id == "LLMProvider" for b in node.bases
            ):
                reason = "test-defined model provider; inspect for generated sample responses"
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "setattr":
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and node.args[1].value in {
                    "complete", "dispatch", "_client", "_get_client", "run_one", "run_batch",
                    "_call_llm", "_select_provider", "AsyncClient", "Client", "post", "get",
                }:
                    reason = "replaces a model/tool/service execution dependency"
            if reason:
                findings.append({"path": str(path.relative_to(root)), "line": int(getattr(node, "lineno", 0)), "reason": reason})
    return findings


def main() -> int:
    findings = inventory(Path(__file__).resolve().parents[1])
    sys.stdout.write(json.dumps({
        "status": "migration_required" if findings else "no_known_patterns",
        "test_execution_performed": False, "automatic_deletion_performed": False,
        "flagged_files": len({item["path"] for item in findings}), "findings": findings,
        "limitation": "AST heuristics require manual review; absence is not proof.",
    }, ensure_ascii=False, indent=2) + "\n")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
