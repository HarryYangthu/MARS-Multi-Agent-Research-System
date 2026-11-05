"""Check of the toy project: the feature map builds, returns finite numbers, and its size is reported."""
import json
import math
import sys

sys.path.insert(0, ".")
try:
    from model import features
    values = [features(x / 10) for x in range(-10, 11)]
    widths = {len(v) for v in values}
    ok = len(widths) == 1 and all(math.isfinite(f) for v in values for f in v)
    print(json.dumps({"ok": ok, "params": widths.pop() if len(widths) == 1 else None,
                      "message": "" if ok else "features() must return equally long lists of finite numbers"}))
except Exception as error:  # noqa: BLE001 - reported to the coding agent
    print(json.dumps({"ok": False, "error": f"{type(error).__name__}: {error}"}))
