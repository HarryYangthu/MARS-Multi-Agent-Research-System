"""Real local source availability; never a model/tool success substitute."""
from pathlib import Path
import hashlib
import json

import pytest

from app.bridge.repository_handoff import baseline_repository_context
from app.bridge.repository_handoff import baseline_data_description
from app.harness.schema.frontmatter_parser import dumps
from app.harness.tools.project_repo import ProjectRepo


def repo(root: Path) -> ProjectRepo:
    return ProjectRepo("local", root, "local_path", True, ("",), ("baseline/",), ())


def proposal(ref: str, project: str = "local") -> dict[str, str]:
    text = dumps({"schema": "proposal.v1", "project": project,
                  "evidence_refs": [{"kind": "code", "ref": ref}]}, "Human-authored source reference.")
    return {"idea_proposal.approved.md": "[upstream artifact: idea/idea_proposal.approved.md]\n" + text}


def test_actual_source_has_path_and_hash_but_no_consumption_claim(tmp_path: Path) -> None:
    path = tmp_path / "train.py"
    path.write_text("value = 1\n")
    context = json.loads(baseline_repository_context(repo(tmp_path), proposal("train.py")))
    assert context["files"] == [{"path": "train.py", "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": 10}]
    assert context["contents_loaded"] is False
    path.write_text("value = 2\n")
    updated = json.loads(baseline_repository_context(repo(tmp_path), proposal("train.py")))
    assert updated["files"][0]["sha256"] != context["files"][0]["sha256"]


@pytest.mark.parametrize('prefix', ['repo_reader:', 'code.repo_reader:'])
def test_reader_provenance_prefix_preserves_the_actual_file_scope(tmp_path: Path, prefix: str) -> None:
    path = tmp_path / 'model.py'
    path.write_text('VALUE = 1\n')
    result = json.loads(baseline_repository_context(repo(tmp_path), proposal(prefix + 'model.py')))
    assert result['files'][0]['path'] == 'model.py'
    assert result['files'][0]['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    for unsafe in ['../outside.py', '.env.py']:
        with pytest.raises(ValueError):
            baseline_repository_context(repo(tmp_path), proposal(prefix + unsafe))


@pytest.mark.parametrize("name", ["missing.py", "../outside.py", ".env.py"])
def test_missing_escaping_or_credential_source_is_not_available(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError):
        baseline_repository_context(repo(tmp_path), proposal(name))


def test_other_projects_and_prose_do_not_supply_code(tmp_path: Path) -> None:
    (tmp_path / "train.py").write_text("value = 1\n")
    assert baseline_repository_context(repo(tmp_path), proposal("train.py", "other")) == ""
    assert baseline_repository_context(repo(tmp_path), proposal("train.py + explanation")) == ""
    assert baseline_repository_context(repo(tmp_path / "not-present"), proposal("train.py")) == ""


def test_actual_baseline_dataset_configuration_is_described_without_loading_data(tmp_path: Path) -> None:
    """Actual author-written config; no dataset execution result is represented."""
    path = tmp_path / 'baseline.yaml'
    path.write_text('data:\n  path: data/not-loaded.pth\n  tx_key: x\n  password: secret\ndata_param:\n  channels: 16\n')
    context = json.loads(baseline_data_description(repo(tmp_path), proposal(path.name)))
    assert context["dataset_contents_loaded"] is False
    assert context["sources"][0]["data"]["path"]
    assert set(context["sources"][0]["data"]) <= {"path", "tx_key", "rx_key", "nf_key"}
    assert 'secret' not in json.dumps(context)


def test_approved_experiment_base_config_supplies_actual_repository_identity(tmp_path: Path) -> None:
    path = tmp_path / "base.yaml"
    path.write_text("lr_init: 0.0004\ndata:\n  path: data/not-loaded.pth\n")
    plan = dumps({"schema": "experiment_plan.v1", "project": "local",
                  "estimated_runs": 1, "ablations": [{"name": "candidate", "config": {
                      "base_config": path.name, "config_path": "new.yaml", "seed": 2026, "budget_steps": 50}}]},
                 "Human-authored approved plan input; the output configuration does not exist yet.")
    upstream = {"experiment_plan.approved.md": "[upstream artifact: experiment/experiment_plan.approved.md]\n" + plan}
    context = json.loads(baseline_repository_context(repo(tmp_path), upstream))
    assert context["files"] == [{"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                 "bytes": path.stat().st_size}]
    assert not context["contents_loaded"]
    assert json.loads(baseline_data_description(repo(tmp_path), upstream))["sources"][0]["config_path"] == path.name
    assert baseline_repository_context(repo(tmp_path), {"plan": plan.replace("project: local", "project: other")}) == ""


@pytest.mark.parametrize("source", ["missing.yaml", "../outside.yaml", ".env.yaml"])
def test_experiment_base_config_does_not_bypass_source_checks(tmp_path: Path, source: str) -> None:
    plan = dumps({"schema": "experiment_plan.v1", "project": "local",
                  "ablations": [{"config": {"base_config": source}}]}, "Authored path check input.")
    with pytest.raises(ValueError):
        baseline_repository_context(repo(tmp_path), {"plan": plan})


def test_new_output_config_and_prose_do_not_supply_a_baseline(tmp_path: Path) -> None:
    (tmp_path / "new.yaml").write_text("seed: 1\n")
    for config in [{"config_path": "new.yaml"}, {"base_config": "new.yaml plus prose"}]:
        plan = dumps({"schema": "experiment_plan.v1", "project": "local",
                      "ablations": [{"config": config}]}, "Authored identity check input.")
        assert baseline_repository_context(repo(tmp_path), {"plan": plan}) == ""


def executable_data_plan(ref: str, project: str = "local") -> dict[str, str]:
    plan = dumps({"schema": "experiment_plan.v1", "project": project, "estimated_runs": 1,
                  "ablations": [{"name": "candidate", "config": {
                      "config_path": ref, "seed": 2026, "budget_unit": "epochs", "max_iters": 100}}]},
                 "Human-authored approved configuration; no measured result.")
    return {"experiment_plan.approved.md": "[upstream artifact: experiment/experiment_plan.approved.md]\n" + plan}


@pytest.mark.parametrize("field", ["data_path: data/not-loaded.pth", "data:\n  path: data/not-loaded.pth"])
def test_approved_execution_configuration_supplies_data_without_baseline_or_selection(
    tmp_path: Path, field: str,
) -> None:
    config = tmp_path / "candidate.yaml"
    config.write_text(field + "\nscale: 32768\nfs: 245.76\nband: [-29.74, -24.74]\npassword: secret\n")
    upstream = executable_data_plan(config.name)
    assert baseline_repository_context(repo(tmp_path), upstream) == ""
    described = json.loads(baseline_data_description(repo(tmp_path), upstream))
    source = described["sources"][0]
    assert source["config_path"] == config.name
    assert source["config_sha256"] == hashlib.sha256(config.read_bytes()).hexdigest()
    assert source["data"]["path"] == "data/not-loaded.pth"
    assert source["protocol"] == {"scale": 32768, "fs": 245.76, "band": [-29.74, -24.74]}
    assert described["dataset_contents_loaded"] is False
    assert "secret" not in json.dumps(described)


def test_execution_data_is_not_replaced_with_unrelated_baseline_or_proposal_prose(tmp_path: Path) -> None:
    (tmp_path / "base.yaml").write_text("data:\n  path: data/baseline.pth\n")
    (tmp_path / "new.yaml").write_text("seed: 2026\n")
    upstream = {**proposal("base.yaml"), **executable_data_plan("new.yaml")}
    assert baseline_data_description(repo(tmp_path), upstream) == ""
    assert baseline_data_description(repo(tmp_path), {"description": "data_path: data/invented.pth"}) == ""


@pytest.mark.parametrize("ref", ["missing.yaml", "../outside.yaml", ".env.yaml"])
def test_approved_data_configuration_keeps_file_scope_checks(tmp_path: Path, ref: str) -> None:
    with pytest.raises(ValueError):
        baseline_data_description(repo(tmp_path), executable_data_plan(ref))


def test_data_handoff_keeps_coding_plan_hash_and_project_guards(tmp_path: Path) -> None:
    from app.harness.schema.experiment_contract import document_hash
    (tmp_path / "candidate.yaml").write_text("data_path: data/not-loaded.pth\n")
    upstream = executable_data_plan("candidate.yaml")
    code = {"schema": "code_spec.v1", "project": "local",
            "experiment_plan_sha256": document_hash(upstream["experiment_plan.approved.md"]),
            "execution_jobs": [{"name": "candidate", "config": {"command_id": "registered"}}]}
    upstream["code_spec.approved.md"] = dumps(code, "Authored command binding.")
    assert json.loads(baseline_data_description(repo(tmp_path), upstream))["sources"]
    code["experiment_plan_sha256"] = "0" * 64
    upstream["code_spec.approved.md"] = dumps(code, "Stale binding.")
    with pytest.raises(ValueError, match="experiment_plan_sha256"):
        baseline_data_description(repo(tmp_path), upstream)
    with pytest.raises(ValueError, match="项目"):
        baseline_data_description(repo(tmp_path), executable_data_plan("candidate.yaml", "other"))


@pytest.mark.parametrize("contents", ["data_path: 123", "data_path: a.pth\ndata:\n  path: b.pth"])
def test_ambiguous_or_invalid_dataset_fields_fail_explicitly(tmp_path: Path, contents: str) -> None:
    (tmp_path / "new.yaml").write_text(contents)
    with pytest.raises(ValueError):
        baseline_data_description(repo(tmp_path), executable_data_plan("new.yaml"))
