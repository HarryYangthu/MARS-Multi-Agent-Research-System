"""Helpers for turning a user's execution intent into a batch size."""
from __future__ import annotations

import re

_EN_COUNT: dict[str, int] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

_CN_DIGIT: dict[str, int] = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}

_COUNT_TOKEN = r"[0-9]+|one|two|three|four|five|six|seven|eight|nine|ten|[一二两三四五六七八九十]+"

_COUNT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        rf"(?:只|仅|先|帮我|请|麻烦)?\s*(?:跑|运行|执行|做|测试|仿真|启动)\s*"
        rf"(?P<count>{_COUNT_TOKEN})\s*(?:组|个|次|轮)\s*(?:实验|仿真|消融|对比)",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?:只|仅|先|帮我|请|麻烦)?\s*(?:跑|运行|执行|做|测试|仿真|启动)\s*"
        rf"(?P<count>{_COUNT_TOKEN})\s*(?:组|次|轮)(?=$|[，。,.；;\s])",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?P<count>{_COUNT_TOKEN})\s*(?:组|个|次|轮)\s*(?:实验|仿真)",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?:run|execute|start|launch)\s+(?P<count>{_COUNT_TOKEN})\s+"
        r"(?:experiment|experiments|simulation|simulations|run|runs)",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?P<count>{_COUNT_TOKEN})\s+"
        r"(?:experiment|experiments|simulation|simulations|run|runs)",
        re.IGNORECASE,
    ),
)

_SINGLE_COUNT_TERMS: tuple[str, ...] = (
    "一组实验",
    "一组仿真",
    "一个实验",
    "一次实验",
    "单组实验",
    "单次实验",
    "单个实验",
    "只跑一组",
    "只跑一个",
    "只做一组",
    "先跑一组",
    "帮我跑一组",
)

_SWEEP_TERMS: tuple[str, ...] = (
    "sweep",
    "grid",
    "ablation",
    "ablations",
    "matrix",
    "batch",
    "多组",
    "多实验",
    "多轮",
    "扫描",
    "消融",
    "网格",
    "矩阵",
    "批量",
    "参数搜索",
    "对比实验",
    "全部组合",
    "完整跑",
)


def _parse_chinese_count(token: str) -> int | None:
    if token == "十":
        return 10
    if "十" not in token:
        return _CN_DIGIT.get(token)
    left, _, right = token.partition("十")
    tens = 1 if left == "" else _CN_DIGIT.get(left, 0)
    ones = 0 if right == "" else _CN_DIGIT.get(right, 0)
    value = tens * 10 + ones
    return value if value > 0 else None


def parse_experiment_count_token(token: str) -> int | None:
    """Parse a small human-language count token used near experiment words."""
    cleaned = token.strip().lower()
    if cleaned.isdigit():
        value = int(cleaned)
    elif cleaned in _EN_COUNT:
        value = _EN_COUNT[cleaned]
    else:
        parsed = _parse_chinese_count(cleaned)
        if parsed is None:
            return None
        value = parsed
    return value if 0 < value <= 64 else None


def requested_experiment_count(text: str) -> int | None:
    """Return an explicit requested experiment count, if the text contains one.

    The patterns require experiment/run words around the number so unrelated
    domain numbers such as "16 channels" or "245.76 MHz" do not alter the plan.
    """
    if not text.strip():
        return None
    if any(term in text for term in _SINGLE_COUNT_TERMS):
        return 1
    for pattern in _COUNT_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        count = parse_experiment_count_token(match.group("count"))
        if count is not None:
            return count
    return None


def wants_execution_sweep(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in _SWEEP_TERMS)


def default_experiment_count(text: str, *, sweep_count: int = 16) -> int:
    explicit = requested_experiment_count(text)
    if explicit is not None:
        return explicit
    return sweep_count if wants_execution_sweep(text) else 1
