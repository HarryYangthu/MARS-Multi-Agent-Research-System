"""Pure error mapping contracts: no model or service substitutions."""
from app.bridge.commander_errors import conversation_failure
from app.harness.context.runtime_pack import ContextBudgetExceeded
from app.harness.llm.accounting import ResourceBudgetError, ResourceReconciliationRequired


def test_public_errors_are_actionable_and_do_not_leak_payloads() -> None:
    for error, code in [
        (ResourceBudgetError('run elapsed-time budget exhausted'), 'time_budget_exhausted'),
        (ResourceBudgetError('secret payload'), 'resource_budget_blocked'),
        (ResourceReconciliationRequired('secret request id'), 'resource_reconciliation_required'),
        (ContextBudgetExceeded('secret prompt'), 'context_budget_exceeded'),
        (TimeoutError('secret endpoint'), 'model_timeout'),
    ]:
        result = conversation_failure(error)
        assert result is not None and result[0] == code
        assert 'secret' not in result[1]
    assert conversation_failure(RuntimeError('unexpected')) is None
