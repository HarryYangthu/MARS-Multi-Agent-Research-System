"""Weights-only PTH reader with explicit NumPy numeric reconstruction support."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np


def read_tensor_archive(path: Path) -> Any:
    import torch
    from numpy._core.multiarray import _reconstruct
    allowed: list[Any] = [(_reconstruct, 'numpy.core.multiarray._reconstruct'), _reconstruct, np.ndarray, np.dtype]
    allowed.extend(type(np.dtype(kind)) for kind in ('float16', 'float32', 'float64', 'complex64', 'complex128', 'int8', 'int16', 'int32', 'int64', 'uint8', 'uint16', 'uint32', 'uint64', 'bool'))
    with torch.serialization.safe_globals(allowed):
        return torch.load(path, map_location='cpu', weights_only=True)
