"""Shared approved paper-training configuration resolution and overrides."""
from __future__ import annotations
import ast
from pathlib import Path
from typing import Any

def approved_config_path(config: dict[str, Any], policy: dict[str, Any], root: Path) -> Path:
    """Use this job's approved file, never silently substitute a global template."""
    raw = config.get("config_path")
    if not isinstance(raw, str) or not raw:
        raise ValueError("编码交付缺少本组实验 config_path；请绑定已批准配置文件")
    path = _resolve_path(raw, root)
    if not path.is_relative_to(root.resolve()) or any(part.startswith(".") for part in Path(raw).parts):
        raise ValueError("实验配置文件必须位于绑定代码目录中")
    if config.get("entrypoint") != "train_static.py":
        raise ValueError("paper_static 的 entrypoint 只能填写 train_static.py（纯脚本路径，不含 python 或命令参数）；"
                         "配置路径放在 config_path，训练预算放在 budget_steps 或 budget_unit/max_iters")
    from app.harness.schema.experiment_contract import budget
    unit, _ = budget(config)
    if unit == "steps":
        tree = ast.parse((root / "train_static.py").read_text())
        supported = any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument" and any(isinstance(arg, ast.Constant)
            and arg.value == "--max-steps" for arg in node.args) for node in ast.walk(tree))
        if not supported:
            raise ValueError("交付入口尚不支持 --max-steps，不能将训练步数换成轮数")
    return path


def _resolve_path(raw: str, base: Path) -> Path:
    if not raw:
        raise ValueError("paper_static path is empty")
    expanded = Path(raw).expanduser()
    return expanded.resolve() if expanded.is_absolute() else (base / expanded).resolve()


def override_args(config: dict[str, Any], cfg: dict[str, Any]) -> list[str]:
    allowed = cfg.get("allowed_overrides", [])
    if not isinstance(allowed, list):
        allowed = []
    pairs: list[tuple[str, Any]] = []
    for key in allowed:
        key_s = str(key)
        if key_s in config:
            pairs.append((key_s, config[key_s]))
    for source, target in {
        "learning_rate": "lr_init",
        "lr": "lr_init",
        "lut_n_spline": "model.lut_n_spline",
        "lut_rmax": "model.lut_rmax",
        "lut_init": "model.lut_init",
    }.items():
        if source in config:
            pairs.append((target, config[source]))
    args: list[str] = []
    for key, value in pairs:
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, int | float | str):
            rendered = str(value)
        else:
            continue
        args.extend(["--set", f"{key}={rendered}"])
    return args
