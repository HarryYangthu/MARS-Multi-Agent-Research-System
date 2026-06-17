from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from app.agents.base import Artifact, BaseAgent, ContextPack, RunRequest
from app.harness.llm.mock_provider import build_fake_metadata
from app.harness.llm.model_registry import AgentConfig
from app.harness.llm.provider_base import Completion
from app.harness.schema.frontmatter_parser import dumps as fm_dumps
from app.harness.schema.validator import validate_document
from app.harness.tools.registry import ToolContext, ToolResult


def _agent_config(*, tools: tuple[str, ...] = ()) -> AgentConfig:
    return AgentConfig(
        name="idea",
        enabled=True,
        output_schema="proposal.v1",
        model_provider="mock",
        model_name="mock-1",
        temperature=0.0,
        max_tokens=1024,
        debate_enabled=False,
        debate_rounds=1,
        debate_participants=(),
        tools=tools,
        raw={"loop": {"max_validation_repairs": 1, "max_tool_steps": 2}},
    )


def _valid_proposal(seed: str = "context-runtime") -> str:
    return fm_dumps(
        build_fake_metadata("proposal.v1", seed=seed),
        "# proposal\n\n用于 Context V1 runtime 回归测试。",
    )


class _RuntimeAgent(BaseAgent):
    name = "idea"
    output_schema = "proposal.v1"

    def __init__(self, *, tools: tuple[str, ...] = ()) -> None:
        super().__init__(agent_config=_agent_config(tools=tools))
        self.completions: list[str] = []

    async def draft(self, request: RunRequest, context: ContextPack) -> Artifact:
        text = _valid_proposal("draft")
        return self._artifact_from_completion(
            Completion(text=text, provider="test", model="test", is_mock=True)
        )

    async def _call_llm(
        self,
        messages: Sequence[object],
        *,
        debate_role: str | None = None,
    ) -> Completion:
        text = self.completions.pop(0) if self.completions else _valid_proposal("fallback")
        return Completion(
            text=text,
            provider="test",
            model="test",
            is_mock=True,
            debate_role=debate_role,
        )


class _FakeToolRegistry:
    async def dispatch(
        self,
        tool_name: str,
        args: dict[str, Any],
        ctx: ToolContext,
    ) -> ToolResult:
        return ToolResult(
            ok=True,
            output={
                "tool": tool_name,
                "args": args,
                "payload": "x" * 2000,
                "run_id": ctx.run_id,
            },
            metrics={"rows": 1},
        )


def _manifest_payloads(run_root: Path) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for path in sorted((run_root / "context").glob("context_manifest.v2.*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            payloads.append(raw)
    return payloads


@pytest.mark.asyncio
async def test_schema_repair_writes_precall_manifest(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    request = RunRequest(
        project="moe-pimc",
        user_request="repair manifest",
        extra={"run_id": "run-1", "run_root": str(run_root), "node_key": "idea"},
    )
    context = ContextPack(system="system", project="project", task="task")
    invalid = Artifact(
        text="---\nschema: proposal.v1\n---\n# missing required fields\n",
        schema_id="proposal.v1",
        metadata={"schema": "proposal.v1"},
        body="# missing required fields",
    )
    validation = validate_document(invalid.text, expected_schema="proposal.v1")
    assert not validation.valid
    agent = _RuntimeAgent()
    agent.completions.append(_valid_proposal("repair"))

    repaired = await agent.repair_after_validation_failure(
        request=request,
        context=context,
        artifact=invalid,
        validation=validation,
        attempt=1,
    )

    assert validate_document(repaired.text, expected_schema="proposal.v1").valid
    manifests = _manifest_payloads(run_root)
    assert any(item["purpose"] == "schema_repair_1" for item in manifests)
    repair = next(item for item in manifests if item["purpose"] == "schema_repair_1")
    assert repair["messages_preview"]
    assert any(segment["kind"] == "task" for segment in repair["segments"])


@pytest.mark.asyncio
async def test_tool_gather_writes_message_manifest_and_raw_ref(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_root = tmp_path / "run"
    request = RunRequest(
        project="moe-pimc",
        user_request="gather context",
        extra={"run_id": "run-2", "run_root": str(run_root), "node_key": "idea"},
    )
    context = ContextPack(system="system", project="project", task="task")
    agent = _RuntimeAgent(tools=("search.local_docs",))
    agent.max_tool_steps = 2
    agent.completions.extend(
        [
            '{"tool_calls": [{"tool": "search.local_docs", "args": {"query": "router"}}]}',
            '{"done": true}',
        ]
    )
    monkeypatch.setattr(agent, "_tools_enabled", lambda: True)

    import app.harness.tools.registry as registry_mod

    monkeypatch.setattr(registry_mod, "get_registry", lambda: _FakeToolRegistry())

    observations = await agent._gather_with_tools(request, context)

    assert observations
    assert observations[0]["raw_ref"]
    manifests = _manifest_payloads(run_root)
    assert any(item["purpose"] == "tool_gather_1" for item in manifests)
    assert any(item["diagnostics"].get("capture_mode") == "messages" for item in manifests)
    raw_files = list((run_root / "context" / "raw").glob("**/*.json"))
    assert raw_files


@pytest.mark.asyncio
async def test_tool_raw_externalize_can_be_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARS_CONTEXT_TOOL_RAW_EXTERNALIZE", "false")
    import app.settings as settings_mod

    settings_mod._settings = None
    run_root = tmp_path / "run"
    request = RunRequest(
        project="moe-pimc",
        user_request="gather without raw",
        extra={"run_id": "run-3", "run_root": str(run_root), "node_key": "idea"},
    )
    context = ContextPack(system="system", project="project", task="task")
    agent = _RuntimeAgent(tools=("search.local_docs",))
    agent.max_tool_steps = 1
    agent.completions.append(
        '{"tool_calls": [{"tool": "search.local_docs", "args": {"query": "router"}}]}'
    )
    monkeypatch.setattr(agent, "_tools_enabled", lambda: True)

    import app.harness.tools.registry as registry_mod

    monkeypatch.setattr(registry_mod, "get_registry", lambda: _FakeToolRegistry())

    observations = await agent._gather_with_tools(request, context)

    assert observations
    assert observations[0]["raw_ref"] is None
    assert not (run_root / "context" / "raw").exists()
    settings_mod._settings = None
