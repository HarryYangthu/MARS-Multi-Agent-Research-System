"""Parse markdown documents with YAML frontmatter.

Wraps `python-frontmatter` so the rest of the codebase only sees a typed
`ParsedDoc`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import frontmatter


@dataclass(frozen=True)
class ParsedDoc:
    metadata: dict[str, Any]
    body: str


class FrontmatterError(ValueError):
    """Raised when the document cannot be parsed."""


def parse(text: str) -> ParsedDoc:
    """Parse a markdown string with YAML frontmatter.

    Raises FrontmatterError on syntactic failure.
    """
    try:
        post = frontmatter.loads(text)
    except Exception as exc:  # frontmatter raises various errors
        raise FrontmatterError(f"failed to parse frontmatter: {exc}") from exc
    metadata: dict[str, Any] = dict(post.metadata) if post.metadata else {}
    return ParsedDoc(metadata=metadata, body=post.content)


def dumps(metadata: dict[str, Any], body: str) -> str:
    """Serialize back to a single markdown string with YAML frontmatter."""
    post = frontmatter.Post(body, **metadata)
    rendered: Any = frontmatter.dumps(post)
    if isinstance(rendered, bytes):
        return rendered.decode("utf-8")
    return str(rendered)
