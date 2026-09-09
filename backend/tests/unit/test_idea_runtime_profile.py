"""Pure configuration and real archive guards; no model/tool/service substitutes."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError
import yaml

from app.agents.base import RunRequest
from app.agents.idea.agent import IdeaAgent
from app.agents.idea.runtime_profile import (
    PROFILE_FILE, SNAPSHOT_FILE, ResolvedIdeaProfile, _Definition, bind_profile_snapshot,
    public_agent_configuration, resolve_idea_profile,
)
from app.agents.idea.service_agent import ServiceIdeaAgent
from app.harness.agent_loop import AgentLoopPolicy
from app.harness.agent_loop.trace import canonical, digest
from app.harness.llm.model_registry import AgentConfig, get_agent_config
from app.settings import Settings, repo_root


@pytest.fixture
def profile() -> ResolvedIdeaProfile:
    selected = resolve_idea_profile("experimental_research_pro_per_insight_v1")
    assert selected is not None
    return selected


def test_profile_selection_is_local_explicit_and_baseline_is_default() -> None:
    settings = Settings(_env_file=None, mars_idea_runtime_profile="baseline")  # type: ignore[call-arg]
    assert settings.mars_idea_runtime_profile == "baseline"
    assert Settings.model_fields["mars_idea_runtime_profile"].default == "baseline"
    assert resolve_idea_profile("baseline") is None
    for invalid in ("", "../agents.yaml", "/tmp/profile.yaml", "deepseek-v4-pro", "https://host/profile"):
        with pytest.raises(ValueError, match="unknown Idea runtime profile"):
            resolve_idea_profile(invalid)
        with pytest.raises(ValidationError):
            Settings(_env_file=None, mars_idea_runtime_profile=invalid)  # type: ignore[call-arg, arg-type]


def test_resolution_preserves_base_configuration_files_environment_and_product_tools() -> None:
    lead, child = get_agent_config("idea"), get_agent_config("idea_research")
    original = canonical({"lead": asdict(lead), "child": asdict(child)})
    paths = [repo_root() / "configs/agents.yaml", repo_root() / "configs/tools.yaml", repo_root() / PROFILE_FILE]
    before = {path: path.read_bytes() for path in paths}
    environment = dict(os.environ)
    selected = resolve_idea_profile("experimental_research_pro_per_insight_v1")
    assert selected is not None
    assert selected.lead.tools == lead.tools and selected.child.tools == child.tools
    assert {"code.repo_reader", "search.local_docs", "knowledge.baseline_match"} <= set(selected.lead.tools)
    assert {"search.local_docs", "knowledge.baseline_match", "search.web_search"} <= set(selected.child.tools)
    assert selected.lead.raw["research"]["source_downloads"] == lead.raw["research"]["source_downloads"]
    assert selected.child.raw["research"]["excerpt_context_chars"] == child.raw["research"]["excerpt_context_chars"]
    assert canonical({"lead": asdict(lead), "child": asdict(child)}) == original
    assert all(path.read_bytes() == value for path, value in before.items())
    assert dict(os.environ) == environment


def test_profile_adopts_declared_variant_models_protocol_and_budgets(profile: ResolvedIdeaProfile) -> None:
    scenario = yaml.safe_load((repo_root() / "configs/evaluation/idea_research_per_insight_real.yaml").read_text())
    snapshot = profile.snapshot()
    for role, model_key, loop_key in (("lead", "model", "loop"), ("child", "child_model", "child_loop")):
        effective = snapshot["configuration"][role]
        for key, value in scenario[model_key].items():
            assert effective["model"][key] == value
        assert effective["loop"] == asdict(AgentLoopPolicy.from_mapping(scenario[loop_key]))
        assert effective["model"]["api_key_env"] == "DEEPSEEK_API_KEY"
        assert effective["model"]["base_url_env"] == ""
        assert effective["model"]["base_url"] == "https://api.deepseek.com/v1"
    assert profile.lead.raw["research"]["max_delegations"] == scenario["research"]["max_delegations"] == 3
    assert profile.child.raw["research"]["review_mode"] == scenario["child_research"]["review_mode"]
    assert snapshot["configuration_sha256"] == digest(snapshot["configuration"])
    assert snapshot["source"]["sha256"] == hashlib.sha256((repo_root() / PROFILE_FILE).read_bytes()).hexdigest()
    assert snapshot["status"] == "experimental"
    assert snapshot["validated"] is False
    assert all(key not in snapshot for key in ("candidate", "counts", "history", "usage", "pending", "accept"))
    assert "question" not in snapshot["configuration"] and "requirements" not in snapshot["configuration"]
    # Provider author retries remain explicit; review-plan runtime independently
    # forces zero retries and reserves every unit + whole within these budgets.
    assert snapshot["configuration"]["lead"]["loop"]["max_tool_steps"] == 5
    assert snapshot["configuration"]["child"]["loop"]["max_tool_steps"] == 10


def test_raw_model_and_typed_configuration_are_consistent(profile: ResolvedIdeaProfile) -> None:
    for config in (profile.lead, profile.child):
        model = config.raw["model"]
        assert model["model"] == config.model_name
        assert model["thinking"]["enabled"] == config.thinking_enabled
        assert model["reasoning_effort"] == config.reasoning_effort
        assert model["retry"]["max_retries"] == config.max_retries
        assert not config.debate_enabled and not config.raw["debate"]["enabled"]


def test_v2_only_enables_author_empty_completion_repair_with_original_budgets(profile: ResolvedIdeaProfile) -> None:
    selected = resolve_idea_profile("experimental_research_pro_per_insight_v2")
    assert selected is not None
    settings = Settings(_env_file=None, mars_idea_runtime_profile="experimental_research_pro_per_insight_v2")  # type: ignore[call-arg]
    assert settings.mars_idea_runtime_profile == selected.profile_id
    old, new = profile.snapshot(), selected.snapshot()
    for role in ("lead", "child"):
        expected = deepcopy(old["configuration"][role])
        assert expected["loop"]["author_empty_completion_repair_enabled"] is False
        expected["loop"]["author_empty_completion_repair_enabled"] = True
        assert new["configuration"][role] == expected
    assert old["configuration_sha256"] != new["configuration_sha256"]
    assert new["status"] == "experimental" and new["validated"] is False


def test_v1_receipt_cannot_silently_adopt_v2_repair(tmp_path: Path, profile: ResolvedIdeaProfile) -> None:
    bind_profile_snapshot(tmp_path, profile)
    path = tmp_path / SNAPSHOT_FILE
    before = path.read_bytes()
    selected = resolve_idea_profile("experimental_research_pro_per_insight_v2")
    assert selected is not None
    with pytest.raises(ValueError, match="configuration"):
        bind_profile_snapshot(tmp_path, selected, resume=True)
    assert path.read_bytes() == before


def test_catalog_contract_cannot_include_question_tools_or_untrusted_endpoint() -> None:
    definition = yaml.safe_load((repo_root() / PROFILE_FILE).read_text())["profiles"]["experimental_research_pro_per_insight_v1"]
    for section, key, value in ((None, "question", "injected answer"), ("lead", "tools", [])):
        modified = deepcopy(definition)
        (modified if section is None else modified[section])[key] = value
        with pytest.raises(ValidationError):
            _Definition.model_validate(modified)
    modified = deepcopy(definition)
    modified["child"]["model"]["base_url"] = "https://credential:secret@example.invalid/api?key=secret"
    with pytest.raises(ValidationError):
        _Definition.model_validate(modified)


def test_snapshot_records_no_arbitrary_raw_values(profile: ResolvedIdeaProfile) -> None:
    config = replace(profile.child, raw={**profile.child.raw, "api_key": "not-a-real-credential", "private_notes": "private"})
    public = canonical(public_agent_configuration(config))
    assert "not-a-real-credential" not in public and "private_notes" not in public
    assert '"api_key"' not in public


def test_binding_is_idempotent_and_never_changes_execution_files(tmp_path: Path, profile: ResolvedIdeaProfile) -> None:
    # Pure configuration receipt. There is no purported model output/checkpoint.
    snapshot = bind_profile_snapshot(tmp_path, profile)
    path = tmp_path / SNAPSHOT_FILE
    first, modified = path.read_bytes(), path.stat().st_mtime_ns
    assert bind_profile_snapshot(tmp_path, profile, resume=True) == snapshot
    assert path.read_bytes() == first and path.stat().st_mtime_ns == modified
    assert not (tmp_path / "agent_traces").exists()
    assert not (tmp_path / "idea").exists()
    with pytest.raises(ValueError, match="baseline cannot"):
        bind_profile_snapshot(tmp_path, None, resume=True)
    assert path.read_bytes() == first


def test_missing_receipt_never_authorizes_resume(tmp_path: Path, profile: ResolvedIdeaProfile) -> None:
    with pytest.raises(ValueError, match="existing run"):
        bind_profile_snapshot(tmp_path, profile, resume=True)
    assert not (tmp_path / SNAPSHOT_FILE).exists()
    assert bind_profile_snapshot(tmp_path, None, resume=True) is None
    assert not (tmp_path / "input").exists()


def test_changed_profile_or_child_cannot_reuse_prior_receipt(tmp_path: Path, profile: ResolvedIdeaProfile) -> None:
    bind_profile_snapshot(tmp_path, profile)
    original = (tmp_path / SNAPSHOT_FILE).read_bytes()
    changed_child = replace(profile.child, temperature=0.2)
    configuration = {"lead": public_agent_configuration(profile.lead), "child": public_agent_configuration(changed_child)}
    changed = replace(profile, child=changed_child, configuration_json=canonical(configuration))
    for alternative in (changed, replace(profile, profile_id="different-profile"), replace(profile, source_sha256="0" * 64)):
        with pytest.raises(ValueError, match="differs"):
            bind_profile_snapshot(tmp_path, alternative, resume=True)
    assert (tmp_path / SNAPSHOT_FILE).read_bytes() == original


@pytest.mark.parametrize("content", ["{", "null", '{"schema":"first","schema":"second"}', '{"configuration_sha256":"unverified"}'])
def test_partial_or_modified_receipt_is_not_repaired(tmp_path: Path, profile: ResolvedIdeaProfile, content: str) -> None:
    path = tmp_path / SNAPSHOT_FILE
    path.parent.mkdir()
    path.write_text(content)
    with pytest.raises(ValueError):
        bind_profile_snapshot(tmp_path, profile)
    assert path.read_text() == content


def test_receipt_symlink_is_never_followed(tmp_path: Path, profile: ResolvedIdeaProfile) -> None:
    external = tmp_path / "original.json"
    external.write_text("Caller-owned file; not an execution artifact.")
    path = tmp_path / SNAPSHOT_FILE
    path.parent.mkdir()
    path.symlink_to(external)
    with pytest.raises(ValueError, match="regular file"):
        bind_profile_snapshot(tmp_path, profile)
    assert external.read_text() == "Caller-owned file; not an execution artifact."


@pytest.mark.asyncio
async def test_baseline_service_context_matches_original_and_writes_no_profile(tmp_path: Path) -> None:
    original, service = IdeaAgent(), ServiceIdeaAgent()
    first = RunRequest(project="pimc", user_request="Use the supplied research context.", extra={"run_root": str(tmp_path)})
    second = deepcopy(first)
    a, b = await original.build_context(first), await service.build_context(second)
    assert a == b and original.config == service.config and original.loop_policy == service.loop_policy
    assert "idea_research_config" not in second.runtime
    assert not (tmp_path / SNAPSHOT_FILE).exists()


@pytest.mark.asyncio
async def test_service_pins_both_configs_and_keeps_project_context(tmp_path: Path, profile: ResolvedIdeaProfile) -> None:
    service = ServiceIdeaAgent(profile=profile)
    request = RunRequest(project="pimc", user_request="Use the supplied research context.", extra={"run_root": str(tmp_path)})
    context = await service.build_context(request)
    child = request.runtime["idea_research_config"]
    assert isinstance(child, AgentConfig)
    assert service.config == profile.lead and child == profile.child
    assert (repo_root() / "projects/pimc/AGENTS.md").read_text() in context.project
    assert context.metadata["context_sources"] == {"project_rules": True, "code_repositories": True}
    assert "idea_requirements" not in request.extra and "context_sources" not in request.extra
    assert context.metadata["idea_runtime_profile"]["status"] == "experimental"
    assert not (tmp_path / "agent_traces").exists()
    # Build the actual local registry/session, without dispatching any tool.
    registry = service.loop_registry(request, context)
    session = request.runtime["idea_research_session"]
    assert session.registry is registry and session.config is child
    assert session.max_delegations == 3 and session.require_review
    assert session.config.raw["research"]["review_mode"] == "per_insight_then_whole"
    assert not session.receipts and session.attempted == 0
    # Each request receives its own child configuration; a mutation cannot leak
    # into another run or the frozen startup pair.
    other = RunRequest(project="pimc", user_request="Another request.", extra={"run_root": str(tmp_path / "other")})
    await service.build_context(other)
    assert other.runtime["idea_research_config"] is not child
    child.raw["loop"]["max_model_calls"] = 99
    assert other.runtime["idea_research_config"].raw["loop"]["max_model_calls"] == 16
    with pytest.raises(ValueError, match="researcher differs"):
        await service.build_context(request)
    assert service.service_profile_snapshot == profile.snapshot()


@pytest.mark.asyncio
async def test_existing_child_override_is_rejected_before_context_or_models(tmp_path: Path, profile: ResolvedIdeaProfile) -> None:
    service = ServiceIdeaAgent(profile=profile)
    request = RunRequest(project="pimc", user_request="Context only.", extra={"run_root": str(tmp_path)},
                         runtime={"idea_research_config": get_agent_config("idea_research")})
    with pytest.raises(ValueError, match="researcher differs"):
        await service.build_context(request)
    assert not (tmp_path / "agent_traces").exists()


@pytest.mark.asyncio
async def test_profile_changes_are_rejected_before_draft(tmp_path: Path, profile: ResolvedIdeaProfile) -> None:
    service = ServiceIdeaAgent(profile=profile)
    request = RunRequest(project="pimc", user_request="Context only.", extra={"run_root": str(tmp_path)})
    context = await service.build_context(request)
    path = tmp_path / SNAPSHOT_FILE
    content = json.loads(path.read_text())
    content["profile_id"] = "changed-after-context"
    path.write_text(json.dumps(content))
    with pytest.raises(ValueError, match="differs"):
        await service.draft(request, context)
    assert not (tmp_path / "agent_traces").exists()


def test_mutation_of_resolved_raw_settings_is_rejected(profile: ResolvedIdeaProfile) -> None:
    altered = deepcopy(profile)
    altered.child.raw["loop"]["max_model_calls"] = 99
    with pytest.raises(ValueError, match="mutated"):
        altered.snapshot()


def test_actual_legacy_archive_cannot_be_adopted_or_modified(profile: ResolvedIdeaProfile) -> None:
    configured = os.environ.get("MARS_TEST_IDEA_PROFILE_ARCHIVE")
    if not configured:
        pytest.skip("requires a real completed research archive; no execution substitute")
    root = Path(configured).resolve()
    checkpoint = next(root.glob("agent_traces/idea/*/checkpoint.json"))
    actual = json.loads(checkpoint.read_text())
    assert actual["history"] and actual["counts"]["model_requests"] > 0
    paths = [path for path in root.rglob("*") if path.is_file()]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    assert not (root / SNAPSHOT_FILE).exists()
    with pytest.raises(ValueError, match="existing run"):
        bind_profile_snapshot(root, profile)
    with pytest.raises(ValueError, match="existing run"):
        bind_profile_snapshot(root, profile, resume=True)
    # Only configuration compatibility is checked for legacy baseline here;
    # no claim is made that its terminal loop can resume.
    assert bind_profile_snapshot(root, None, resume=True) is None
    assert {path for path in root.rglob("*") if path.is_file()} == set(before)
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == value for path, value in before.items())
