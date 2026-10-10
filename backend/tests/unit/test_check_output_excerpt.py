"""Pure output formatting checks, without substituting tool execution."""
from app.harness.tools.code import _check_output_excerpt


def test_keeps_failure_before_warnings_and_final_summary() -> None:
    output = 'FAILED test_config: expected 0.0008, got 0.0002\n' + 'warning\n' * 1500 + '1 failed, 4 passed\n'
    excerpt = _check_output_excerpt(output)
    assert excerpt.startswith('FAILED test_config:')
    assert excerpt.endswith('1 failed, 4 passed\n')
    assert len(excerpt) == 4000
    assert 'truncated' in excerpt


def test_short_output_is_unchanged() -> None:
    assert _check_output_excerpt('5 passed\n') == '5 passed\n'
