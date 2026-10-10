"""Turn an attached real repository into a typed, inspectable handoff source.

Availability does not assert that a model has read or understood a file. File
contents remain tool-mediated, and a missing repository never becomes evidence.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import yaml

from app.harness.schema.frontmatter_parser import parse
from app.harness.runtime.project_scope import forbidden_source_path
from app.harness.tools.project_repo import ProjectRepo, resolve_allowed_path


def baseline_repository_context(repo: ProjectRepo, upstream: dict[str, str]) -> str:
    """Verify concrete code references from the approved handoff on disk."""
    if not repo.root.is_dir():
        return ""
    references: set[str] = set()
    for supplied in upstream.values():
        text = supplied.split("\n", 1)[1] if supplied.startswith("[upstream artifact: ") else supplied
        if not text.startswith("---\n"):
            continue
        metadata = parse(text).metadata
        if metadata.get("project") != repo.project:
            continue
        candidates = []
        if metadata.get("schema") == "proposal.v1":
            candidates = [item.get("ref") for item in metadata.get("evidence_refs", [])
                          if isinstance(item, dict) and item.get("kind") == "code"]
        elif metadata.get("schema") == "experiment_plan.v1":
            # A proposal may omit optional evidence_refs. The approved plan
            # still names the exact source configuration for its experiments.
            # Use only that typed identity, never prose or the new output config.
            candidates = [item["config"].get("base_config") for item in metadata.get("ablations", [])
                          if isinstance(item, dict) and isinstance(item.get("config"), dict)]
        for ref in candidates:
            # Mixed prose references are not file identities. Do not guess paths.
            if isinstance(ref, str) and not any(c.isspace() for c in ref) and Path(ref).suffix:
                references.add(ref)
    files = []
    for ref in sorted(references):
        if forbidden_source_path(ref):
            raise ValueError(f"Baseline handoff cannot reference a control or credential file: {ref}")
        path = resolve_allowed_path(repo, ref, require_exists=True, require_text=True)
        if not path.is_file():
            raise ValueError(f"Baseline handoff source is not a file: {ref}")
        files.append({"path": ref, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      "bytes": path.stat().st_size})
    if not files:
        return ""
    return json.dumps({"schema_id": "context.repository_available.v1", "project": repo.project,
        "repo_path": str(repo.root), "files": files, "contents_loaded": False,
        "instruction": "These actual files are available through code.repo_reader. Read the relevant contents before editing; availability is not proof of model consumption."}, ensure_ascii=False)


def attach_repository_handoff(project: str, upstream: dict[str, str]) -> None:
    """Preserve caller text; otherwise resolve approved refs in the bound repo."""
    if upstream.get("baseline_code", "").strip():
        return
    proposals = [parse(text.split("\n", 1)[1] if text.startswith("[upstream artifact: ") else text).metadata
                 for text in upstream.values() if text.startswith(("---\n", "[upstream artifact: "))]
    if not any(m.get("schema") == "proposal.v1" and m.get("project") == project
               and any(isinstance(item, dict) and item.get("kind") == "baseline_code"
                       for item in m.get("handoff", {}).get("required_context", [])) for m in proposals):
        return
    from app.harness.tools.project_repo import load_project_repo
    context = baseline_repository_context(load_project_repo(project), upstream)
    if context:
        upstream["baseline_code"] = context


def baseline_data_description(repo: ProjectRepo, upstream: dict[str, str]) -> str:
    """Describe actual baseline configuration, not inferred dataset contents."""
    baseline = baseline_repository_context(repo, upstream)
    if not baseline:
        return ""
    sources = []
    for item in json.loads(baseline)["files"]:
        if Path(item["path"]).suffix.lower() not in {".yaml", ".yml"}:
            continue
        path = resolve_allowed_path(repo, item["path"], require_exists=True, require_text=True)
        raw = yaml.safe_load(path.read_text())
        if not isinstance(raw, dict) or not isinstance(raw.get("data"), dict) or not raw["data"].get("path"):
            continue
        fields = {key: raw["data"][key] for key in ("path", "tx_key", "rx_key", "nf_key") if key in raw["data"]}
        parameters = raw.get("data_param", {})
        parameters = {key: parameters[key] for key in ("channels", "train_ratio", "xmax", "ymax")
                      if isinstance(parameters, dict) and key in parameters}
        sources.append({"config_path": item["path"], "config_sha256": item["sha256"],
                        "data": fields, "data_param": parameters})
    return json.dumps({"schema_id": "context.data_configuration.v1", "project": repo.project,
        "repo_path": str(repo.root), "sources": sources, "dataset_contents_loaded": False,
        "instruction": "Actual baseline data configuration only. Verify dataset availability and effective overrides during execution confirmation; do not infer its contents."}, ensure_ascii=False) if sources else ""
