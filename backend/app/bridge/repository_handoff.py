"""Turn an attached real repository into a typed, inspectable handoff source.

Availability does not assert that a model has read or understood a file. File
contents remain tool-mediated, and a missing repository never becomes evidence.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
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
            if isinstance(ref, str):
                for prefix in ('code.repo_reader:', 'repo_reader:'):
                    if ref.startswith(prefix):
                        ref = ref[len(prefix):]
                        break
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


def repository_data_configuration(repo: ProjectRepo, ref: str) -> dict[str, Any] | None:
    """Read named configuration only; data availability is checked at launch review."""
    if forbidden_source_path(ref):
        raise ValueError(f"Data configuration cannot reference a control or credential file: {ref}")
    path = resolve_allowed_path(repo, ref, require_exists=True, require_text=True)
    if not path.is_file() or path.suffix.lower() not in {".yaml", ".yml"}:
        return None
    content = path.read_bytes()
    raw = yaml.safe_load(content)
    if not isinstance(raw, dict):
        return None
    nested = raw.get("data", {})
    fields = {key: nested[key] for key in ("path", "tx_key", "rx_key", "nf_key")
              if isinstance(nested, dict) and key in nested}
    if raw.get("data_path"):
        if fields.get("path") and fields["path"] != raw["data_path"]:
            raise ValueError(f"{ref} 的 data_path 与 data.path 不一致，无法核对实际数据入口")
        fields["path"] = raw["data_path"]
    if not fields.get("path"):
        return None
    if not isinstance(fields["path"], str) or not fields["path"].strip():
        raise ValueError(f"{ref} 的数据路径必须是非空字符串")
    parameters = raw.get("data_param", {})
    parameters = {key: parameters[key] for key in ("channels", "train_ratio", "xmax", "ymax")
                  if isinstance(parameters, dict) and key in parameters}
    protocol = {key: raw[key] for key in ("scale", "fs", "band", "bound0", "bound1",
                "train_fraction", "validation_fraction", "split_guard", "context") if key in raw}
    return {"config_path": ref, "config_sha256": hashlib.sha256(content).hexdigest(),
            "data": fields, "data_param": parameters, "protocol": protocol}


def baseline_data_description(repo: ProjectRepo, upstream: dict[str, str]) -> str:
    """Bind data from approved delivery, falling back to actual baseline references.

    Output configuration is not baseline-code evidence. At execution, however,
    the approved experiment/coding identity is the source of effective data knobs.
    Never scrape a proposal's prose or use today's project default for an old run.
    """
    from app.harness.schema.experiment_contract import (
        delivery_experiments, document_metadata, experiment_errors,
    )
    plan_text = upstream.get("experiment_plan.approved.md", "")
    coding_text = upstream.get("code_spec.approved.md", "")
    configurations: set[str] = set()
    rows: list[dict[str, Any]] = []
    if coding_text:
        rows = delivery_experiments(plan_text, document_metadata(coding_text), project=repo.project)
    elif plan_text:
        plan = document_metadata(plan_text)
        if plan.get("schema") != "experiment_plan.v1" or plan.get("project") != repo.project:
            raise ValueError("数据配置交接与当前项目不一致")
        errors = experiment_errors(plan)
        if errors:
            raise ValueError("数据配置交接未通过：" + "；".join(errors))
        rows = plan["ablations"]
    for row in rows:
        config = row["config"]
        ref = (config.get("base_config") if not coding_text else None) or config.get("config_path") or config.get("cfg")
        if isinstance(ref, str) and ref:
            configurations.add(ref)
    # Preserve the existing baseline-only fallback when no executable delivery
    # names a configuration. Do not blend baseline data into a candidate plan.
    if not configurations:
        baseline = baseline_repository_context(repo, upstream)
        if baseline:
            configurations.update(item["path"] for item in json.loads(baseline)["files"]
                                  if Path(item["path"]).suffix.lower() in {".yaml", ".yml"})
    sources = [source for ref in sorted(configurations)
               if (source := repository_data_configuration(repo, ref)) is not None]
    return json.dumps({"schema_id": "context.data_configuration.v1", "project": repo.project,
        "repo_path": str(repo.root), "sources": sources, "dataset_contents_loaded": False,
        "instruction": "Actual approved data configuration only. Verify dataset availability and effective overrides during execution confirmation; do not infer its contents."}, ensure_ascii=False) if sources else ""
