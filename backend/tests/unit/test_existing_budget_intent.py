"""Pure admission cases: rejected examples do not become training budgets."""
from __future__ import annotations

from typing import Any

import pytest

from app.bridge.commander_existing import ExistingIntentValidationError, validate_existing_user_intent
from app.bridge.commander_existing import user_requests_additional_changes


@pytest.mark.parametrize('text', [
    '不要增加模型代码，其余参数不变，训练2epoch', '不用新增层，跑原有模型',
    '模型不增加层；不要修改 batch_size=8', 'Do not add model code; run 2 epochs',
    "Don't change learning rate, run the current model", '无需删除或增加模型层',
])
def test_negated_source_changes_do_not_force_expensive_coding(text: str) -> None:
    assert not user_requests_additional_changes([text])


@pytest.mark.parametrize('text', [
    '不要增加层，但学习率减半，训练2epoch', '不修改优化器，增加模型层',
    'Do not change model, but halve learning rate', '学习率翻倍，训练2epoch',
    '不要只修改学习率，增加模型层',
])
def test_negation_never_swallows_a_separate_positive_change(text: str) -> None:
    assert user_requests_additional_changes([text])


def _resolved(unit: str = "epochs", count: int = 2, *, default: bool = False,
              seed: int = 2026) -> dict[str, Any]:
    return {"requested": {"budget_unit": unit, "max_iters": count, "seed": seed},
            "default_budget": {"budget_unit": unit, "max_iters": count} if default else {},
            "default_seed": 2026}


@pytest.mark.parametrize("text", [
    "训练2个epoch（不是2次优化器更新）",
    "训练2个epoch，不是2次优化器更新",
    "跑两个完整epoch，不要只跑两次更新",
    "不是2次优化器更新，而是2个epoch",
    "不要跑2steps，要跑2epochs",
    "2steps不对，改为2epochs",
    "2steps作废，改跑2epochs",
    "先跑20steps，这次改成2epochs",
    "2epochs，而非2steps，seed=2026",
    "Run 2 epochs, not 2 optimizer updates",
    "Run 2 epochs rather than 2 steps",
    "Run 2 epochs instead of 2 steps",
    "Not 2 optimizer updates but 2 epochs",
    "2 steps, actually run 2 epochs",
    "2epochs，但是不要改为3epochs，仍跑2epochs",
    "2steps而是2epochs",
    "不同配置分别训练2epochs",
    "不要2steps或3steps，而是2epochs",
    "Run 2 epochs, not 2 steps or 3 steps",
])
def test_budget_negation_and_explicit_correction_keep_actual_epoch_count(text: str) -> None:
    validate_existing_user_intent(_resolved(), [text])


@pytest.mark.parametrize("texts", [
    ["200steps", "这次改为2epochs，不是2steps"],
    ["2epochs", "不是2steps，继续"],
    ["不要2epochs，跑3epochs", "这次改回2epochs"],
])
def test_current_correction_and_negative_example_work_across_user_turns(texts: list[str]) -> None:
    validate_existing_user_intent(_resolved(), texts)


@pytest.mark.parametrize("text", [
    "2epochs还是2steps？",
    "2epochs或3epochs",
    "2epochs，3epochs也可以",
    "先跑2epochs，再跑2steps",
    "2steps，但是3epochs也行",
    "2steps but 3epochs would also be fine",
])
def test_multiple_affirmative_budgets_still_require_clarification(text: str) -> None:
    with pytest.raises(ExistingIntentValidationError) as caught:
        validate_existing_user_intent(_resolved(), [text])
    assert caught.value.structured_fields()["error_code"] == "budget_conflict"
    assert caught.value.requires_user_input and not caught.value.retryable


@pytest.mark.parametrize("texts", [
    ["不要2epochs"],
    ["2epochs不对"],
    ["2epochs", "不是2epochs"],
    ["not 2 epochs"],
    ["不要2epochs或3epochs"],
])
def test_rejected_budget_cannot_reappear_from_current_configuration(texts: list[str]) -> None:
    with pytest.raises(ExistingIntentValidationError) as caught:
        validate_existing_user_intent(_resolved(default=True), texts)
    assert caught.value.code == "budget_rejected"


def test_unambiguous_epoch_budget_reports_correct_value_without_unit_conversion() -> None:
    with pytest.raises(ExistingIntentValidationError) as caught:
        validate_existing_user_intent(_resolved(unit="steps"), ["训练2epochs，不是2次优化器更新"])
    assert caught.value.structured_fields() == {
        "error_code": "budget_mismatch", "retryable": False, "requires_user_input": False,
        "details": {"budgets": [{"budget_unit": "epochs", "max_iters": 2}],
                    "expected_budget": {"budget_unit": "epochs", "max_iters": 2}},
    }


def test_correction_of_count_does_not_convert_epoch_to_step() -> None:
    validate_existing_user_intent(_resolved("steps", 7), ["2epochs，改成7steps"])
    with pytest.raises(ExistingIntentValidationError) as caught:
        validate_existing_user_intent(_resolved("epochs", 7), ["2epochs，改成7steps"])
    assert caught.value.details["expected_budget"] == {"budget_unit": "steps", "max_iters": 7}


@pytest.mark.parametrize("text,code", [
    ("训练2epochs，seed=2027", "seed_mismatch"),
    ("训练2epochs，seed=2027或seed=2028", "seed_conflict"),
    ("训练2epochs", "seed_unconfirmed"),
])
def test_seed_must_still_be_explicit_or_match_current_configuration(text: str, code: str) -> None:
    with pytest.raises(ExistingIntentValidationError) as caught:
        validate_existing_user_intent(_resolved(seed=9), [text])
    assert caught.value.code == code
    assert not caught.value.retryable


def test_negative_seed_example_does_not_override_actual_configuration() -> None:
    validate_existing_user_intent(_resolved(), ["训练2epochs，不要seed=2027，seed=2026"])


def test_seed_correction_is_explicit_and_rejected_seed_cannot_return_as_default() -> None:
    validate_existing_user_intent(_resolved(), ["训练2epochs，seed=2027，改为seed=2026"])
    with pytest.raises(ExistingIntentValidationError) as caught:
        validate_existing_user_intent(_resolved(), ["训练2epochs，不要seed=2026"])
    assert caught.value.code == "seed_rejected"
    with pytest.raises(ExistingIntentValidationError) as corrected:
        validate_existing_user_intent(_resolved(), ["训练2epochs，seed=2026", "不是seed=2026"])
    assert corrected.value.code == "seed_rejected"


def test_zero_budget_cannot_be_suggested_as_an_automatic_model_correction() -> None:
    with pytest.raises(ExistingIntentValidationError) as caught:
        validate_existing_user_intent(_resolved(), ["训练0epochs"])
    assert caught.value.code == "budget_invalid" and caught.value.requires_user_input
