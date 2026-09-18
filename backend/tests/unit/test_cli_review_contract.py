"""Actual input/role contracts; no model or tool response substitutes."""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.agents.base import ContextPack, RunRequest
from app.agents.research_cli import ResearchCodingAgent, ResearchAnalysisAgent, candidate_errors
from app.harness.research_trial import candidate_factory_config


def test_factory_uses_exact_isolated_inputs() -> None:
    frozen: dict[str, Any] = {"seed": 1, "context": 144, "baseline": {"model": {"l1_groups": 16}}}
    config = candidate_factory_config(frozen)
    assert config == {"channels": 16, "context": 144, "baseline": frozen["baseline"]}
    config["baseline"]["model"]["l1_groups"] = 1
    assert frozen["baseline"]["model"]["l1_groups"] == 16
    assert "rank" not in config


@pytest.mark.parametrize("expression", ["config['rank']", "config.get('rank', 4)", "config['model']", "config['seed']"])
def test_factory_cannot_request_nonexistent_host_keys(expression: str) -> None:
    assert any("Factory config" in error for error in candidate_errors(f"def build_model(config):\n return {expression}\n"))


def test_candidate_can_set_its_architecture_without_adding_host_config() -> None:
    source = "def build_model(cfg):\n rank = 4\n return cfg['baseline']['model']['l1_groups'] + rank\n"
    assert not candidate_errors(source)  # Static lint only, not a claim of a runnable model.


@pytest.mark.parametrize("agent_type", [ResearchCodingAgent, ResearchAnalysisAgent])
def test_reviewer_does_not_receive_author_document_instructions(agent_type: type[ResearchCodingAgent]) -> None:
    subject = agent_type("deepseek-v4-flash", {})
    request = RunRequest(project="pimc", user_request="Fixed-budget comparison")
    context = ContextPack(system="AUTHOR_ONLY_INSTRUCTION: submit a new document", project="PRESERVE_BASELINE",
        task=request.user_request, upstream={"factory_config": '{"channels":16,"baseline":{},"context":144}',
                                            "source_code": "ACTUAL_SUPPLIED_SOURCE"})
    messages = subject.review_messages(request, context)
    text = "\n".join(m.content for m in messages)
    assert "AUTHOR_ONLY_INSTRUCTION" not in text
    assert "PRESERVE_BASELINE" in text and "ACTUAL_SUPPLIED_SOURCE" in text
    assert "Do not author or submit a document" in text
    assert json.loads(messages[-1].content.split("\n", 1)[1]) == subject.submission_schema(request)
