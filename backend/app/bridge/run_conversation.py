"""Open an existing research run in chat without dispatching any Agent work."""
from __future__ import annotations

from app.bridge.commander_session import ChatMessage, CommanderSession, CommanderSessionStore
from app.storage.run_store import RunStore


def open_run_conversation(
    run_id: str, *, project: str, sessions: CommanderSessionStore,
    runs: RunStore, experiment_id: str | None = None,
) -> CommanderSession:
    run = runs.get(run_id)
    if run is None or run.project != project:
        raise FileNotFoundError("research run not found in this project")
    scope = str(run.meta.get("experiment_id", "") or "")
    if experiment_id is not None and experiment_id != scope:
        raise ValueError("研究任务所属实验不匹配。")

    # The route has no awaits between lookup and creation; repeated opens in the
    # local service reuse the same persisted conversation. Resolve the live
    # object rather than mutating the disk copy returned by list().
    for candidate in sessions.list():
        if candidate.project == project and candidate.linked_run_id == run_id:
            current = sessions.get(candidate.conv_id)
            if current is not None and current.experiment_id == scope:
                return current

    session = sessions.create(project=project, experiment_id=scope)
    session.linked_run_id = run_id
    goal_path = run.root / "input" / "user_request.md"
    goal = goal_path.read_text(encoding="utf-8") if goal_path.is_file() else ""
    session.add(ChatMessage(
        role="system",
        content=(
            f"此对话已接入现有研究任务 {run_id}，项目 {project}，任务 {run.task}。\n"
            "打开对话只展示原任务及其产物，没有重新启动、批准或重试任务。\n"
            "继续处理前应读取原任务的当前状态与已有产物。\n"
            f"原始研究目标：\n{goal}"
        ),
    ))
    sessions.persist(session)
    return session
