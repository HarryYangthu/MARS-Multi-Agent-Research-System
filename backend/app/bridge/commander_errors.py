"""Safe, actionable conversation errors, without provider payloads or secrets."""
from app.harness.context.runtime_pack import ContextBudgetExceeded
from app.harness.llm.accounting import ResourceBudgetError, ResourceReconciliationRequired


def conversation_failure(exc: Exception) -> tuple[str, str] | None:
    if isinstance(exc, ContextBudgetExceeded):
        return ('context_budget_exceeded', '必需上下文超过输入预算。请缩小任务范围或调整上下文预算后继续；本次未发送模型请求。')
    if isinstance(exc, ResourceReconciliationRequired):
        return ('resource_reconciliation_required', '存在结果未确认的模型请求，需要先核对执行记录；系统没有自动重试。')
    if isinstance(exc, ResourceBudgetError):
        if 'elapsed-time' in str(exc):
            return ('time_budget_exhausted', '总控累计模型调用时长已达到上限。请新建对话，或调整预算并核对原记录后继续；刷新不会重置额度。')
        return ('resource_budget_blocked', '模型资源预算或账本校验未通过。请检查请求次数、Token、费用额度和预算配置；系统没有自动重试。')
    if isinstance(exc, TimeoutError):
        return ('model_timeout', '模型调用超时，已保留对话和用量记录。请重新读取对话核对状态；系统没有自动重试。')
    return None
