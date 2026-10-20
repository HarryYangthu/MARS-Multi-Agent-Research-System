"""Safe, actionable conversation errors, without provider payloads or secrets."""
from app.harness.context.runtime_pack import ContextBudgetExceeded
from app.harness.llm.accounting import ResourceBudgetError, ResourceReconciliationRequired


class CommanderDecisionError(ValueError):
    """The model did not return an unambiguous executable decision."""


class CommanderModelResponseError(RuntimeError):
    """A model response cannot drive a turn; carries no upstream secrets."""


def require_native_decision(result: dict[str, object]) -> None:
    if result.get('native_status') == 'unknown':
        raise CommanderModelResponseError(
            '模型没有返回可用的正文或工具调用，本次未执行新操作。请在设置中测试模型连接，并确认接口支持工具调用后重试。')


def conversation_failure(exc: Exception) -> tuple[str, str] | None:
    from app.harness.llm.provider_base import LLMCompletionError
    if isinstance(exc, LLMCompletionError):
        if exc.reason.get("code") == "empty_final_content":
            return ('model_empty_response', '模型接口没有返回最终正文或工具调用。请检查公司网关是否只返回思考字段或截断了最终回复；请求和历史记录已保留。')
        return ('model_output_truncated', '模型回复被截断，尚未得到完整执行指令。请核对模型输出长度与网关限制后重试；历史任务和结果已保留。')
    if isinstance(exc, CommanderModelResponseError):
        return ('model_response_unavailable', str(exc))
    if ((isinstance(exc, ValueError) and 'provider requires API key' in str(exc))
            or (isinstance(exc, RuntimeError) and str(exc).startswith('provider ') and 'is not configured for' in str(exc))):
        return ('model_credentials_missing', '尚未配置模型 API 密钥。历史对话可以直接查看；新研究请先在设置中配置并测试模型连接。')
    if isinstance(exc, ValueError) and ('native tool streaming' in str(exc) or 'upstream completion envelope' in str(exc)):
        return ('model_protocol_incompatible', '当前模型接口未提供兼容的回复或工具调用。请在设置中测试连接，核对接口地址与模型名称。历史任务和结果已保留。')
    status = getattr(exc, 'status_code', None)
    if status in {401, 403}:
        return ('model_authentication_failed', '模型接口拒绝认证或访问。请检查 API 密钥、接口地址和公司内网访问权限；历史对话不受影响。')
    if status == 429:
        return ('model_rate_limited', '模型接口限流或额度不足。请在模型服务端核对额度后重试；历史对话和任务已保留。')
    if isinstance(status, int) and 500 <= status <= 599:
        return ('model_service_unavailable', '模型服务暂时不可用。请在设置中测试连接后重试；历史对话和任务已保留。')
    if type(exc).__name__ in {'APIConnectionError', 'ConnectError', 'ConnectTimeout', 'ReadTimeout', 'APITimeoutError'}:
        return ('model_connection_failed', '无法连接模型接口或请求超时。请核对公司内网网络、接口地址和代理设置；历史对话和任务已保留。')
    if isinstance(exc, CommanderDecisionError):
        return ('invalid_commander_decision', '当前回复没有可执行的新操作。请求、对话和已有任务已保留，已有实验不会因此被停止。')
    if isinstance(exc, ContextBudgetExceeded):
        return ('context_budget_exceeded', '已尝试压缩，但必须保留的任务要求、约束或当前内容仍超出上下文窗口。请拆分任务，或核对模型窗口配置后继续；累计 Token 仅统计，本次未发送模型请求。')
    if isinstance(exc, ResourceReconciliationRequired):
        return ('resource_reconciliation_required', '存在结果未确认的模型请求，需要先核对执行记录；系统没有自动重试。')
    if isinstance(exc, ResourceBudgetError):
        if 'elapsed-time' in str(exc):
            return ('time_budget_exhausted', '总控累计模型调用时长已达到上限。请新建对话，或调整预算并核对原记录后继续；刷新不会重置额度。')
        return ('resource_budget_blocked', '模型资源预算或账本校验未通过。请检查请求次数、费用额度和预算配置；累计 Token 仅统计，系统没有自动重试。')
    if isinstance(exc, TimeoutError):
        return ('model_timeout', '模型调用超时，已保留对话和用量记录。请重新读取对话核对状态；系统没有自动重试。')
    return None
