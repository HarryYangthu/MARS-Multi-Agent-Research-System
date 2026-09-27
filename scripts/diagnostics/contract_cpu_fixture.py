"""Trusted synthetic regression command for the contract integration diagnostic.

The generated candidate is restricted to one bounded arithmetic feature map.
This is a test workload, not an execution sandbox for arbitrary research code.
"""
from __future__ import annotations

import ast
import csv
import json
import math
import os
from pathlib import Path
import random
from typing import Callable, cast


def validate_features(source: str) -> None:
    """Allow a small side-effect-free Python function before compiling it."""
    if len(source.encode()) > 4096:
        raise ValueError("Candidate source exceeds the diagnostic limit")
    tree = ast.parse(source)
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        raise ValueError("Candidate must contain exactly one features function")
    function = tree.body[0]
    args = function.args
    if (function.name != "features" or function.decorator_list or function.returns
            or function.type_comment or getattr(function, "type_params", []) or args.posonlyargs
            or len(args.args) != 1 or args.args[0].arg != "x" or args.args[0].annotation
            or args.vararg or args.kwarg or args.kwonlyargs or args.defaults or args.kw_defaults
            or len(function.body) != 1 or not isinstance(function.body[0], ast.Return)):
        raise ValueError("Candidate requires unannotated features(x) with one return")
    expression = function.body[0].value
    if not isinstance(expression, ast.Tuple) or not 1 <= len(expression.elts) <= 4:
        raise ValueError("Feature return must be a tuple with 1-4 elements")
    if sum(1 for _ in ast.walk(tree)) > 100:
        raise ValueError("Candidate expression is too large")

    def check(node: ast.expr, depth: int = 0) -> None:
        if depth > 16:
            raise ValueError("Candidate expression is too deep")
        if isinstance(node, ast.Name) and node.id == "x":
            return
        if isinstance(node, ast.Constant) and type(node.value) in {float, int}:
            value = cast(float | int, node.value)
            if math.isfinite(value) and abs(value) <= 1_000_000:
                return
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            check(node.operand, depth + 1)
            return
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Pow)):
            if isinstance(node.op, ast.Pow) and (not isinstance(node.right, ast.Constant)
                    or type(node.right.value) is not int or not 0 <= node.right.value <= 8):
                raise ValueError("Only constant integer powers from 0 to 8 are admitted")
            check(node.left, depth + 1)
            check(node.right, depth + 1)
            return
        raise ValueError("Candidate contains unsupported executable syntax")

    for element in expression.elts:
        check(element)


def load_features(path: Path) -> Callable[[float], tuple[float, ...]]:
    source = path.read_text(encoding="utf-8")
    validate_features(source)
    # Execute the exact bytes validated above; no calls, imports,
    # attributes, defaults, decorators or additional statements are possible.
    namespace: dict[str, object] = {"__builtins__": {}}
    exec(compile(source, str(path), "exec"), namespace)
    return cast(Callable[[float], tuple[float, ...]], namespace["features"])


def solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """Small normal-equation solve with pivoting and no third-party dependency."""
    rows = [list(values) + [target] for values, target in zip(matrix, vector, strict=True)]
    size = len(rows)
    for column in range(size):
        pivot = max(range(column, size), key=lambda index: abs(rows[index][column]))
        rows[column], rows[pivot] = rows[pivot], rows[column]
        scale = rows[column][column]
        if abs(scale) < 1e-12:
            raise ValueError("Feature design is singular")
        rows[column] = [value / scale for value in rows[column]]
        for index in range(size):
            if index != column:
                factor = rows[index][column]
                rows[index] = [left - factor * right for left, right in zip(rows[index], rows[column], strict=True)]
    return [row[-1] for row in rows]


def measure(source: Path, seed: int) -> tuple[dict[str, float], list[dict[str, float]], list[float]]:
    features = load_features(source)
    rng = random.Random(seed)
    training = [(rng.uniform(-1, 1), rng.gauss(0, 0.04)) for _ in range(64)]
    held_out = [(rng.uniform(-1, 1), rng.gauss(0, 0.04)) for _ in range(64)]
    def target(x: float, noise: float) -> float:
        return 0.4 + 0.6 * x + 1.3 * x * x + noise
    design = [features(x) for x, _ in training]
    labels = [target(x, noise) for x, noise in training]
    width = len(design[0])
    if any(len(row) != width or any(not math.isfinite(value) for value in row) for row in design):
        raise ValueError("Invalid generated feature values")
    gram = [[sum(row[i] * row[j] for row in design) for j in range(width)] for i in range(width)]
    rhs = [sum(row[i] * value for row, value in zip(design, labels, strict=True)) for i in range(width)]
    weights = solve(gram, rhs)
    measurements = []
    for x, noise in held_out:
        predicted = sum(weight * value for weight, value in zip(weights, features(x), strict=True))
        expected = target(x, noise)
        measurements.append({"x": x, "target": expected, "prediction": predicted, "squared_error": (predicted - expected) ** 2})
    return {"mse": sum(row["squared_error"] for row in measurements) / len(measurements)}, measurements, weights


def main() -> None:
    request = json.loads(Path(os.environ["MARS_JOB_REQUEST"]).read_text())
    arm = request["config"]["arm"]
    if arm not in {"baseline", "candidate"} or type(request["seed"]) is not int or request["steps"] != 1:
        raise ValueError("The diagnostic requires a named arm, integer seed and one fit")
    metrics, observations, weights = measure(Path(arm + ".py"), request["seed"])
    output = Path(request["output_dir"])
    with (output / "measurements.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(observations[0]))
        writer.writeheader()
        writer.writerows(observations)
    (output / "fit.json").write_text(json.dumps({"arm": arm, "seed": request["seed"], "weights": weights,
        "training_examples": 64, "held_out_examples": 64, "solver": "normal_equations_partial_pivoting", "fits": 1}))
    result = {"schema": "local_command_result.v1", "invocation_id": request["invocation_id"],
        "run_id": request["run_id"], "experiment_id": request["experiment_id"], "status": "completed",
        "metrics": metrics, "evidence_paths": ["measurements.csv", "fit.json"]}
    Path(os.environ["MARS_RESULT_PATH"]).write_text(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
