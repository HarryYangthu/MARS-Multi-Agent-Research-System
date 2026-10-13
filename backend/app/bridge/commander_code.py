"""Commander access to project-owned read tools through the shared registry."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any
import hashlib
from pathlib import Path

from app.harness.tools.project_repo import ProjectRepo, load_project_repo, resolve_allowed_path
from app.harness.tools.registry import ToolContext as HarnessContext, get_registry
from app.bridge.commander_session import get_session_store
from app.settings import repo_root

if TYPE_CHECKING:
    from app.bridge.commander_tools import ToolContext
    from app.bridge.commander_session import CommanderSession


def routing_repository_check(args: dict[str, Any], session: CommanderSession) -> dict[str, Any]:
    """Require current source evidence before routing a connected code task.

    This checks provenance, not task semantics. The model still selects the
    necessary stage; non-code tasks can explicitly explain why reads do not apply.
    """
    scope = args.get('repository_scope', 'required')
    reason = str(args.get('routing_reason', '')).strip()
    if scope not in {'required', 'not_applicable'}:
        return {'ok': False, 'error': 'repository_scope must be required or not_applicable'}
    if scope == 'not_applicable':
        if not reason:
            return {'ok': False, 'error': 'Explain why this task is unrelated to the repository in routing_reason.'}
        return {'ok': True, 'repository_scope': scope, 'reason': reason, 'evidence': []}
    if args.get('entrypoint') == 'writing':
        return {'ok': True, 'repository_scope': 'not_applicable', 'reason': 'Summarize existing artifacts.', 'evidence': []}
    try:
        repo = load_project_repo(session.project)
    except ValueError as exc:
        return {'ok': False, 'status': 'repository_unavailable',
                'error': f'代码仓绑定无法核验：{exc}。纯非代码任务可明确声明 repository_scope=not_applicable 并说明理由。'}
    evidence = routing_source_evidence(repo, session)
    if not evidence:
        return {'ok': False, 'status': 'repository_inspection_required',
                'error': '尚未核对本轮任务相关代码，任务未创建。先搜索并读取相关模型、配置或运行入口，再根据实际缺口选择阶段；已有模型仿真不等于研究新模型。非代码任务可使用 repository_scope=not_applicable，并在 routing_reason 说明理由。'}
    return {'ok': True, 'repository_scope': scope, 'reason': reason, 'evidence': evidence}


def routing_source_evidence(repo: ProjectRepo, session: CommanderSession) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    turn = session.active_turn_id or next((m.id for m in reversed(session.messages) if m.role == 'user'), None)
    for message in session.messages:
        if (turn is None or message.turn_id != turn or message.tool_name != 'code.repo_read_lines'
                or not message.tool_result or message.tool_result.get('ok') is not True):
            continue
        output = message.tool_result.get('output')
        if not isinstance(output, dict) or not output.get('content'):
            continue
        if Path(str(output.get('repo_root', ''))).resolve() != repo.root.resolve():
            continue
        try:
            path = resolve_allowed_path(repo, str(output.get('path', '')), require_exists=True, require_text=True)
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
        except (OSError, ValueError):
            continue
        if sha == output.get('sha256'):
            evidence.append({key: output[key] for key in ('path', 'sha256', 'line_start', 'line_end')})
    return evidence


def routing_conclusion_needs_source(session: CommanderSession, *, repo: ProjectRepo | None = None) -> bool:
    """A status receipt cannot validate a code-based claim about the old route."""
    turn = session.active_turn_id
    if turn is None:
        return False
    observations = {m.tool_name for m in session.context_messages()
                    if m.turn_id == turn and m.role == 'tool' and m.tool_result
                    and m.tool_result.get('ok') is True}
    if 'code.repo_search' not in observations or not observations.intersection({'get_run_status', 'run.status', 'run.recovery_status'}):
        return False
    return not (routing_source_evidence(repo, session) if repo is not None
                else routing_repository_check({}, session).get('evidence'))


def repository_summary(project: str) -> str:
    """Inject connection metadata only. Code is selected by later tool actions."""
    try:
        repo = load_project_repo(project)
        if not repo.root.is_dir():
            return "代码仓不可用，请检查项目绑定路径。"
        return f"代码仓: {repo.root}\n读取方式: 按任务浏览、搜索、读取片段；写权限: {'只读' if repo.read_only else '由编码阶段管理'}"
    except (OSError, ValueError) as exc:
        return f"代码仓连接不可用: {exc}"


async def execute_code_inspection(name: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    original_request = next((m.content for m in reversed(ctx.session.context_messages()) if m.role == 'user'), '')
    context = HarnessContext(run_id=ctx.session.conv_id, project=ctx.session.project, agent="commander",
        session_id=ctx.session.conv_id,
        extra={"run_root": str(get_session_store().session_directory(ctx.session)),
               "original_request": original_request})
    result = await get_registry().dispatch(name, args, context)
    return {"ok": result.ok, "output": result.output, "error": result.error,
            "evidence_refs": result.evidence_refs, "status": result.status}
