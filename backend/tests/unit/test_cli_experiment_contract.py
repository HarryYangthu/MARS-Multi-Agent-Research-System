"""Host schedule and reviewer contract checks, without model/tool substitutes."""
from __future__ import annotations

from copy import deepcopy
import json

from jsonschema import Draft202012Validator
import pytest

from app.agents.base import ContextPack, RunRequest
from app.agents.research_cli import ResearchExperimentAgent


def inputs(rounds: int = 1) -> tuple[ResearchExperimentAgent, RunRequest]:
    return ResearchExperimentAgent("deepseek-v4-flash", {}), RunRequest(
        project="schedule_contract", user_request="Human-authored contract input",
        upstream_artifacts={"frozen_protocol": json.dumps({"seed": 2026, "max_steps": 50,
            "data_sha256": "a" * 64, "learning_rate": .001, "baseline": {"model": {"channels": 16}}}),
            "goal": json.dumps({"rounds": rounds})})


def test_plan_has_exact_training_schedule_and_complete_protocol() -> None:
    agent, request = inputs()
    schema = agent.submission_schema(request)
    Draft202012Validator.check_schema(schema)
    metadata = {"schema": "experiment_plan.v1", "agent": "experiment", "project": request.project,
        "variables": {"independent": ["model"], "dependent": ["RES"]}, "metrics": {"primary": "RES"},
        "ablations": [{"name": "candidate", "config": {"rank": 4}}], "estimated_runs": 2,
        "scheduled_trials": ["baseline", "round_01"],
        "protocol_ack": json.loads(request.upstream_artifacts["frozen_protocol"]),
        "hypothesis": "Human-authored input for structure checks only",
        "future_ablations": ["Additional ranks remain unscheduled"]}
    validator = Draft202012Validator(schema)
    validator.validate(metadata)
    for key, value in (("estimated_runs", 4), ("scheduled_trials", ["baseline", "round_01", "rank2"]),
                       ("ablations", metadata["ablations"] * 3)):
        wrong = {**metadata, key: value}
        assert list(validator.iter_errors(wrong))
    wrong = deepcopy(metadata)
    del wrong["protocol_ack"]["data_sha256"]
    assert list(validator.iter_errors(wrong))


@pytest.mark.parametrize("rounds", [1, 2, 3])
def test_schedule_tracks_explicit_round_budget(rounds: int) -> None:
    agent, request = inputs(rounds)
    props = agent.submission_schema(request)["properties"]
    assert props["estimated_runs"]["const"] == rounds + 1
    assert props["scheduled_trials"]["const"] == ["baseline", *[f"round_{i:02d}" for i in range(1, rounds + 1)]]
    assert props["ablations"]["minItems"] == props["ablations"]["maxItems"] == rounds


def test_reviewer_gets_same_submission_contract_and_budget_limits() -> None:
    agent, request = inputs()
    context = ContextPack(system="Contract review", project=request.project, task=request.user_request,
                          upstream=request.upstream_artifacts)
    messages = agent.review_messages(request, context)
    actual = json.loads(messages[-1].content.split("\n", 1)[1])
    assert actual == agent.submission_schema(request)
    assert "never require additional runs" in agent.reflection_rubric()
    assert "mechanisms" in agent.reflection_rubric()
