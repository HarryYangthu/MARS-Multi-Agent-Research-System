"""Read only hash-addressed material already admitted to this run."""
from pathlib import Path
from typing import Any

from app.harness.context.runtime_pack import read_material
from app.harness.context.runtime_policy import freeze_policy
from app.harness.runtime.project_scope import current_project_scope
from app.harness.tools.registry import ToolContext, ToolResult


async def read_material_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    try:
        root = Path(str(ctx.extra['run_root'])).resolve()
        policy = freeze_policy(root)
        if policy.get('version') != 3:
            raise ValueError('material reading requires a v3 run')
        ref, offset = str(args.get('ref', '')), args.get('char_offset', 0)
        if type(offset) is not int:
            raise ValueError('char_offset must be an integer')
        scope = current_project_scope(ctx.project, ctx.run_id)
        if scope is not None:
            scope.run_file(f'context/materials/{ref}.json', must_exist=True)
        output = read_material(root, ref, offset, int(policy['read_chars']))
        return ToolResult(ok=True, output=output, evidence_refs=[ref])
    except (KeyError, ValueError, OSError) as exc:
        return ToolResult(ok=False, error=str(exc))
