from __future__ import annotations

from app.harness.execution_intent import (
    default_experiment_count,
    requested_experiment_count,
    wants_execution_sweep,
)


def test_chinese_single_experiment_intent() -> None:
    assert requested_experiment_count("帮我跑一组实验，看一下 loss") == 1
    assert default_experiment_count("先跑一组仿真") == 1


def test_numeric_experiment_count_intent() -> None:
    assert requested_experiment_count("跑 3 组消融对比") == 3
    assert requested_experiment_count("run two experiments") == 2


def test_domain_numbers_do_not_become_batch_size() -> None:
    text = "使用 16 通道数据，fs 245.76 MHz，检查静态 PIM 性能"
    assert requested_experiment_count(text) is None
    assert default_experiment_count(text) == 1


def test_sweep_defaults_to_full_grid_without_explicit_count() -> None:
    assert wants_execution_sweep("做一个 learning-rate sweep") is True
    assert default_experiment_count("做一个 learning-rate sweep") == 16
