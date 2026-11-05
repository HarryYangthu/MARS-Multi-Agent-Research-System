"""Fit the model's features to (x, y) points by gradient descent and report validation error as JSON."""
import argparse
import csv
import json
import random

from model import features


def load(path):
    with open(path, encoding="utf-8") as handle:
        return [(float(r["x"]), float(r["y"])) for r in csv.DictReader(handle)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--steps", type=int, default=0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--learning-rate", type=float, default=0.05)
    args = parser.parse_args()
    points = load(args.data)
    train, valid = points[::2], points[1::2]
    rows = [(features(x), y) for x, y in train]
    width = len(rows[0][0])
    rng = random.Random(args.seed)
    weights = [rng.uniform(-0.1, 0.1) for _ in range(width)]
    steps = args.steps or 2000
    curve = []
    for step in range(1, steps + 1):
        grad = [0.0] * width
        for f, y in rows:
            error = sum(w * v for w, v in zip(weights, f)) - y
            for i, v in enumerate(f):
                grad[i] += 2 * error * v / len(rows)
        weights = [w - args.learning_rate * g for w, g in zip(weights, grad)]
        if step % max(1, steps // 10) == 0:
            mse = sum((sum(w * v for w, v in zip(weights, features(x))) - y) ** 2 for x, y in valid) / len(valid)
            curve.append([step, round(mse, 6)])
    mse = sum((sum(w * v for w, v in zip(weights, features(x))) - y) ** 2 for x, y in valid) / len(valid)
    print(json.dumps({"val_mse": mse, "params": width, "curve": curve}))


if __name__ == "__main__":
    main()
