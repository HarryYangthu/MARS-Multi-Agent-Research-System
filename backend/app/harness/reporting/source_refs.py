"""Validate report references without creating or repairing model evidence."""
from pathlib import Path

from app.harness.runtime.project_scope import safe_scope_path
from app.harness.schema.frontmatter_parser import parse


def report_source_catalogue(root: Path) -> dict[str, object]:
    """List admitted report references without reading or inventing results."""
    candidates = {'proposal': 'idea/idea_proposal.approved.md',
                  'plan': 'experiment/experiment_plan.approved.md',
                  'code': 'coding/code_spec.approved.md'}
    refs: dict[str, object] = {}
    absent: list[str] = []
    for key, name in candidates.items():
        try:
            present = safe_scope_path(root, name).is_file()
        except (ValueError, OSError):
            present = False
        if present:
            refs[key] = name
        else:
            absent.append(key)
    measured = ['execution/run_log.approved.md', 'execution/metrics.json',
                'execution/batch_summary.json', 'execution/loss_curves_16.png']
    measured.extend(path.relative_to(root).as_posix() for path in
        sorted((root / 'execution/local_commands').glob('*/*/execution_receipt.json')))
    runs: list[str] = []
    for name in measured:
        try:
            if safe_scope_path(root, name).is_file():
                runs.append(name)
        except (ValueError, OSError):
            continue
    refs['runs'] = runs
    return {'chain_refs': refs, 'absent_stages': absent,
        'instruction': '以上是当前研究实际存在的完整文件路径，不是内容或实验结论。chain_refs 可直接引用；'
            '未运行的 proposal/plan 阶段省略对应字段，不得用其他阶段冒充。上下文材料标签（例如 '
            'execution.job_files）不是磁盘路径；逐作业证据按其中 source 字段引用，不能猜哈希或目录。'
            '这些是当前 run 的引用路径，不是代码仓路径，不能交给 code.repo_reader 读取。'
            '读取作业文本时使用 execution.job_files 中对应 source 的 reader（context.read_material + material_ref）；'
            '读取代码仓源码时才使用 code.repo_reader。工具对错误目录的读取失败不证明真实证据文件缺失。'}


def report_source_errors(root: Path, text: str) -> list[str]:
    refs = parse(text).metadata.get("chain_refs", {})
    errors: list[str] = []
    names = [(key, refs[key]) for key in ("proposal", "plan", "code") if refs.get(key)]
    names.extend((f"runs/{index}", name) for index, name in enumerate(refs.get("runs", [])))
    for key, name in names:
        try:
            safe_scope_path(root, name, must_exist=True)
        except (ValueError, OSError, TypeError) as exc:
            errors.append(f"/chain_refs/{key}: {name!r} is not an available file in this run: {exc}. "
                          "Use the exact admitted source reference, preserving its full filename.")
    for name in ("execution/metrics.json", "execution/batch_summary.json"):
        try:
            exists = safe_scope_path(root, name).is_file()
        except (ValueError, OSError):
            exists = False
        if exists and name not in refs.get("runs", []):
            errors.append(f"/chain_refs/runs: include actual measured source {name}; an approved plan cannot replace it")
    return errors
