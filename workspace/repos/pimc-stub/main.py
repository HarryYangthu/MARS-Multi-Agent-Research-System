"""Stub training driver."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def main() -> None:
    from libs.Model import Paper_Router_v2, Paper_Total_0327

    baseline = Paper_Total_0327()
    candidate = Paper_Router_v2()
    print("baseline", baseline.expert_count, "candidate", candidate.expert_count)


if __name__ == "__main__":
    main()
