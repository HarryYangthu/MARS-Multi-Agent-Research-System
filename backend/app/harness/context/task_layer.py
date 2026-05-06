"""Task layer of the 3-layer context.

Holds the user's request, KB top-k retrievals, and upstream Agent handoffs.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TaskLayer:
    user_request: str
    kb_excerpts: list[str] = field(default_factory=list)
    upstream_handoff: dict[str, str] = field(default_factory=dict)
    recent_dialog: list[str] = field(default_factory=list)

    def render(self) -> str:
        parts: list[str] = []
        if self.user_request:
            parts.append(f"## User request\n{self.user_request}")
        if self.kb_excerpts:
            parts.append(
                "## KB excerpts\n"
                + "\n\n".join(f"- {e[:1500]}" for e in self.kb_excerpts)
            )
        for label, content in self.upstream_handoff.items():
            parts.append(f"## Upstream: {label}\n{content[:4000]}")
        if self.recent_dialog:
            parts.append("## Recent dialog\n" + "\n\n".join(self.recent_dialog))
        return "\n\n".join(parts)
