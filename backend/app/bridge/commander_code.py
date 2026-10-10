"""Commander access to project-owned read tools through the shared registry."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.harness.tools.project_repo import load_project_repo
from app.harness.tools.registry import ToolContext as HarnessContext, get_registry
from app.settings import repo_root

if TYPE_CHECKING:
    from app.bridge.commander_tools import ToolContext


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
    context = HarnessContext(run_id=ctx.session.conv_id, project=ctx.session.project, agent="commander",
        session_id=ctx.session.conv_id,
        extra={"run_root": str(repo_root() / "conversations" / ctx.session.conv_id)})
    result = await get_registry().dispatch(name, args, context)
    return {"ok": result.ok, "output": result.output, "error": result.error,
            "evidence_refs": result.evidence_refs, "status": result.status}
