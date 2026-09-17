from pathlib import Path

import pytest

from app.agents.base import ContextPack, RunRequest
from app.agents.coding.opencode_adapter import OpenCodeAdapter
from app.harness.llm.accounting import ResourceBudgetError, run_resource_scope
from app.harness.agent_loop.context import pack_context


def test_compatibility_messages_preserve_complete_pinned_evidence() -> None:
    source = "研究约束。" * 1200 + "FINAL_MEASUREMENT=31.110267053009647"
    context = ContextPack(system="Role instructions", project="Isolated project",
                          task="Use only supplied evidence", upstream={"execution": source})
    messages = context.to_messages(agent_name="writing", output_schema="report.v1")
    assert any(source in message.content for message in messages)
    with pytest.raises(ValueError, match="budget"):
        pack_context(messages, [], "", "", budget=1000, observation_chars=100)


@pytest.mark.asyncio
async def test_external_model_process_is_rejected_before_workspace_changes(tmp_path: Path) -> None:
    request = RunRequest(project="pimc", user_request="Not an executed coding task",
                         extra={"run_root": str(tmp_path / "run")})
    context = ContextPack(system="constraints", project="pimc", task=request.user_request)
    with run_resource_scope(tmp_path / "run"):
        with pytest.raises(ResourceBudgetError, match="no enforceable shared model budget"):
            await OpenCodeAdapter().run(request, context)
    assert not (tmp_path / "run").exists()
