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
                  "ablations": [{"config": {"base_config": path.name, "config_path": "new.yaml"}}]},
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
