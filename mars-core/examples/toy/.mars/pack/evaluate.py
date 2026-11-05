"""Evaluation command of the toy project: train once and print MARS's result JSON (last line)."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--data", required=True)
parser.add_argument("--steps", type=int, default=0)
parser.add_argument("--seed", type=int, default=0)
parser.add_argument("--output", required=True)
parser.add_argument("--overrides", required=True)
args = parser.parse_args()
overrides = json.loads(Path(args.overrides).read_text(encoding="utf-8"))
command = [sys.executable, "train.py", "--data", args.data, "--steps", str(args.steps), "--seed", str(args.seed)]
if "learning_rate" in overrides:
    command += ["--learning-rate", str(overrides["learning_rate"])]
done = subprocess.run(command, capture_output=True, text=True)
if done.returncode:
    print(json.dumps({"ok": False, "error": done.stderr[-1000:]}))
    sys.exit(0)
result = json.loads(done.stdout.strip().splitlines()[-1])
Path(args.output).mkdir(parents=True, exist_ok=True)
Path(args.output, "result.json").write_text(json.dumps(result), encoding="utf-8")
print(json.dumps({"ok": True, "metrics": {"val_mse": result["val_mse"], "params": result["params"]}, "curve": result["curve"]}))
