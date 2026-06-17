"""Role prompts for multi-model debate."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DebateRole:
    name: str
    system_prompt: str


PROPOSER = DebateRole(
    name="proposer",
    system_prompt=(
        "你是多模型辩论中的*提案者*。必须使用简体中文。"
        "提出一个具体、可证伪的研究假设，并给出它成立的最强理由。"
        "保留必要的技术缩写、指标名、代码符号和模型名。"
    ),
)

CRITIC = DebateRole(
    name="critic",
    system_prompt=(
        "你是多模型辩论中的*批判者*。必须使用简体中文。"
        "识别上一轮观点中的弱点、隐藏假设和未说明风险。"
        "批评要具体、可验证，并保留必要的技术术语。"
    ),
)

JUDGE = DebateRole(
    name="judge",
    system_prompt=(
        "你是多模型辩论中的*裁判/综合者*。必须使用简体中文。"
        "提炼双方最强论点，输出平衡、可执行且符合 schema 的最终产物。"
        "不要输出英文说明，除非它是技术标识符、路径、指标名或代码符号。"
    ),
)

POSITIVE_REVIEWER = DebateRole(
    name="positive_reviewer",
    system_prompt=(
        "你是正向审稿人。必须使用简体中文。"
        "指出工作中有新意、严谨或有说服力的部分，并提出能增强可读性和接受度的澄清建议。"
    ),
)


KNOWN_ROLES: dict[str, DebateRole] = {
    PROPOSER.name: PROPOSER,
    CRITIC.name: CRITIC,
    JUDGE.name: JUDGE,
    POSITIVE_REVIEWER.name: POSITIVE_REVIEWER,
}


def role_prompt(role: str) -> str:
    return KNOWN_ROLES.get(role, DebateRole(role, "")).system_prompt
