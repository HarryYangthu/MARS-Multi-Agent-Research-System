"""One GPU budget for everything the app trains itself (hyperparameter search, simulation verification).

The core runs at most 6 remote jobs and its evolution routes need most of them, so the app keeps to MAX_PARALLEL.
"""
from __future__ import annotations

import threading

MAX_PARALLEL = 2
SLOTS = threading.BoundedSemaphore(MAX_PARALLEL)
