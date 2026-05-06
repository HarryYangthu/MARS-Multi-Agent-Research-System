"""Static check: the bridge package never imports concrete agent modules.

This is enforced by .importlinter; we keep an extra unit-test guard so a
test failure surfaces the issue even when CI runs only pytest.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BRIDGE_DIR = REPO_ROOT / "backend" / "app" / "bridge"
FORBIDDEN_PREFIXES = (
    "app.agents.idea",
    "app.agents.experiment",
    "app.agents.coding",
    "app.agents.execution",
    "app.agents.writing",
)


def _walk_imports(path: Path) -> list[str]:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                out.append(n.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                out.append(node.module)
    return out


def test_bridge_does_not_import_concrete_agents() -> None:
    failures = []
    for py in BRIDGE_DIR.rglob("*.py"):
        for imp in _walk_imports(py):
            for pref in FORBIDDEN_PREFIXES:
                if imp == pref or imp.startswith(pref + "."):
                    failures.append((py.relative_to(REPO_ROOT), imp))
    assert not failures, (
        "bridge/ must look up agents via agent_registry, not direct import. "
        f"violations={failures}"
    )
