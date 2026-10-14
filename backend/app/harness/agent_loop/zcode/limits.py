"""Local invocation limits; durable run accounting remains a separate boundary."""
from __future__ import annotations

from dataclasses import dataclass

from app.harness.agent_loop.policy import AgentLoopPolicy
from app.harness.agent_loop.zcode.config import ZCodeConfig


@dataclass(frozen=True)
class ZCodeInvocationLimits:
    model_calls: int | None
    tool_calls: int | None
    validation_repairs: int | None
    active_seconds: int | None

    def allows_model_call(self, used: int) -> bool:
        return self.model_calls is None or used < self.model_calls

    def allows_tool_call(self, used: int) -> bool:
        return self.tool_calls is None or used < self.tool_calls

    def allows_validation_repairs(self, used: int) -> bool:
        return self.validation_repairs is None or used <= self.validation_repairs

    def remaining_active_seconds(self, previous: float, current: float = 0.0) -> float | None:
        return None if self.active_seconds is None else max(0.0, self.active_seconds - previous - current)


def invocation_limits(config: ZCodeConfig, policy: AgentLoopPolicy) -> ZCodeInvocationLimits:
    """Completion-driven work is bounded by host run resources, not turn counts.

    Configuration is not rewritten. Request/RPC timeouts, ToolRegistry gates,
    candidate validation and RunModelBudget continue to enforce their contracts.
    """
    if policy.completion_driven:
        return ZCodeInvocationLimits(None, None, None, None)
    model_calls = (config.max_model_calls if policy.max_model_calls is None
                   else min(config.max_model_calls, policy.max_model_calls))
    return ZCodeInvocationLimits(model_calls, min(config.max_tool_calls, policy.max_tool_steps),
                                 config.max_validation_repairs, config.active_timeout_seconds)
