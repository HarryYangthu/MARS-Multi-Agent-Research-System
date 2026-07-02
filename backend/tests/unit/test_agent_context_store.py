"""Agent context store behavior."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.base import RunRequest
from app.agents.experiment.agent import ExperimentAgent
from app.harness.kb.stores import KBStores
from app.storage import agent_context_store as store


@pytest.fixture
def fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    def fake_repo_root() -> Path:
        return tmp_path

    monkeypatch.setattr(store, "repo_root", fake_repo_root)
    idea_root = tmp_path / "backend" / "app" / "agents" / "idea"
    (idea_root / "docs").mkdir(parents=True)
    (idea_root / "docs" / "principles.md").write_text("先调研，再提案。", encoding="utf-8")
    (idea_root / "agent.py").write_text("class IdeaAgent: ...\n", encoding="utf-8")
    for agent in ("experiment", "coding", "execution", "writing"):
        root = tmp_path / "backend" / "app" / "agents" / agent
        (root / "docs").mkdir(parents=True)
        (root / "docs" / "principles.md").write_text(
            f"# {agent}\n上下文配置。\n",
            encoding="utf-8",
        )
        (root / "agent.py").write_text(f"class {agent.title()}Agent: ...\n", encoding="utf-8")
    bridge_root = tmp_path / "backend" / "app" / "bridge"
    (bridge_root / "docs").mkdir(parents=True)
    (bridge_root / "docs" / "commander.md").write_text(
        "# Commander\n主 Agent 配置。\n",
        encoding="utf-8",
    )
    (bridge_root / "commander.py").write_text("class Commander: ...\n", encoding="utf-8")
    project_repo = tmp_path / "workspace" / "repos" / "pimc"
    project_repo.mkdir(parents=True)
    (project_repo / "README.md").write_text("# PIMC repo\n", encoding="utf-8")
    project_dir = tmp_path / "projects" / "pimc"
    project_dir.mkdir(parents=True)
    (project_dir / "repo_link.yaml").write_text(
        "project: pimc\n"
        "repo_mode: local_path\n"
        f"repo_path: {project_repo}\n"
        "read_only: false\n"
        "sync_strategy: live\n"
        "allowed_paths:\n"
        "  - libs/\n"
        "  - README.md\n"
        "protected_paths:\n"
        "  - libs/model.py\n"
        "ignore_patterns:\n"
        "  - data/\n",
        encoding="utf-8",
    )
    config_dir = tmp_path / "configs" / "agent_contexts"
    config_dir.mkdir(parents=True)
    (tmp_path / "configs" / "context.yaml").write_text(
        "context_engineering:\n"
        "  storage_layout:\n"
        "    long_term_root: configs/agent_contexts/{agent}.yaml\n"
        "    agent_root: runs/<run_id>/context/agents/{agent}/\n"
        "    manifests: runs/<run_id>/context/agents/{agent}/manifests/\n"
        "    raw: runs/<run_id>/context/agents/{agent}/raw/\n"
        "    packed: runs/<run_id>/context/agents/{agent}/packed/\n"
        "    memory: runs/<run_id>/context/agents/{agent}/memory/\n"
        "    research: runs/<run_id>/context/agents/{agent}/research/\n"
        "    debate: runs/<run_id>/context/agents/{agent}/debate/\n"
        "    tool_results: runs/<run_id>/context/agents/{agent}/tool_results/\n"
        "  agent_blueprints:\n"
        "    idea:\n"
        "      goal: Generate hypotheses.\n"
        "      packing_order: [system role, task]\n"
        "      items:\n"
        "        - order: 1\n"
        "          layer: 系统提示词\n"
        "          content: Idea system prompt\n"
        "          storage: [configs/agents.yaml]\n"
        "          required: 必装\n"
        "          risk: 低\n"
        "          strategy: 不修剪、不卸载\n"
        "          packing_position: system 最前\n",
        encoding="utf-8",
    )
    (config_dir / "idea.yaml").write_text(
        "agent: idea\n"
        "research_sites:\n"
        "  - id: arxiv\n"
        "    label: arXiv\n"
        "    url: https://arxiv.org\n"
        "    enabled: true\n"
        "    source: default\n",
        encoding="utf-8",
    )
    return tmp_path


def test_context_store_lists_runtime_and_editable_files(fake_repo: Path) -> None:
    del fake_repo
    files = store.list_agent_context_files("idea")
    by_path = {item.path: item for item in files}
    assert by_path["docs/principles.md"].editable
    assert by_path["docs/principles.md"].deletable
    assert not by_path["agent.py"].editable
    assert by_path["agent.py"].source == "runtime_code"


def test_context_store_supports_all_v2_agents(fake_repo: Path) -> None:
    del fake_repo
    for agent in ("commander", "idea", "experiment", "coding", "execution", "writing"):
        files = store.list_agent_context_files(agent)
        assert files, agent
        assert any(item.editable for item in files), agent
        assert any(item.source == "runtime_code" for item in files), agent


def test_agent_context_blueprint_exposes_storage_strategy(fake_repo: Path) -> None:
    del fake_repo
    blueprint = store.load_agent_context_blueprint("idea")
    assert blueprint.goal == "Generate hypotheses."
    assert blueprint.storage_layout.agent_root == "runs/<run_id>/context/agents/idea/"
    assert blueprint.storage_layout.manifests.endswith("/agents/idea/manifests/")
    assert blueprint.items[0].layer == "系统提示词"
    assert blueprint.items[0].storage == ("configs/agents.yaml",)
    assert blueprint.packing_order == ("system role", "task")


def test_code_repository_config_is_visible_to_all_agents(fake_repo: Path) -> None:
    for agent in ("idea", "experiment", "coding", "execution", "writing"):
        repos = store.load_agent_code_repositories(agent, project="pimc")
        assert len(repos) == 1
        assert repos[0].exists, agent
        assert repos[0].allowed_paths == ("libs/", "README.md")
        assert repos[0].protected_paths == ("libs/model.py",)


@pytest.mark.asyncio
async def test_non_idea_agent_context_includes_code_repository(fake_repo: Path) -> None:
    del fake_repo
    agent = ExperimentAgent()

    context = await agent.build_context(
        RunRequest(project="pimc", user_request="turn proposal into experiments")
    )

    assert "experiment_code_repositories" in context.upstream
    assert "README.md" in context.upstream["experiment_code_repositories"]
    assert context.metadata["experiment_code_repository_count"] == 1


def test_context_store_create_update_delete_uploaded_code(fake_repo: Path) -> None:
    del fake_repo
    created = store.create_agent_context_file(
        "idea",
        category="uploads/code",
        filename="probe.py",
        content="VALUE = 1\n",
    )
    assert created.path == "uploads/code/probe.py"
    assert created.editable

    updated = store.update_agent_context_file(
        "idea",
        path=created.path,
        content="VALUE = 2\n",
    )
    assert "VALUE = 2" in updated.content

    store.delete_agent_context_file("idea", path=created.path)
    paths = {item.path for item in store.list_agent_context_files("idea")}
    assert created.path not in paths


def test_research_sites_can_be_replaced(fake_repo: Path) -> None:
    del fake_repo
    saved = store.save_agent_research_sites(
        "idea",
        [
            {
                "id": "custom_lab",
                "label": "Custom Lab",
                "url": "https://example.com/papers",
                "enabled": True,
                "source": "custom",
            }
        ],
    )
    assert saved[0].id == "custom_lab"
    loaded = store.load_agent_research_sites("idea")
    assert len(loaded) == 1
    assert loaded[0].url == "https://example.com/papers"


def test_context_files_register_as_governed_memory(fake_repo: Path) -> None:
    stores = KBStores(fake_repo / "knowledge")
    store.create_agent_context_file(
        "idea",
        category="prompts",
        filename="router.md",
        content="Use schema-first router prompts.",
    )
    store.create_agent_context_file(
        "idea",
        category="examples",
        filename="good_case.md",
        content="A high-quality proposal example.",
    )
    store.create_agent_context_file(
        "idea",
        category="evals",
        filename="rubric.md",
        content="Rubric: provenance and novelty must pass.",
    )
    store.create_agent_context_file(
        "idea",
        category="uploads/docs",
        filename="paper.md",
        content="Uploaded paper note about sparse routing.",
    )

    written = store.register_agent_context_memory(
        "idea",
        project="pimc",
        stores=stores,
    )

    assert written == 4
    methodology = stores.zone("methodology").all(exclude_mock=False)
    run_archive = stores.zone("run_archive").all(exclude_mock=False)
    literature = stores.zone("literature").all(exclude_mock=False)
    assert {
        item.metadata["source_path"] for item in methodology
    } == {"agents/idea/prompts/router.md", "agents/idea/evals/rubric.md"}
    assert run_archive[0].metadata["memory_type"] == "episodic"
    assert run_archive[0].metadata["source_path"] == "agents/idea/examples/good_case.md"
    assert literature[0].metadata["memory_type"] == "semantic"
    assert literature[0].metadata["source_path"] == "agents/idea/uploads/docs/paper.md"


def test_context_file_sync_upserts_and_delete_cleans_memory(fake_repo: Path) -> None:
    stores = KBStores(fake_repo / "knowledge")
    created = store.create_agent_context_file(
        "idea",
        category="prompts",
        filename="router.md",
        content="First prompt policy.",
    )

    assert (
        store.sync_agent_context_file_to_memory(
            "idea",
            created,
            project="pimc",
            stores=stores,
        )
        == 1
    )
    updated = store.update_agent_context_file(
        "idea",
        path=created.path,
        content="Updated prompt policy.",
    )
    assert (
        store.sync_agent_context_file_to_memory(
            "idea",
            updated,
            project="pimc",
            stores=stores,
        )
        == 1
    )

    records = stores.zone("methodology").all(exclude_mock=False)
    assert len(records) == 1
    assert records[0].text == "Updated prompt policy."
    assert records[0].metadata["memory_type"] == "procedural"

    assert (
        store.delete_agent_context_memory(
            "idea",
            path=created.path,
            stores=stores,
        )
        == 1
    )
    assert stores.zone("methodology").all(exclude_mock=False) == []
