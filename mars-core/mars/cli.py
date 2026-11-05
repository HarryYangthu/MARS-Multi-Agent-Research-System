"""MARS core CLI: run / refine / implement / evaluate / resume / stop / status / kb."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import typer
from langgraph.types import Command

from mars import osutil
from mars.run import ROOT, Run
from mars.project import Scope, resolve_path

app = typer.Typer(add_completion=False, help="MARS 最小核心：Step1 提方案 + Step2 Evolve")


@app.callback()
def main(ctx: typer.Context,
         environment: str = typer.Option(None, "--environment", "--env", help="训练执行位置：local（本地）/ remote（远程 GPU）；放在子命令前"),
         project: Path = typer.Option(None, "--project", "--project-root", help="独立项目根目录；放在子命令前"),
         prepared: bool = typer.Option(False, "--prepared", hidden=True)) -> None:
    if environment is not None:
        if environment not in ("local", "remote"):
            raise typer.BadParameter("请选择 local 或 remote", param_hint="--environment")
    ctx.obj = {"project_root": project.expanduser().resolve() if project is not None else None,
               "environment": environment, "prepared": prepared}


def _given(ctx: typer.Context, path: Path) -> Path:
    """A path the user typed: relative to the project with --project, otherwise to the current directory."""
    return resolve_path(path, _scope(ctx).root if ctx.obj["project_root"] else Path.cwd())


def _scope(ctx: typer.Context) -> Scope:
    return Scope(ctx.obj["project_root"])


@app.command("inspect-project")
def inspect_project(ctx: typer.Context, project: Path = typer.Argument(None)) -> None:
    """只读检查独立项目，不初始化基线或运行模型。"""
    from mars import api
    selected = project or ctx.obj["project_root"]
    if selected is None:
        raise typer.BadParameter("请提供项目目录或 --project")
    typer.echo(json.dumps(api.inspect_project(selected), ensure_ascii=False, indent=1))


@app.command()
def environments(ctx: typer.Context) -> None:
    """列出本地与远程 GPU 两种执行环境（不连接服务器）。"""
    from mars.api import execution_environments
    typer.echo(json.dumps(execution_environments(project_root=ctx.obj["project_root"]), ensure_ascii=False, indent=1))


def _lock(run_dir: Path):
    """One process per run: hold an exclusive flock on runs/<id>/run.lock (contents: pid) until exit."""
    handle = (run_dir / "run.lock").open("a+", encoding="utf-8")
    try:
        osutil.lock(handle)
    except BlockingIOError:
        handle.seek(0)
        raise SystemExit(f"运行 {run_dir.name} 已有进程在执行（pid {handle.read().strip() or '?'}）") from None
    handle.truncate(0)
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def _invoke(run_dir: Path, payload: object, project_root: Path | None = None, environment: str | None = None) -> None:
    if os.name == "nt":  # keep Windows awake during multi-hour runs (bin/mars uses caffeinate on macOS)
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    from mars.graph import build
    lock = _lock(run_dir)
    connection = None
    try:
        Run.open(run_dir, project_root=project_root, environment=environment)
        graph, connection = build(run_dir)
        thread = run_dir.name  # each run has its own checkpoint file, so the run id is unique; no absolute path (folders move)
        config = {"configurable": {"thread_id": thread, "run_dir": str(run_dir), "project_root": str(project_root) if project_root else None, "environment": environment}, "recursion_limit": 500}
        result = graph.invoke(payload, config)
        pending = graph.get_state(config).tasks
        interrupts = [i.value for t in pending for i in (t.interrupts or [])]
        if interrupts:
            typer.echo("\n等待人工确认：\n" + interrupts[0]["ranking"])
            ids = [row.split()[0] for row in interrupts[0]["ranking"].splitlines() if row.strip()]
            typer.echo(f"\n确认后运行：mars resume {run_dir.name} --approve {','.join(ids[:2])}")
        else:
            typer.echo(f"\n结束：{result.get('outcome')}；报告：{run_dir / 'report.md'}")
    finally:
        if connection:
            connection.close()
        lock.close()


@app.command()
def run(ctx: typer.Context, task: Path, run_id: str = typer.Option("", help="默认使用时间戳"),
        auto_approve: bool = typer.Option(False, help="按评审排序自动进入 Step2（不等待人工确认）")) -> None:
    """从研究约定启动一次完整研究。"""
    run_id = run_id or time.strftime("%Y%m%d-%H%M%S") + "-" + task.stem
    run_dir = _scope(ctx).run(run_id)
    if (run_dir / "contract.yaml").exists() and not ctx.obj["prepared"]:
        raise SystemExit(f"运行 {run_id} 已存在；继续请用 mars resume {run_id}，新研究请换一个 run-id")
    Run.open(run_dir, _given(ctx, task), project_root=ctx.obj["project_root"], environment=ctx.obj["environment"])
    typer.echo(f"运行目录：{run_dir}")
    _invoke(run_dir, {"round": 0, "auto_approve": auto_approve}, ctx.obj["project_root"], ctx.obj["environment"])


@app.command()
def resume(ctx: typer.Context, run_id: str, approve: str = typer.Option("", help="逗号分隔的方案 id，用于回答人工确认")) -> None:
    """从检查点继续（中断、崩溃或人工确认之后）。"""
    run_dir = _scope(ctx).run(run_id)
    if not (run_dir / "contract.yaml").exists():
        raise SystemExit(f"找不到运行 {run_id}")
    if (run_dir / "implement").exists():  # an implement run has no graph: rerun it from its saved spec
        return implement(ctx, run_dir / "contract.yaml", None, run_id, None)
    from mars.api import _validate_approval
    _validate_approval(run_id, [a.strip() for a in approve.split(",") if a.strip()] if approve else None, ctx.obj["project_root"])
    payload = Command(resume=[a.strip() for a in approve.split(",") if a.strip()]) if approve else None
    _invoke(run_dir, payload, ctx.obj["project_root"], ctx.obj["environment"])


@app.command()
def refine(ctx: typer.Context, task: Path, proposal: Path = typer.Option(..., help="用户方案（Markdown/文本）"),
           run_id: str = typer.Option("", help="默认使用时间戳"),
           auto_approve: bool = typer.Option(False, help="不等待对结构化方案的人工确认")) -> None:
    """方案完善：把用户方案结构化后直接进入 Step2，在方案附近短预算进化（不回到 Step1）。"""
    run_id = run_id or time.strftime("%Y%m%d-%H%M%S") + "-refine"
    run_dir = _scope(ctx).run(run_id)
    if (run_dir / "contract.yaml").exists() and not ctx.obj["prepared"]:
        raise SystemExit(f"运行 {run_id} 已存在；继续请用 mars resume {run_id}")
    run = Run.open(run_dir, _given(ctx, task), project_root=ctx.obj["project_root"], environment=ctx.obj["environment"])
    run.path("step1/r0").mkdir(parents=True, exist_ok=True)
    run.path("step1/r0/user_proposal.md").write_text(_given(ctx, proposal).read_text(encoding="utf-8"), encoding="utf-8")
    typer.echo(f"运行目录：{run_dir}")
    _invoke(run_dir, {"round": 0, "auto_approve": auto_approve, "mode": "refine"}, ctx.obj["project_root"], ctx.obj["environment"])


@app.command()
def implement(ctx: typer.Context, task: Path, spec: Path = typer.Option(None, help="方案描述；续跑时可省略"),
              run_id: str = typer.Option("", help="默认使用时间戳"),
              parent: str = typer.Option(None, help="在哪个分支上实现；默认 mars/base，续跑沿用")) -> None:
    """仿真验证：按方案描述实现一次（编码、检查、修复、提交、资源账），不训练、不进化。"""
    from mars.step2 import Evolver
    run_id = run_id or time.strftime("%Y%m%d-%H%M%S") + "-implement"
    run_dir = _scope(ctx).run(run_id)
    run = Run.open(run_dir, _given(ctx, task), project_root=ctx.obj["project_root"], environment=ctx.obj["environment"])
    parent_file = run.path("implement/parent.txt")
    saved_parent = parent_file.read_text(encoding="utf-8") if parent_file.exists() else None
    if parent and saved_parent and parent != saved_parent:
        raise ValueError("实现基于的父版本已固定；更换父版本请使用新的 run_id")
    parent = saved_parent or parent or "mars/base"
    parent_file.write_text(parent, encoding="utf-8")
    target = run.path("implement/spec.md")
    if spec is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        text = _given(ctx, spec).read_text(encoding="utf-8")
        if target.exists() and target.read_text(encoding="utf-8") != text:
            raise ValueError("实现方案已固定；修改方案请使用新的 run_id")
        target.write_text(text, encoding="utf-8")
    if not target.exists():
        raise SystemExit("缺少方案描述：请用 --spec 指定")
    lock = _lock(run_dir)
    try:
        result = Evolver(run).implement_once(target.read_text(encoding="utf-8"), parent)
    finally:
        lock.close()
    typer.echo(json.dumps({k: result.get(k) for k in ("ok", "candidate_dir", "commit", "violations", "gates")},
                          ensure_ascii=False, indent=1))


@app.command()
def status(ctx: typer.Context, run_id: str) -> None:
    """显示运行事件与各路线进度。"""
    from mars import api
    state = api.status(run_id, project_root=ctx.obj["project_root"])
    typer.echo(f"阶段：{state['phase']}  Step1：{state['step1_round']}")
    for item in api.events(run_id, project_root=ctx.obj["project_root"])[-25:]:
        extra = {k: v for k, v in item.items() if k not in ("ts", "stage", "message")}
        typer.echo(f"{item['ts']} [{item['stage']}] {item['message']}" + (f"  {json.dumps(extra, ensure_ascii=False)}" if extra else ""))
    for route in state["routes"]:
        last = route["iterations"][-1] if route["iterations"] else {}
        typer.echo(f"路线 {route['route']}：{len(route['iterations'])} 轮，最近 {last.get('stopped_at') or '筛选'} {last.get('screen')}")
    if state.get("pending_approval"):
        typer.echo("待确认：" + "；".join(f"{r['id']}({r['score']})" for r in state["pending_approval"]))


@app.command()
def evaluate(ctx: typer.Context, candidate_dir: Path, dataset: str = typer.Option(..., help="项目声明的数据集名称"),
             steps: int = typer.Option(0, help="传给评测命令的预算；0 = 项目的完整预算"), seed: int = 2026,
             baseline: bool = typer.Option(False, help="评测基线（目录用 mars baseline-dir 的输出）"),
             set_: list[str] = typer.Option([], "--set", help="本次评测的配置覆盖，如 --set learning_rate=0.0005"),
             details: bool = typer.Option(False, help="候选同时输出项目的评测前检查")) -> None:
    """仿真验证 / 超参寻优：用项目的评测命令评测一个已提交的检出目录。"""
    import yaml
    from mars import api
    overrides = {k: yaml.safe_load(v) for k, v in (item.split("=", 1) for item in set_)}
    result = api.evaluate(candidate_dir, dataset, steps, seed, baseline=baseline, overrides=overrides or None, details=details,
                          environment=ctx.obj["environment"], project_root=ctx.obj["project_root"])
    keep = ("ok", "error", "dataset", "steps", "seed", "metrics", "missing_metrics", "plots", "checks",
            "elapsed_seconds", "executed_on", "run_dir")
    typer.echo(json.dumps({k: result[k] for k in keep if k in result}, ensure_ascii=False, indent=1))


@app.command("baseline-dir")
def baseline_dir(ctx: typer.Context) -> None:
    """输出基线代码检出目录（evaluate --baseline 用）。"""
    from mars import api
    typer.echo(api.baseline_dir(project_root=ctx.obj["project_root"]))


@app.command()
def stop(ctx: typer.Context, run_id: str, route: str = typer.Option("", help="只停一条路线（下一轮开始前生效）")) -> None:
    """停止运行（检查点保留，可 resume），或只停其中一条路线。"""
    from mars import api
    if route:
        api.stop_route(run_id, route, "命令行请求停止", project_root=ctx.obj["project_root"])
        typer.echo(f"已请求停止路线 {route}，将在下一轮开始前生效")
    else:
        typer.echo("已停止" if api.stop(run_id, project_root=ctx.obj["project_root"]) else "没有正在执行的进程")


@app.command()
def kb(ctx: typer.Context, query: str, k: int = 6) -> None:
    """检索项目知识库。"""
    from mars.knowledge import KnowledgeBase, load_history
    from mars.api import _kernel
    kernel = _kernel(ctx.obj["project_root"], ctx.obj["environment"])
    kernel.ensure_base()
    base = KnowledgeBase(kernel.knowledge_files(), load_history(kernel.scope.runs / "history.jsonl"))
    for chunk in base.search(query, k):
        typer.echo(f"{chunk['id']} [{chunk['kind']}] {Path(chunk['source']).name} · {chunk['title']}\n  {chunk['text'][:200]}\n")


if __name__ == "__main__":
    app()
