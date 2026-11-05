"""Where mars-core lives and where this app keeps its own files."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    core_root: Path
    data_dir: Path
    deploy_root: Path = APP_ROOT.parent / "mars-deploy"  # intranet packaging line; provides checks/check_models.py

    @property
    def runs_dir(self) -> Path:
        return self.core_root / "runs"

    @property
    def cli(self) -> Path:
        return self.core_root / "bin/mars"

    @classmethod
    def from_env(cls) -> "Settings":
        core = Path(os.environ.get("MARS_CORE_ROOT", APP_ROOT.parent / "mars-core")).resolve()
        data = Path(os.environ.get("MARS_APP_DATA", APP_ROOT / "data")).resolve()
        deploy = Path(os.environ.get("MARS_DEPLOY_ROOT", APP_ROOT.parent / "mars-deploy")).resolve()
        return cls(core, data, deploy)
