from app.bridge.commander_session import ChatMessage, CommanderSession


def test_research_summary_uses_first_user_goal_and_normalizes_whitespace() -> None:
    session = CommanderSession(conv_id="summary", project="research")
    session.add(ChatMessage(role="assistant", content="What do you want to study?"))
    session.add(ChatMessage(role="user", content="Optimize\n  baseline\taccuracy"))
    session.add(ChatMessage(role="user", content="Continue"))
    assert session.to_meta()["summary"] == "Optimize baseline accuracy"


def test_research_summary_empty_or_long_goal() -> None:
    session = CommanderSession(conv_id="summary", project="research")
    assert session.to_meta()["summary"] == ""
    session.add(ChatMessage(role="user", content="长" * 100))
    assert session.to_meta()["summary"] == "长" * 80


def test_summary_prefers_research_question_over_launch_instructions() -> None:
    session = CommanderSession(conv_id="summary", project="research")
    session.add(ChatMessage(role="user", content="请启动完整流程。研究问题：降低学习率能否改善残差？\n数据：本地资料"))
    assert session.to_meta()["summary"] == "降低学习率能否改善残差？"
