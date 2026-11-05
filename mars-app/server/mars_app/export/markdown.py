"""Markdown → blocks for the Office writers (taken from MARS-Product reporting/generators.py)."""
from __future__ import annotations

from typing import Any

import mistune


def blocks(markdown: str) -> list[dict[str, Any]]:
    result = mistune.create_markdown(renderer="ast", plugins=["table"])(markdown or "")
    return result if isinstance(result, list) else []


def plain(token: dict[str, Any]) -> str:
    if token.get("type") in {"softbreak", "linebreak"}:
        return "\n"
    value = str(token.get("raw", "")) + "".join(plain(child) for child in token.get("children", []))
    if token.get("type") in {"link", "image"}:
        return value + f" ({token.get('attrs', {}).get('url', '')})"
    return value


def table_rows(token: dict[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    for section in token.get("children", []):
        if section.get("type") == "table_head":
            rows.append([plain(cell) for cell in section.get("children", [])])
        else:
            rows.extend([[plain(cell) for cell in row.get("children", [])] for row in section.get("children", [])])
    return rows
