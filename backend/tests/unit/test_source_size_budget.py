"""Real settings and pure byte budget checks; no download substitutions."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.harness.tools.search.source_fetch import enforce_source_size
from app.settings import Settings


def test_default_and_configured_size_budgets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARS_SOURCE_MAX_MIB", raising=False)
    assert Settings().mars_source_max_mib == 12
    monkeypatch.setenv("MARS_SOURCE_MAX_MIB", "32")
    assert Settings().mars_source_max_mib == 32


@pytest.mark.parametrize("value", ["0", "65", "unbounded", "1.5"])
def test_settings_reject_outside_hard_budget(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARS_SOURCE_MAX_MIB", value)
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize("mib", [1, 12, 32, 64])
def test_exact_limit_allowed_but_next_byte_refused(mib: int) -> None:
    enforce_source_size(mib * 1024 * 1024, mib)
    with pytest.raises(ValueError, match=f"configured {mib} MiB limit"):
        enforce_source_size(mib * 1024 * 1024 + 1, mib)


@pytest.mark.parametrize("mib", [0, 65, True])
def test_helper_retains_hard_budget(mib: int) -> None:
    with pytest.raises(ValueError, match="1..64"):
        enforce_source_size(1, mib)
