"""Pure identity/budget/failure contracts; no provider or download substitutes."""
from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.harness.tools.search.source_fetch import (
    SourceFetchError,
    attempt_policy,
    failure_details,
    resource_aliases,
    resource_key,
    fetch_sources_tool,
)
from app.harness.tools.registry import ToolContext
from app.settings import Settings


@pytest.mark.parametrize("url", [
    "https://arxiv.org/abs/2505.03042v1",
    "https://arxiv.org/pdf/2505.03042v1",
    "https://arxiv.org/pdf/2505.03042v1.pdf",
    "https://export.arxiv.org/pdf/2505.03042v1.pdf?download=1#page=3",
    "https://www.arxiv.org/pdf/2505.03042v1",
])
def test_link_aliases_keep_the_explicit_paper_version(url: str) -> None:
    assert resource_key(url) == "arxiv:2505.03042:v1"


def test_versions_latest_and_generic_query_selectors_remain_distinct() -> None:
    urls = ["https://arxiv.org/pdf/2505.03042" + version for version in ("", "v1", "v2")]
    assert len({resource_key(url) for url in urls}) == 3
    assert resource_key("https://arxiv.org/pdf/hep-th/9901001v2.pdf") == "arxiv:hep-th/9901001:v2"
    assert resource_key("https://example.org/p?id=1") != resource_key("https://example.org/p?id=2")
    assert resource_key("https://EXAMPLE.org:443/p?id=1#page=2") == resource_key("https://example.org/p?id=1")


def test_only_an_observed_version_resolution_can_link_latest_to_version() -> None:
    latest = "https://arxiv.org/pdf/2505.03042"
    v1 = latest + "v1"
    assert resource_aliases(latest, v1) == {"arxiv:2505.03042:latest", "arxiv:2505.03042:v1"}
    assert resource_aliases(v1, latest) == {"arxiv:2505.03042:v1"}
    with pytest.raises(SourceFetchError, match="explicit version"):
        resource_aliases(v1, latest + "v2")
    with pytest.raises(SourceFetchError, match="paper"):
        resource_aliases(v1, "https://arxiv.org/pdf/2204.13983v1")


def attempt(*, code: str = "read_timeout", retryable: bool = True) -> dict[str, Any]:
    # A declaration for the pure retry-ledger function, not an executed tool
    # result, provider response, PDF receipt, or successful download fixture.
    return {"network_download_attempted": True, "download_url": "https://arxiv.org/pdf/2505.03042v1.pdf",
            "error_code": code, "retryable": retryable, "source_max_mib": 32}


def test_attempt_cap_covers_aliases_and_ignores_nonattempts() -> None:
    alias = "https://export.arxiv.org/pdf/2505.03042v1"
    history = [attempt(), {"network_download_attempted": False, "download_url": alias}]
    assert attempt_policy(history, alias, max_attempts=2, max_mib=64) == (1, None, "read_timeout")
    history.append(attempt())
    assert attempt_policy(history, alias, max_attempts=2, max_mib=64)[1] == "resource attempt limit reached"
    assert attempt_policy(history, alias + "0", max_attempts=2, max_mib=64) == (0, None, None)


def test_known_redirect_identity_also_shares_attempt_cap() -> None:
    previous = attempt()
    previous.update(download_url="https://arxiv.org/pdf/2505.03042", final_url="https://arxiv.org/pdf/2505.03042v1")
    assert attempt_policy([previous], "https://export.arxiv.org/pdf/2505.03042v1.pdf", max_attempts=1, max_mib=64)[1]


def test_permanent_failure_blocks_retry_and_size_increase_remains_bounded() -> None:
    url = "https://arxiv.org/pdf/2505.03042v1"
    assert attempt_policy([attempt(code="http_404", retryable=False)], url, max_attempts=2, max_mib=64)[1]
    oversized = attempt(code="size_limit", retryable=False)
    assert attempt_policy([oversized], url, max_attempts=2, max_mib=32)[1]
    assert attempt_policy([oversized], url, max_attempts=2, max_mib=64) == (1, None, "size_limit")
    assert attempt_policy([oversized], url, max_attempts=1, max_mib=64)[1]


@pytest.mark.parametrize(("exc", "expected"), [
    (httpx.ConnectTimeout("connection stalled"), ("connect_timeout", True)),
    (httpx.ReadTimeout("no response data"), ("read_timeout", True)),
    (httpx.RemoteProtocolError("incomplete response"), ("network_error", True)),
    (SourceFetchError("http_404", "missing version"), ("http_404", False)),
    (SourceFetchError("download_timeout", "deadline", retryable=True), ("download_timeout", True)),
])
def test_error_classification_is_not_a_success_substitute(exc: Exception, expected: tuple[str, bool]) -> None:
    assert failure_details(exc, stage="download") == expected


def test_parse_errors_are_distinct_from_acquisition_errors() -> None:
    assert failure_details(ValueError("page outside range"), stage="extraction") == ("extraction_error", False)
    assert failure_details(ValueError("bad host"), stage="input") == ("invalid_source", False)


@pytest.mark.parametrize(("field", "valid", "invalid"), [
    ("mars_source_connect_timeout_seconds", 15, 61),
    ("mars_source_read_timeout_seconds", 30, 121),
    ("mars_source_download_timeout_seconds", 180, 241),
    ("mars_source_total_timeout_seconds", 240, 301),
    ("mars_source_max_attempts", 2, 6),
])
def test_download_settings_have_finite_hard_bounds(field: str, valid: int, invalid: int) -> None:
    assert getattr(Settings.model_validate({field: valid}), field) == valid
    for value in (0, invalid, float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            Settings.model_validate({field: value})


@pytest.mark.asyncio
async def test_real_pdf_alias_windows_and_corrupt_cache_when_opted_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if os.environ.get("MARS_RUN_SOURCE_FETCH_SMOKE") != "true":
        pytest.skip("actual PDF acquisition requires explicit network opt-in")
    # This public URL failed in the actual 20260908T135405_d8359e run.
    # Only settings are changed; every downloaded byte comes from the real URL.
    monkeypatch.setenv("MARS_ENABLE_NETWORK_TOOLS", "true")
    monkeypatch.setenv("MARS_WEB_SEARCH_ALLOWLIST", "arxiv.org,export.arxiv.org")
    monkeypatch.setenv("MARS_SOURCE_MAX_MIB", "64")
    monkeypatch.setenv("MARS_SOURCE_MAX_ATTEMPTS", "2")
    source = {"title": "A New Perspective To Understanding Multi-resolution Hash Encoding For Neural Fields",
              "url": "https://arxiv.org/abs/2505.03042v1",
              "pdf_url": "https://arxiv.org/pdf/2505.03042v1.pdf"}
    ctx = ToolContext("actual-source-reliability", "pimc", "idea_research", extra={"run_root": str(tmp_path)})
    concurrent = await asyncio.gather(*(fetch_sources_tool({"sources": [source], "max_pages": 1}, ctx)
                                       for _ in range(2)))
    assert all(result.ok for result in concurrent), [result.output for result in concurrent]
    assert sum(result.output["sources"][0]["network_download_attempted"] for result in concurrent) == 1
    first = next(result for result in concurrent if result.output["sources"][0]["network_download_attempted"])
    original = first.output["sources"][0]
    path = Path(original["download_path"])
    assert original["download_complete"] and original["archive_complete"]
    assert original["download_bytes_received"] == path.stat().st_size
    alias = {**source, "pdf_url": "https://export.arxiv.org/pdf/2505.03042v1"}
    invalid_window = await fetch_sources_tool({"sources": [alias], "start_page": 999999}, ctx)
    invalid = invalid_window.output["sources"][0]
    assert not invalid_window.ok and invalid["error_code"] == "extraction_error"
    assert invalid["archive_complete"] and not invalid["network_download_attempted"]
    assert not invalid.get("read_receipt")
    second = await fetch_sources_tool({"sources": [alias], "start_page": 2, "max_pages": 1}, ctx)
    assert second.ok, second.output
    reused = second.output["sources"][0]
    assert reused["reused"] and not reused["network_download_attempted"]
    assert reused["visible_pages"][0]["page"] == 2
    assert reused["sha256"] == original["sha256"]
    assert reused["read_receipt"] != original["read_receipt"]
    # Corrupt this test's own real downloaded file: hash mismatch must trigger
    # a real new acquisition, never reuse unverified content as a read receipt.
    with path.open("ab") as stream:
        stream.write(b"corruption for cache rejection test")
    restored = await fetch_sources_tool({"sources": [source], "max_pages": 1}, ctx)
    assert restored.ok, restored.output
    repaired = restored.output["sources"][0]
    assert repaired["network_download_attempted"] and not repaired["reused"]
    assert repaired["attempt_count"] == 2
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original["sha256"] == repaired["sha256"]
