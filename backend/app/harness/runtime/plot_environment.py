"""Prepare the optional plot cache before requests establish environment identities."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile

from loguru import logger


def initialize_plot_environment() -> bool:
    """Prepare the renderer's existing cache path without importing Matplotlib.

    Failure is diagnostic only: the application may start, but later plotting
    and a stable plotting environment have not been established.
    """
    configured = os.environ.get("MPLCONFIGDIR")
    try:
        directory = Path(configured) if configured else Path(tempfile.gettempdir()) / "mars-matplotlib"
        directory.expanduser().mkdir(parents=True, exist_ok=True)
    except (OSError, RuntimeError) as exc:
        logger.warning("MARS 绘图缓存目录未就绪，服务继续启动；后续绘图及环境稳定性尚未保证：{}", exc)
        return False
    if not configured:
        os.environ["MPLCONFIGDIR"] = str(directory)
    return True
