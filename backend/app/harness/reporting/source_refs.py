"""Validate report references without creating or repairing model evidence."""
from pathlib import Path

from app.harness.runtime.project_scope import safe_scope_path
from app.harness.schema.frontmatter_parser import parse


def report_source_errors(root: Path, text: str) -> list[str]:
    refs = parse(text).metadata.get("chain_refs", {})
    errors: list[str] = []
    names = [(key, refs[key]) for key in ("proposal", "plan", "code") if refs.get(key)]
    names.extend((f"runs/{index}", name) for index, name in enumerate(refs.get("runs", [])))
    for key, name in names:
        try:
            safe_scope_path(root, name, must_exist=True)
        except (ValueError, OSError, TypeError) as exc:
            errors.append(f"/chain_refs/{key}: {name!r} is not an available file in this run: {exc}. "
                          "Use the exact admitted source reference, preserving its full filename.")
    for name in ("execution/metrics.json", "execution/batch_summary.json"):
        try:
            exists = safe_scope_path(root, name).is_file()
        except (ValueError, OSError):
            exists = False
        if exists and name not in refs.get("runs", []):
            errors.append(f"/chain_refs/runs: include actual measured source {name}; an approved plan cannot replace it")
    return errors
